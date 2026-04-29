"""Trading-mode guards — FastAPI dependencies that gate order routes by
the caller's PAPER vs LIVE mode.

Use these on every order-execution route so the backend rejects requests
that bypass the navbar pill (e.g. direct API calls, stale frontend
caches, future mobile clients):

    from services.trading_mode_guards import require_live_mode

    @router.post("/order/{broker_id}")
    async def place_order(broker_id: str, ..., _live=Depends(require_live_mode)):
        ...

The guard returns the user dict on success so the route handler doesn't
need to call ``get_current_user`` separately.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from routes.auth import get_current_user
from services.trading_mode_service import get_user_trading_mode


async def require_live_mode(request: Request) -> dict:
    """Allow the request only when the caller's mode is ``live``.

    Rejects with 403 + a structured ``detail`` so the UI can surface a
    "switch to LIVE" CTA without parsing free-form messages.
    """
    user = await get_current_user(request)
    mode = await get_user_trading_mode(user)
    if mode != "live":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wrong_mode",
                "required_mode": "live",
                "current_mode": mode,
                "message": "This action requires LIVE trading mode. "
                           "Switch via the navbar pill to continue.",
            },
        )
    return user


async def require_paper_mode(request: Request) -> dict:
    """Allow the request only when the caller's mode is ``paper``."""
    user = await get_current_user(request)
    mode = await get_user_trading_mode(user)
    if mode != "paper":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wrong_mode",
                "required_mode": "paper",
                "current_mode": mode,
                "message": "This action requires PAPER trading mode. "
                           "Switch via the navbar pill to continue.",
            },
        )
    return user
