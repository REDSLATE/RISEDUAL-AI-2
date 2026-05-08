"""Receipts + decision-log + Phase 5b observability + Camaro bridge.

All endpoints under ``/api/admin/ml/v2``. Read-only — no broker
calls, no order placement.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Query, Request

from services import alpha_decision_log
from services.ml import boot_receipts
from services.ml.pipeline import get_pipeline

from ._auth import require_admin
from ._routers import get_db, router

logger = logging.getLogger(__name__)


# ── Boot receipts ────────────────────────────────────────────────


@router.get("/boot-receipts")
async def get_boot_receipts(request: Request) -> Dict[str, Any]:
    await require_admin(request)
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
    await require_admin(request)
    return await alpha_decision_log.summary(get_db(), days=days)


@router.get("/decisions/recent")
async def get_recent_decisions(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    blocked_at: Optional[str] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    await require_admin(request)
    db = get_db()
    if db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if blocked_at:
        q["blocked_at"] = blocked_at
    if symbol:
        q["symbol"] = symbol.upper()
    try:
        cursor = db[alpha_decision_log.COLLECTION].find(
            q, projection={"_id": 0},
        ).sort("created_at", -1).limit(limit)
        items: List[Dict[str, Any]] = []
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
        return {"items": items, "count": len(items)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ml.v2.admin] decisions/recent failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Phase 5b broker wire — observability ─────────────────────────


@router.get("/phase5b/summary")
async def phase5b_summary(
    request: Request,
    days: int = Query(7, ge=1, le=30),
) -> Dict[str, Any]:
    """Per-lane breakdown of phase5b_intents rows. Reports how many
    signals would have fired vs how many were gate-blocked."""
    await require_admin(request)
    db = get_db()
    if db is None:
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
        cursor = db["phase5b_intents"].aggregate([
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
    lane: Optional[str] = Query(None, pattern="^(equity|crypto)$"),
    classification: Optional[str] = Query(
        None, pattern="^(SHADOW_ONLY|GATE_BLOCK|WOULD_HAVE_FIRED|FIRED)$"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Last N phase5b_intents rows for review."""
    await require_admin(request)
    db = get_db()
    if db is None:
        return {"items": [], "count": 0}
    q: Dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    if classification:
        q["classification"] = classification
    items: List[Dict[str, Any]] = []
    try:
        cursor = db["phase5b_intents"].find(
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
    await require_admin(request)
    from services.ml.camaro_shelly_bridge import get_bridge_status
    return await get_bridge_status(get_db(), limit=limit)


@router.post("/camaro/bridge/run")
async def camaro_bridge_run(
    request: Request,
    lookback_hours: int = Query(24, ge=1, le=168),
) -> Dict[str, Any]:
    """Trigger a Camaro→Shelly ingestion run. Idempotent — re-runs
    are no-ops on already-ingested ``trade_id``s."""
    await require_admin(request)
    from services.ml.camaro_shelly_bridge import run_bridge
    return await run_bridge(get_db(), lookback_hours=lookback_hours)
