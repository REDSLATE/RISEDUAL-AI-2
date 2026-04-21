"""Rejected-signals admin routes.

Thin wrapper over `services/rejection_log.py` so the Admin UI can browse
recent rejections and see aggregate counts without touching Mongo
directly. Admin/owner only.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from services.rejection_log import (
    SOURCES,
    recent_rejections,
    rejection_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["rejections"])


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/rejections")
async def list_rejections(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    source: str | None = Query(default=None),
    asset: str | None = Query(default=None),
):
    """Return most-recent rejections, newest first."""
    await _require_admin(request)
    if source and source not in SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown source; must be one of: {sorted(SOURCES)}",
        )
    rows = await recent_rejections(limit=limit, source=source, asset=asset)
    return {"count": len(rows), "rejections": rows}


@router.get("/rejections/stats")
async def rejections_stats(
    request: Request,
    hours: int = Query(default=24, ge=1, le=24 * 30),
):
    """Aggregate rejection counts by source for the past N hours."""
    await _require_admin(request)
    return await rejection_stats(hours=hours)
