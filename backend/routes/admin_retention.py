"""Admin Retention Panel — status + manual purge trigger.

Owner/admin only. Mirrors the "Operator Control → Retention" panel
pattern from mission.risedual.ai: shows per-collection expired
backlog counts, a big PURGE BACKLOG NOW button, and a lifetime
"total purged" counter.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from services.auth_helpers import get_current_user
from services import retention_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/retention", tags=["admin-retention"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    from fastapi import HTTPException
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/status")
async def retention_status(request: Request):
    """Return backlog + lifetime purge stats for the Retention panel."""
    await _require_admin(request)
    return await retention_service.get_retention_status(db)


@router.post("/purge")
async def retention_purge(request: Request):
    """Drain up to ``RISEDUAL_RETENTION_PURGE_BATCH`` expired rows.

    Idempotent — the caller re-issues the request while
    ``more_remains`` is ``true`` to fully clear the queue.
    """
    user = await _require_admin(request)
    result = await retention_service.purge_backlog(db, triggered_by="manual")
    logger.info(
        f"[retention] manual purge by {user.get('email')}: "
        f"purged={result['purged_total']} more_remains={result['more_remains']}",
    )
    return result
