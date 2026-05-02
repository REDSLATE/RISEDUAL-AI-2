"""Terminal endpoints (T1): /market-state + /signal/{symbol}.

All endpoints are owner-gated via ``routes.admin._require_owner``,
matching every other decision-surface route in this codebase.

Top Actions (/top-actions) is intentionally NOT shipped here — T2
will add it once ``position_context`` is reliably populated. Shipping
it sooner would risk wrong #1-action recommendations on users with
existing open positions, which is the one failure mode the product
spec explicitly rules out.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from services.terminal_aggregator import (
    TerminalContext, get_market_state, get_signal,
)

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

db = None


def set_db(database):
    global db
    db = database


async def _require_owner_ref(request: Request):
    """Delegate to the canonical owner gate defined in routes.admin.
    Keeping the import lazy so route registration doesn't depend on
    admin being loaded first."""
    from routes.admin import _require_owner
    return await _require_owner(request)


@router.get("/market-state")
async def terminal_market_state(request: Request):
    """Answers: is the market safe to trade right now?

    Single-doc read over ``option_universe._id="current"``. Stale or
    missing snapshot returns CLOSED — never guesses. Budget: < 50ms.
    """
    await _require_owner_ref(request)
    ctx = TerminalContext(db=db)
    return await get_market_state(ctx)


@router.get("/signal/{symbol}")
async def terminal_signal(
    symbol: str, request: Request, user_id: str | None = None,
):
    """Answers the Why / Risks / How-much questions for one symbol.

    Read-only — never creates a new decision. Per-user ``position_context``
    lookup is keyed on optional ``user_id`` query param; omit it to get
    the market-agnostic view.
    """
    await _require_owner_ref(request)
    if not symbol or len(symbol) > 12:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    ctx = TerminalContext(db=db)
    return await get_signal(ctx, symbol=symbol, user_id=user_id)
