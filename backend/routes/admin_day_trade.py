"""Day-trade scanner admin endpoints.

Extracted from ``routes/admin.py`` — manual scan trigger, recent
scan log, target queue, and EOD exit-monitor manual tick. URLs
unchanged.

  * ``POST /api/admin/day-trade/scan/{asset_class}``
  * ``GET  /api/admin/day-trade/scan/recent``
  * ``GET  /api/admin/day-trade/targets``
  * ``POST /api/admin/day-trade/exit-monitor/tick``
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-day-trade"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _scan_result_to_dict(result) -> dict:
    """Serialise a ``ScanResult`` to a JSON-safe dict. ``_id`` is
    never part of the dataclass so no Mongo leak risk."""
    chosen = asdict(result.chosen) if result.chosen is not None else None
    return {
        "scan_id": result.scan_id,
        "asset_class": result.asset_class,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "total_scanned": result.total_scanned,
        "blocked_count": result.blocked_count,
        "chosen": chosen,
        "candidates": [asdict(c) for c in result.candidates],
    }


@router.post("/day-trade/scan/{asset_class}")
async def day_trade_manual_scan(asset_class: str, request: Request):
    """Manually trigger a scan → rank → gate → queue pass for
    ``equity`` or ``crypto``. Returns the ranked candidate list +
    the chosen winner (if any). Same code path as the scheduled
    scanner — every manual run is fully audit-logged."""
    await _require_owner(request)
    lane = (asset_class or "").strip().lower()
    if lane not in ("equity", "crypto"):
        raise HTTPException(
            status_code=400, detail="asset_class must be 'equity' or 'crypto'",
        )
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.day_trade_scanner import run_scan
    result = await run_scan(db, lane)  # type: ignore[arg-type]
    return _scan_result_to_dict(result)


@router.get("/day-trade/scan/recent")
async def day_trade_recent_scans(request: Request, limit: int = 20):
    """Recent scan-log rows (both lanes, newest first). Used by
    the admin dashboard to show scanner activity. No mutation."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 100))
    cursor = db.day_trade_scan_log.find(
        {}, {"_id": 0},
    ).sort("finished_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        for k in ("started_at", "finished_at"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"rows": rows, "count": len(rows)}


@router.get("/day-trade/targets")
async def day_trade_targets(request: Request, status: str = "pending", limit: int = 50):
    """List day-trade targets (pending by default). Operator view
    of the scanner's outgoing queue."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 200))
    query: dict = {}
    if status and status.lower() != "all":
        query["status"] = status.lower()
    cursor = db.day_trade_targets.find(
        query, {"_id": 0},
    ).sort("queued_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        for k in ("queued_at", "max_hold_until"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"rows": rows, "count": len(rows), "status_filter": status}


@router.post("/day-trade/exit-monitor/tick")
async def day_trade_exit_tick(request: Request):
    """Manually trigger the day-trade EOD exit monitor. Useful for
    smoke-testing the 21:00 UTC close path without waiting."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.day_trade_exit_monitor import expire_due_day_trades
    counts = await expire_due_day_trades(db)
    return {"status": "ok", **counts}
