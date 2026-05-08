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
# Phase 5d safety surface — exposed at the simpler /api/admin/ml
# prefix (no /v2) so operators can curl heartbeat/receipts without
# guessing the version path.
ml_safety_router = APIRouter(prefix="/api/admin/ml", tags=["admin", "ml-safety"])
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


# ── Calibration Kanban (read-only promotion-readiness tile) ──────


@router.get("/calibration/kanban")
async def calibration_kanban(request: Request) -> Dict[str, Any]:
    """Per-lane promotion-readiness snapshot for the admin tile.

    Read-only. NEVER mutates flags. NEVER calls a broker. Buttons on
    the tile only display ``Eligible`` / ``Blocked`` / ``Ready for
    Review`` — they do not flip enforcement.
    """
    await _require_admin(request)
    from services.calibration_kanban import get_kanban
    return await get_kanban(_db)


# ── Phase 5b broker wire — observability ─────────────────────────


@router.get("/phase5b/summary")
async def phase5b_summary(
    request: Request,
    days: int = Query(7, ge=1, le=30),
) -> Dict[str, Any]:
    """Per-lane breakdown of phase5b_intents rows. Reports how many
    signals would have fired vs how many were gate-blocked."""
    await _require_admin(request)
    if _db is None:
        return {"by_lane": {}, "total": 0}
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = {
        "by_lane": {
            "equity": {"total": 0, "by_classification": {}},
            "crypto": {"total": 0, "by_classification": {}},
        },
        "total": 0,
    }
    try:
        cursor = _db["phase5b_intents"].aggregate([
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"lane": "$lane", "classification": "$classification"},
                "n": {"$sum": 1},
            }},
        ])
        async for row in cursor:
            n = int(row.get("n") or 0)
            lane = row["_id"].get("lane")
            cls = row["_id"].get("classification") or "UNKNOWN"
            out["total"] += n
            if lane in out["by_lane"]:
                out["by_lane"][lane]["total"] += n
                out["by_lane"][lane]["by_classification"][cls] = (
                    out["by_lane"][lane]["by_classification"].get(cls, 0) + n
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ml.v2.admin] phase5b/summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return out


@router.get("/phase5b/recent")
async def phase5b_recent(
    request: Request,
    lane: Optional[str] = Query(None, regex="^(equity|crypto)$"),
    classification: Optional[str] = Query(
        None, regex="^(SHADOW_ONLY|GATE_BLOCK|WOULD_HAVE_FIRED|FIRED)$"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Last N phase5b_intents rows for review."""
    await _require_admin(request)
    if _db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    if classification:
        q["classification"] = classification
    items: List[Dict[str, Any]] = []
    try:
        cursor = _db["phase5b_intents"].find(
            q, projection={"_id": 0},
        ).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"items": items, "count": len(items)}


# ── Camaro → Shelly bridge ───────────────────────────────────────


@router.get("/camaro/bridge/status")
async def camaro_bridge_status(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
) -> Dict[str, Any]:
    """Read-only — last N bridge runs."""
    await _require_admin(request)
    from services.ml.camaro_shelly_bridge import get_bridge_status
    return await get_bridge_status(_db, limit=limit)


@router.post("/camaro/bridge/run")
async def camaro_bridge_run(
    request: Request,
    lookback_hours: int = Query(24, ge=1, le=168),
) -> Dict[str, Any]:
    """Trigger a Camaro→Shelly ingestion run. Idempotent — re-runs
    are no-ops on already-ingested ``trade_id``s."""
    await _require_admin(request)
    from services.ml.camaro_shelly_bridge import run_bridge
    return await run_bridge(_db, lookback_hours=lookback_hours)


@ml_safety_router.get("/heartbeat")
async def pipeline_heartbeat(request: Request) -> Dict[str, Any]:
    """Per-lane pipeline heartbeat — surfaces frozen lanes.

    Read-only. Reports rolling 1h signals/holds, last_signal_at,
    last_pipeline_run_at, feature_health_avg, and the current
    executor model age (from the boot receipts). Combines:

      * in-process executor_heartbeat tracker
      * latest boot-receipt model_age_hours per lane

    No broker calls, no DB writes.
    """
    await _require_admin(request)
    from services.ml import executor_heartbeat

    # Ensure pipeline is constructed so receipts exist.
    get_pipeline()
    age_by_lane: Dict[str, float] = {}
    for r in boot_receipts.get_all_receipts():
        if r.layer != "executor" or not r.lane:
            continue
        if r.model_age_hours is not None:
            age_by_lane[r.lane] = r.model_age_hours
    snap = executor_heartbeat.get_snapshot(model_age_by_lane=age_by_lane)
    # Augment with boot-stale flags so the operator can see the cause
    # of an observe-only lane at a glance.
    stale_by_lane: Dict[str, bool] = {}
    for r in boot_receipts.get_all_receipts():
        if r.layer == "executor" and r.lane:
            stale_by_lane[r.lane] = bool(r.stale)
    for lane, info in snap.get("lanes", {}).items():
        info["model_stale"] = stale_by_lane.get(lane, False)
    return snap


# ── Pipeline receipts (alias surfacing live decisions for ops) ───


@ml_safety_router.get("/pipeline/receipts")
async def pipeline_receipts(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    lane: Optional[str] = Query(None, regex="^(equity|crypto)$"),
) -> Dict[str, Any]:
    """Most recent pipeline decision-log receipts. Alias for
    /decisions/recent with optional lane filter.
    """
    await _require_admin(request)
    if _db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    items: List[Dict[str, Any]] = []
    try:
        cursor = _db[alpha_decision_log.COLLECTION].find(
            q, projection={"_id": 0},
        ).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"items": items, "count": len(items)}


# ── Artifact inventory (read-only file-stat) ────────────────────


@ml_safety_router.get("/artifacts")
async def list_artifacts_endpoint(request: Request) -> Dict[str, Any]:
    """List every .joblib under data/models/ with metadata.

    Strict read-only: file-stat + env-read only. NEVER calls
    joblib.load, NEVER mutates env, NEVER promotes / restarts /
    calls broker. Missing models dir returns empty list, not 500.
    """
    await _require_admin(request)
    from services.ml.artifact_inventory import list_artifacts, _models_dir
    items = list_artifacts()
    return {
        "models_dir": str(_models_dir()),
        "items": items,
        "count": len(items),
    }


# ── Wedge alerter (heartbeat-driven, notification-only) ─────────


@ml_safety_router.get("/wedge-alerter/status")
async def wedge_alerter_status(request: Request) -> Dict[str, Any]:
    """Read-only — returns config flag + persisted state shape."""
    await _require_admin(request)
    from services.wedge_alerter import is_configured
    state: Dict[str, Any] = {
        "configured": is_configured(),
        "lane_frozen_started_at": {},
        "alert_history": {},
    }
    if _db is not None:
        try:
            doc = await _db["wedge_alerter_state"].find_one(
                {"_id": "state"}, {"_id": 0},
            )
            if doc:
                ua = doc.get("updated_at")
                if hasattr(ua, "isoformat"):
                    doc["updated_at"] = ua.isoformat()
                state.update({
                    "lane_frozen_started_at": doc.get("lane_frozen_started_at") or {},
                    "alert_history": doc.get("alert_history") or {},
                    "updated_at": doc.get("updated_at"),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wedge-alerter] status read failed: %s", exc)
    return state


@ml_safety_router.get("/wedge-alerter/history")
async def wedge_alerter_history(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    lane: Optional[str] = Query(None, regex="^(equity|crypto)$"),
) -> Dict[str, Any]:
    """Read-only — last N audit rows from wedge_alerter_history."""
    await _require_admin(request)
    if _db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    items: List[Dict[str, Any]] = []
    try:
        cursor = _db["wedge_alerter_history"].find(
            q, projection={"_id": 0},
        ).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"items": items, "count": len(items)}


@ml_safety_router.post("/wedge-alerter/run-now")
async def wedge_alerter_run_now(request: Request) -> Dict[str, Any]:
    """Manual trigger for one tick. Same notification-only contract."""
    await _require_admin(request)
    from services.wedge_alerter import run_tick
    return await run_tick(_db)


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
