"""Trading-mode routes — per-user PAPER vs LIVE switch.

GET  /api/trading-mode/current   → mode + cooldown state
POST /api/trading-mode/switch    → flip mode (with confirm + cooldown)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routes.auth import get_current_user
from services.trading_mode_service import (
    TradingModeError,
    get_mode_state,
    set_db as _set_service_db,
    switch_user_trading_mode,
)

router = APIRouter(prefix="/api/trading-mode", tags=["trading-mode"])


def set_db(database):
    _set_service_db(database)


class SwitchModeRequest(BaseModel):
    mode: str
    confirm_text: Optional[str] = None


@router.get("/current")
async def current_mode(request: Request):
    """Return the caller's current trading mode + cooldown status."""
    user = await get_current_user(request)
    state = await get_mode_state(user)
    return state


@router.post("/switch")
async def switch_mode(req: SwitchModeRequest, request: Request):
    """Flip the caller's trading mode.

    Errors map to HTTP codes the UI can branch on:

    * 400 ``invalid_mode``      — bad mode value
    * 400 ``confirm_required``  — switching TO live without "LIVE" text
    * 429 ``cooldown``          — too soon since last switch
    * 401                       — unauth (raised by get_current_user)
    """
    user = await get_current_user(request)

    request_meta = {
        "ip": (request.client.host if request.client else None),
        "user_agent": request.headers.get("user-agent"),
    }

    try:
        result = await switch_user_trading_mode(
            user,
            new_mode=req.mode,
            confirm_text=req.confirm_text,
            request_meta=request_meta,
        )
    except TradingModeError as e:
        status = 429 if e.code == "cooldown" else 400
        raise HTTPException(status_code=status, detail={
            "code": e.code,
            "message": e.message,
        })
    return result
