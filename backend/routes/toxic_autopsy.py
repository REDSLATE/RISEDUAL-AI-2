"""Admin-only Toxic Spike Autopsy endpoint.

Powers the admin Autopsy panel — answers WHY 56 high-confidence
predictions failed, grouped across six dimensions. Read-only.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user
from services.toxic_autopsy_service import build_autopsy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["toxic-autopsy"])

_db = None


def set_db(database) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() in ("admin", "owner")


@router.get("/toxic-spike/autopsy")
async def toxic_spike_autopsy(
    request: Request,
    days: int = Query(2, ge=1, le=30, description="Lookback window in days"),
    min_confidence: float = Query(
        80.0, ge=0.0, le=100.0,
        description="Minimum confidence (0-100) — matches nightly cleanup default",
    ),
) -> dict:
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await build_autopsy(
        _db,
        days=days,
        min_confidence_pct=min_confidence,
    )
