"""Admin endpoints for the new 8-ML stack (Phase 0–5a).

All routes require admin role (re-uses ``_require_admin`` from
:mod:`routes.admin`). Read-only — no broker calls, no order placement.

Routes:
  * GET  /api/admin/ml/v2/boot-receipts
  * GET  /api/admin/ml/v2/decisions/summary
  * GET  /api/admin/ml/v2/decisions/recent
  * POST /api/admin/ml/v2/pipeline/decide   (synthetic dry-run)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from routes.admin import _require_admin
from services import alpha_decision_log
from services.ml import boot_receipts
from services.ml.contracts import FeatureFrame
from services.ml.pipeline import get_pipeline
from services.ml.roadguard import (
    AccountSnapshot,
    RoadGuardV2,
    TradeIntent,
)

router = APIRouter(prefix="/api/admin/ml/v2", tags=["admin", "ml-v2"])
logger = logging.getLogger(__name__)


_db = None


def set_db(database) -> None:
    global _db
    _db = database


# ── Boot receipts ────────────────────────────────────────────────


@router.get("/boot-receipts")
async def get_boot_receipts(request: Request) -> Dict[str, Any]:
    await _require_admin(request)
    # Lazy-init the pipeline so receipts are registered if the process
    # hasn't run a decision yet.
    get_pipeline()
    receipts = [r.as_dict() for r in boot_receipts.get_all_receipts()]
    return {
        "count": len(receipts),
        "ready": sum(1 for r in receipts if r.get("ready")),
        "disabled": sum(1 for r in receipts if not r.get("ready")),
        "receipts": receipts,
    }


# ── Decision log ─────────────────────────────────────────────────


@router.get("/decisions/summary")
async def get_decision_summary(
    request: Request,
    days: int = Query(7, ge=1, le=30),
) -> Dict[str, Any]:
    await _require_admin(request)
    return await alpha_decision_log.summary(_db, days=days)


@router.get("/decisions/recent")
async def get_recent_decisions(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    blocked_at: Optional[str] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    await _require_admin(request)
    if _db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if blocked_at:
        q["blocked_at"] = blocked_at
    if symbol:
        q["symbol"] = symbol.upper()
    try:
        cursor = _db[alpha_decision_log.COLLECTION].find(
            q, projection={"_id": 0},
        ).sort("created_at", -1).limit(limit)
        items: List[Dict[str, Any]] = []
        async for doc in cursor:
            # serialise datetimes
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
        return {"items": items, "count": len(items)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ml.v2.admin] decisions/recent failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── RoadGuard pair (lane-isolated) ───────────────────────────────


@router.get("/roadguard/pair-status")
async def roadguard_pair_status(request: Request) -> Dict[str, Any]:
    """Surface the closed-loop RG pair: per-lane decision counts +
    last verdict per lane. Lets the operator audit equity vs crypto
    promotion-readiness independently."""
    await _require_admin(request)
    from services.ml.roadguard import CryptoRoadGuard, EquityRoadGuard

    out: Dict[str, Any] = {"pair": {}}
    for cls in (EquityRoadGuard, CryptoRoadGuard):
        coll_name = cls.DECISIONS_COLLECTION
        entry: Dict[str, Any] = {
            "lane": cls.LANE,
            "collection": coll_name,
            "total": 0,
            "by_decision": {"PASS": 0, "REDUCE": 0, "BLOCK": 0},
            "last_verdict": None,
        }
        if _db is not None:
            try:
                cursor = _db[coll_name].aggregate([
                    {"$group": {"_id": "$verdict.decision", "n": {"$sum": 1}}},
                ])
                async for row in cursor:
                    decision = row.get("_id")
                    if decision in entry["by_decision"]:
                        entry["by_decision"][decision] = int(row.get("n") or 0)
                    entry["total"] += int(row.get("n") or 0)
                last = await _db[coll_name].find_one(
                    {}, projection={"_id": 0}, sort=[("created_at", -1)],
                )
                if last is not None:
                    ca = last.get("created_at")
                    if hasattr(ca, "isoformat"):
                        last["created_at"] = ca.isoformat()
                    entry["last_verdict"] = last
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
        out["pair"][cls.LANE] = entry
    return out


@router.get("/roadguard/decisions/recent")
async def roadguard_recent_decisions(
    request: Request,
    lane: str = Query(..., regex="^(equity|crypto)$"),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    await _require_admin(request)
    from services.ml.roadguard import CryptoRoadGuard, EquityRoadGuard
    coll = (
        EquityRoadGuard.DECISIONS_COLLECTION if lane == "equity"
        else CryptoRoadGuard.DECISIONS_COLLECTION
    )
    if _db is None:
        return {"items": [], "count": 0, "lane": lane, "collection": coll}
    items: List[Dict[str, Any]] = []
    try:
        cursor = _db[coll].find({}, projection={"_id": 0}).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"items": items, "count": len(items), "lane": lane, "collection": coll}


# ── Synthetic dry-run ────────────────────────────────────────────


@router.post("/pipeline/decide")
async def pipeline_decide(
    request: Request,
    payload: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    """Dry-run the 8-ML pipeline against an operator-supplied frame.

    Body shape:
      {
        "symbol": "AAPL",
        "lane": "equity",
        "market": { ... },                    // any of the 21 fields
        "intent_hint": "BUY"                  // optional, default BUY
      }

    NEVER places an order. NEVER calls a broker. Optionally writes a
    receipt to alpha_decision_log if ?record=true is passed.
    """
    await _require_admin(request)

    symbol = str(payload.get("symbol") or "TEST").upper()
    lane = str(payload.get("lane") or "equity").lower()
    if lane not in ("equity", "crypto"):
        raise HTTPException(status_code=400, detail="lane must be equity|crypto")

    frame = FeatureFrame(
        symbol=symbol,
        lane=lane,
        timestamp=datetime.now(timezone.utc).isoformat(),
        market=dict(payload.get("market") or {}),
        extra={"intent_hint": str(payload.get("intent_hint") or "BUY").upper()},
    )
    decision = get_pipeline().decide(frame)

    rg_verdict = None
    if "roadguard" in payload:
        rg_payload = payload["roadguard"] or {}
        try:
            snapshot = AccountSnapshot(**(rg_payload.get("snapshot") or {}))
            intent = TradeIntent(
                symbol=symbol, lane=lane,
                side=decision.final.decision,
                requested_notional_usd=float(rg_payload.get("requested_notional_usd", 100.0)),
                will_hit_live_broker=False,
            )
            rg_verdict = RoadGuardV2().evaluate(intent, snapshot)
        except Exception as exc:  # noqa: BLE001
            return {
                "pipeline": decision.as_dict(),
                "roadguard_error": f"{type(exc).__name__}:{exc}",
            }

    record = bool(request.query_params.get("record"))
    if record and _db is not None:
        await alpha_decision_log.record_pipeline_decision(_db, decision)

    return {
        "pipeline": decision.as_dict(),
        "roadguard_v2": rg_verdict.as_dict() if rg_verdict else None,
        "recorded": record,
    }
