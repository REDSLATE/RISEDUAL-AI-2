"""
Sovereign AI — Admin endpoints.

Read-only observability + manual tick controls. All owner-gated; no authority
mutations are exposed over HTTP (promotion / demotion is fully automatic and
driven by the gate math).
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/sovereign-ai", tags=["sovereign-ai"])

_db_ref: Any = None


def _db() -> Any:
    return _db_ref


def set_db(db: Any) -> None:
    """Wire the shared Mongo handle for sovereign admin endpoints."""
    global _db_ref
    _db_ref = db


async def _require_owner_request(request: Request):
    from routes.admin import _require_owner
    return await _require_owner(request)


@router.get("/status")
async def sovereign_status(request: Request) -> dict[str, Any]:
    """Return per-core promotion status + top-of-queue row counts."""
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    from services.sovereign_promotion_gate import compute_sovereign_promotion_status

    equity = await compute_sovereign_promotion_status(db, "equity")
    crypto = await compute_sovereign_promotion_status(db, "crypto")

    # Recent 10 decisions per core for at-a-glance view
    async def _recent(asset_type: str) -> list[dict[str, Any]]:
        cur = db["sovereign_decisions"].find(
            {"asset_type": asset_type},
            {
                "_id": 0,
                "decision_id": 1,
                "symbol": 1,
                "action": 1,
                "confidence": 1,
                "conviction_tier": 1,
                "size_multiplier": 1,
                "vetoes": 1,
                "shadow": 1,
                "resolved": 1,
                "created_at": 1,
            },
        ).sort("created_at", -1).limit(10)
        return await cur.to_list(length=10)

    return {
        "cores": {
            "equity": equity,
            "crypto": crypto,
        },
        "recent": {
            "equity": await _recent("equity"),
            "crypto": await _recent("crypto"),
        },
    }


@router.post("/decide/{symbol}")
async def sovereign_decide_manual(
    request: Request,
    symbol: str,
    asset_type: Literal["equity", "crypto"] = "equity",
    persist: bool = False,
) -> dict[str, Any]:
    """Manual tick for smoke-testing. Builds minimal features from the last
    known snapshot sources and runs the engine.
    """
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    from dataclasses import asdict
    from services.sovereign_ai_core import (
        SovereignFeatures, sovereign_decide,
    )

    symbol = symbol.upper()

    # Equity: pull latest bars-derived snapshot hints from equity_telemetry
    # + catalyst snapshot. If nothing is available, build a stub so the
    # engine still runs (useful for pure-compute verification).
    catalyst = None
    try:
        catalyst = await db.catalyst_snapshots.find_one(
            {"symbol": symbol}, {"_id": 0},
        )
    except Exception:
        catalyst = None

    features = SovereignFeatures(
        symbol=symbol,
        asset_type=asset_type,
        news_shock_state=(catalyst or {}).get("news_shock", {}).get("state")
            if catalyst else None,
        event_risk=(catalyst or {}).get("event_risk") if catalyst else None,
        news_sentiment=(catalyst or {}).get("news_shock", {}).get("sentiment_score")
            if catalyst else None,
        news_volume_zscore=(catalyst or {}).get("news_shock", {}).get("zscore")
            if catalyst else None,
    )
    decision = await sovereign_decide(
        db, features, shadow=True, persist=bool(persist),
    )
    return {"decision": asdict(decision)}


@router.post("/resolve/{decision_id}")
async def sovereign_resolve_manual(
    request: Request,
    decision_id: str,
    horizon: Literal["60m", "4h", "eod"] = "60m",
    pnl_pct: float = 0.0,
    was_right: bool = False,
) -> dict[str, Any]:
    """Back-patch a decision with its realised outcome (admin smoke endpoint)."""
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    from services.sovereign_ai_core import resolve_sovereign_decision

    ok = await resolve_sovereign_decision(
        db, decision_id, horizon=horizon, pnl_pct=pnl_pct, was_right=was_right,
    )
    return {"ok": ok, "decision_id": decision_id, "horizon": horizon}


@router.post("/ensure-indexes")
async def sovereign_ensure_indexes(request: Request) -> dict[str, Any]:
    """Create the required Mongo indexes on ``sovereign_decisions``."""
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    from services.sovereign_ai_core import ensure_sovereign_indexes

    return await ensure_sovereign_indexes(db)


@router.get("/mode")
async def sovereign_mode(request: Request) -> dict[str, Any]:
    """Return the current core mode (DTD or PRD)."""
    await _require_owner_request(request)
    from services.sovereign_mode_guard import get_core_mode, is_dtd, is_prd
    return {
        "mode": get_core_mode(),
        "is_dtd": is_dtd(),
        "is_prd": is_prd(),
    }


@router.post("/dtd/nightly-retrain")
async def sovereign_dtd_nightly_retrain(request: Request) -> dict[str, Any]:
    """Manually trigger the DTD nightly retrain probe.

    Guarded behind ``require_dtd()`` — returns HTTP 403 when called on a
    core started with ``RISEDUAL_CORE_MODE=PRD``.
    """
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    try:
        from services.sovereign_dtd_adapter import run_dtd_nightly_retrain
        return await run_dtd_nightly_retrain(db)
    except RuntimeError as exc:
        # Mode guard fired — translate to HTTP 403 for clarity
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/narrate/{decision_id}")
async def sovereign_narrate(
    request: Request,
    decision_id: str,
) -> dict[str, Any]:
    """AI-to-AI narrator: 3-bullet plain-English explanation of a sovereign
    decision (cached 24h per decision_id)."""
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.sovereign_narrator import narrate_sovereign_decision
    return await narrate_sovereign_decision(db, decision_id)


@router.post("/resolution/tick")
async def sovereign_resolution_tick(request: Request) -> dict[str, Any]:
    """Manually trigger one resolution tick (useful for smoke tests
    without waiting for the 15-min cron)."""
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.sovereign_resolution_loop import run_resolution_tick
    return await run_resolution_tick(db)


@router.get("/burn-in")
async def sovereign_burn_in(request: Request) -> dict[str, Any]:
    """Burn-in snapshot for the frontend chip: per-core decisions_24h,
    resolved_pct, avg_conviction, contribution_applied_pct, phase.
    """
    await _require_owner_request(request)
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")

    from datetime import datetime, timedelta, timezone
    from services.sovereign_promotion_gate import compute_sovereign_promotion_status

    since = datetime.now(timezone.utc) - timedelta(hours=24)

    async def _core_snapshot(asset_type: str) -> dict[str, Any]:
        base = {"asset_type": asset_type}
        coll = db["sovereign_decisions"]
        decisions_24h = await coll.count_documents(
            {**base, "created_at": {"$gte": since}},
        )
        resolved_24h = await coll.count_documents(
            {**base, "resolved": True, "created_at": {"$gte": since}},
        )
        # Avg conviction over 24h
        pipe = [
            {"$match": {**base, "created_at": {"$gte": since}}},
            {"$group": {
                "_id": None,
                "avg_conf": {"$avg": "$confidence"},
                "avg_cal": {"$avg": "$calibration_score"},
            }},
        ]
        try:
            agg = await coll.aggregate(pipe).to_list(length=1)
            avg_conf = round(float(agg[0]["avg_conf"]), 4) if agg else None
            avg_cal = round(float(agg[0]["avg_cal"]), 4) if agg else None
        except Exception:
            avg_conf = None
            avg_cal = None

        # Contribution applied rate — read trade rows for this asset
        trade_coll = "paper_trades" if asset_type == "equity" else "crypto_paper_trades"
        try:
            trades_24h = await db[trade_coll].count_documents(
                {"opened_at": {"$gte": since}} if asset_type == "equity"
                else {"created_at": {"$gte": since}},
            )
            contrib_applied = await db[trade_coll].count_documents(
                {
                    "sovereign_contribution.applied": True,
                    ("opened_at" if asset_type == "equity" else "created_at"): {"$gte": since},
                },
            )
        except Exception:
            trades_24h = 0
            contrib_applied = 0

        promotion = await compute_sovereign_promotion_status(db, asset_type)  # type: ignore[arg-type]

        return {
            "asset_type": asset_type,
            "decisions_24h": decisions_24h,
            "resolved_24h": resolved_24h,
            "resolved_pct": round(resolved_24h / decisions_24h, 4) if decisions_24h else None,
            "avg_confidence": avg_conf,
            "avg_calibration": avg_cal,
            "trades_24h": trades_24h,
            "contribution_applied_24h": contrib_applied,
            "contribution_applied_pct": round(contrib_applied / trades_24h, 4)
                if trades_24h else None,
            "phase": promotion.get("phase"),
            "promoted": promotion.get("promoted"),
            "rows_to_go": promotion.get("rows_to_go"),
        }

    return {
        "ran_at": datetime.now(timezone.utc),
        "cores": {
            "equity": await _core_snapshot("equity"),
            "crypto": await _core_snapshot("crypto"),
        },
    }
