"""Post-signup onboarding endpoints.

Small surface — the frontend just needs to know whether to show the
onboarding modal, and how to mark it complete.

The onboarding flow itself lives on the frontend; the backend only
persists the "seen it" flag so a returning user isn't nagged.
"""
from __future__ import annotations

from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

_db = None


def set_db(db):
    global _db
    _db = db


class CompleteRequest(BaseModel):
    stage: Optional[str] = None  # e.g. "brokers_connected", "skipped"


@router.get("/status")
async def onboarding_status(request: Request):
    """Return whether the caller has completed post-signup onboarding.

    Also returns a summary of broker connections so the UI can render
    a live "you have X of Y connected" state without a second call.
    """
    user = await get_current_user(request)
    uid = str(user["_id"])
    fresh = await _db.users.find_one(
        {"_id": ObjectId(uid)},
        {"onboarding_completed": 1, "onboarding_stage": 1,
         "onboarding_completed_at": 1, "created_at": 1},
    )
    # Broker snapshot (never fatal — onboarding must still open if this fails).
    connections: list = []
    try:
        cur = _db.broker_connections.find(
            {"user_id": uid, "status": {"$ne": "revoked"}},
            {"_id": 0, "broker_id": 1, "status": 1, "connected_at": 1},
        )
        connections = [c async for c in cur]
    except Exception:  # noqa: BLE001
        connections = []
    connected_ids = {c.get("broker_id") for c in connections if c.get("status") in ("connected", "active")}

    return {
        "completed": bool((fresh or {}).get("onboarding_completed", False)),
        "stage": (fresh or {}).get("onboarding_stage"),
        "completed_at": (fresh or {}).get("onboarding_completed_at"),
        "brokers": {
            "public_connected": "public" in connected_ids,
            "moomoo_connected": "moomoo" in connected_ids,
            "any_connected": len(connected_ids) > 0,
            "connections": connections,
        },
    }


@router.post("/complete")
async def onboarding_complete(request: Request, body: CompleteRequest):
    """Mark the caller's onboarding as complete. Idempotent."""
    from datetime import datetime, timezone
    user = await get_current_user(request)
    uid = str(user["_id"])
    now = datetime.now(timezone.utc)
    try:
        _oid = ObjectId(uid)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid user id")
    await _db.users.update_one(
        {"_id": _oid},
        {"$set": {
            "onboarding_completed": True,
            "onboarding_stage": (body.stage or "completed"),
            "onboarding_completed_at": now,
        }},
    )
    return {"ok": True, "completed": True, "stage": body.stage or "completed"}


__all__ = ["router", "set_db"]
