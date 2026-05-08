"""Phase 5d/6 read-only safety surface — heartbeat, pipeline
receipts, promotion checklist, artifact inventory, wedge alerter.

All endpoints under ``/api/admin/ml`` (no /v2). Strict read-only:
no broker calls, no env mutation, no joblib loading, no promotion
actions.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Query, Request

from services import alpha_decision_log
from services.ml import boot_receipts
from services.ml.pipeline import get_pipeline

from ._auth import require_admin
from ._routers import get_db, ml_safety_router

logger = logging.getLogger(__name__)


# ── Heartbeat ────────────────────────────────────────────────────


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
    await require_admin(request)
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


# ── Pipeline receipts (alias for /decisions/recent w/ lane filter) ─


@ml_safety_router.get("/pipeline/receipts")
async def pipeline_receipts(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    lane: Optional[str] = Query(None, pattern="^(equity|crypto)$"),
) -> Dict[str, Any]:
    """Most recent pipeline decision-log receipts. Alias for
    /decisions/recent with optional lane filter.
    """
    await require_admin(request)
    db = get_db()
    if db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    items: List[Dict[str, Any]] = []
    try:
        cursor = db[alpha_decision_log.COLLECTION].find(
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


# ── Promotion checklist (read-only Phase 6 readiness aggregator) ─


@ml_safety_router.get("/promotion-checklist")
async def promotion_checklist(request: Request) -> Dict[str, Any]:
    """Read-only Phase 6 readiness aggregator. 8 checks pulled from
    existing surfaces (artifacts inventory + heartbeat + boot
    receipts + alpha_decision_log).

    Hard-rule invariants:
      * Never mutates env, artifacts, or pipeline state.
      * Never loads joblib.
      * Never calls broker/executor/pipeline.
      * May say 'Ready for review'. MUST NOT say 'Promote now'.
    """
    await require_admin(request)
    from services.ml.promotion_checklist import build_checklist
    return await build_checklist(db=get_db())


# ── Artifact inventory (read-only file-stat) ────────────────────


@ml_safety_router.get("/artifacts")
async def list_artifacts_endpoint(request: Request) -> Dict[str, Any]:
    """List every .joblib under data/models/ with metadata.

    Strict read-only: file-stat + env-read only. NEVER calls
    joblib.load, NEVER mutates env, NEVER promotes / restarts /
    calls broker. Missing models dir returns empty list, not 500.
    """
    await require_admin(request)
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
    await require_admin(request)
    from services.wedge_alerter import is_configured
    db = get_db()
    state: Dict[str, Any] = {
        "configured": is_configured(),
        "lane_frozen_started_at": {},
        "alert_history": {},
    }
    if db is not None:
        try:
            doc = await db["wedge_alerter_state"].find_one(
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
    lane: Optional[str] = Query(None, pattern="^(equity|crypto)$"),
) -> Dict[str, Any]:
    """Read-only — last N audit rows from wedge_alerter_history."""
    await require_admin(request)
    db = get_db()
    if db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    items: List[Dict[str, Any]] = []
    try:
        cursor = db["wedge_alerter_history"].find(
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
    await require_admin(request)
    from services.wedge_alerter import run_tick
    return await run_tick(get_db())
