"""Admin-only ops snapshot endpoint.

Surfaces a one-stop health/config readout to the AdminPanel.
Read-only; never writes anywhere; never returns API keys.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user
from services.ops_snapshot import collect_ops_snapshot
from services.ops_alerter import is_configured as alerter_configured, run_tick as alerter_tick

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


@router.get("/ops-alerter/status")
async def ops_alerter_status(request: Request) -> dict:
    """Lightweight status check for the AdminPanel — surfaces
    whether the wedge-detector webhook is configured, and the most
    recent persisted state. Never returns the webhook URL itself."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    state = {}
    if _db is not None:
        state = await _db["ops_alerter_state"].find_one(
            {"_id": "state"}, {"_id": 0},
        ) or {}
    return {
        "configured": alerter_configured(),
        "last_notes": state.get("last_notes", []),
        "alert_history": state.get("alert_history", {}),
        "updated_at": state.get("updated_at"),
    }


@router.post("/ops-alerter/run")
async def ops_alerter_run(request: Request) -> dict:
    """Manually trigger one alerter tick. Useful for verifying the
    webhook URL works without waiting for the cron. Identical to
    the scheduled job's behaviour — diffs against persisted state,
    posts on transitions, dedupes."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await alerter_tick(_db)
