"""Owner-only API for the Operator Trading Gate.

Endpoints
---------
GET  /api/admin/trading-gate/status   — current authorization state
POST /api/admin/trading-gate/toggle   — flip enabled (owner only)
GET  /api/admin/trading-gate/history  — last N changes (audit trail)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from . import operator_trading_gate as gate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/trading-gate",
    tags=["admin", "trading-gate"],
)


_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _get_db():
    if _db is None:
        raise HTTPException(
            status_code=503, detail="trading-gate: db not wired",
        )
    return _db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


class ToggleRequest(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=500)


@router.get("/status")
async def status(request: Request):
    await _require_owner(request)
    return await gate.get_status(_get_db())


@router.post("/toggle")
async def toggle(body: ToggleRequest, request: Request):
    user = await _require_owner(request)
    db = _get_db()
    return await gate.set_authorized(
        db,
        enabled=body.enabled,
        operator_id=user.get("email") or str(user.get("_id")),
        note=body.note,
    )


@router.get("/history")
async def history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    enabled: Optional[bool] = None,
):
    await _require_owner(request)
    db = _get_db()
    q: dict = {}
    if enabled is not None:
        q["enabled"] = bool(enabled)
    cursor = (
        db[gate.HISTORY_COLLECTION]
        .find(q, {"_id": 0})
        .sort("at", -1)
        .limit(limit)
    )
    out = [doc async for doc in cursor]
    return {"history": out, "count": len(out)}


@router.get("/synthetic-summary")
async def synthetic_summary(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Recent ``PAUSED_BY_OPERATOR`` ADL receipts — what *would*
    have traded if the gate were open."""
    await _require_owner(request)
    db = _get_db()
    cursor = (
        db["alpha_decision_log"]
        .find(
            {
                "blocked_at": "executor",
                "extras.blocker": "operator_trading_gate",
                "extras.synthetic": True,
            },
            {"_id": 0},
        )
        .sort("recorded_at", -1)
        .limit(limit)
    )
    out = [doc async for doc in cursor]
    return {"synthetic_receipts": out, "count": len(out)}
