"""Notification lifecycle admin endpoints — manual cleanup + receipts list.

Extracted from ``routes/admin.py``. Pairs with the APScheduler-driven
``notification_lifecycle_sweep`` job in ``server.py`` (cron 04:00 +
16:00 UTC); these endpoints are the manual-trigger + audit-trail
companions.

  * ``POST /api/admin/notifications/lifecycle/cleanup``
  * ``GET  /api/admin/notifications/lifecycle/runs``

URLs unchanged. Owner-gated.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-notification-lifecycle"])
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


@router.post("/notifications/lifecycle/cleanup")
async def notifications_lifecycle_cleanup(
    request: Request, types: str | None = None,
):
    """Manually run the notification-lifecycle cleanup. Optional
    ``types`` param is a comma-separated list (e.g.
    ``toxic_spike,verdict_change``) to scope the run; omitting it
    runs every registered superseder.

    Same code path the regrade backfill calls automatically and
    the operator's one-shot script invokes from CLI."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.notification_lifecycle import supersede_stale_alerts
    type_list: list[str] | None = None
    if types:
        type_list = [t.strip() for t in types.split(",") if t.strip()]
    return await supersede_stale_alerts(db, types=type_list, trigger="manual_admin")


@router.get("/notifications/lifecycle/runs")
async def notifications_lifecycle_runs(
    request: Request, limit: int = 20,
):
    """Recent ``notification_lifecycle_runs`` receipts (newest first).
    Lets the operator audit "no stale alerts" claims with timestamps
    + trigger source."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 200))
    cursor = db.notification_lifecycle_runs.find(
        {}, {"_id": 0},
    ).sort("ran_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        v = r.get("ran_at")
        if hasattr(v, "isoformat"):
            r["ran_at"] = v.isoformat()
    return {"rows": rows, "count": len(rows)}
