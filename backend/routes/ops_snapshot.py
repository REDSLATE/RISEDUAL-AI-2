"""Admin-only ops snapshot endpoint.

Surfaces a one-stop health/config readout to the AdminPanel.
Read-only; never writes anywhere; never returns API keys.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user
from services.ops_snapshot import collect_ops_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["ops"])


_db = None


def set_db(database) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() in ("admin", "owner")


@router.get("/ops-snapshot")
async def ops_snapshot(request: Request) -> dict:
    """Operator dashboard payload — env flags, integrations,
    Mongo ping, scheduler heartbeat, Tier-3 progress, heuristic
    notes. Replaces "grep .env + curl /ready + check Mongo"."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await collect_ops_snapshot(_db)
