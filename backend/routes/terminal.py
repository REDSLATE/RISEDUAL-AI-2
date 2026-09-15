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
    TerminalContext, get_market_state, get_signal, get_top_actions,
)
import asyncio
import json as _json
import logging as _logging
from datetime import datetime as _dt, timezone as _tz
from sse_starlette.sse import EventSourceResponse

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


@router.get("/top-actions")
async def terminal_top_actions(
    request: Request,
    user_id: str | None = None,
    limit: int = 10,
):
    """Answers Q6: what should the operator act on right now?

    Returns the operator's prioritized action queue for the terminal:
    a ranked list of MANAGE_POSITION / ENTER / WATCH / EXIT cards
    composed from the freshest sovereign decisions per symbol plus
    any open paper trades for ``user_id``.

    Read-only — never creates a new decision. Empty list on cold-start
    is a valid response. Bounded ``limit`` ∈ [1, 50] to keep payload
    sizes predictable.
    """
    await _require_owner_ref(request)
    if limit is not None and (limit < 1 or limit > 50):
        raise HTTPException(
            status_code=400, detail="limit must be between 1 and 50",
        )
    ctx = TerminalContext(db=db)
    return await get_top_actions(ctx, user_id=user_id, limit=limit)


@router.get("/top-actions/stream")
async def terminal_top_actions_stream(
    request: Request,
    user_id: str | None = None,
    limit: int = 10,
):
    """Server-Sent Events stream of the top-actions payload.

    Pushes a fresh snapshot whenever the underlying data **changes**:
    a new sovereign_decision lands, a paper_trade opens/closes, or
    a live-price check in the EXIT detector flips a breach state.

    Change detection is intentionally simple — re-compute the payload
    every 10s and emit only when a structural hash of the actions
    list changes. This gives the operator near-real-time visibility
    without requiring a Mongo change stream (which needs a replica
    set) or a fan-out pub/sub layer.

    A heartbeat fires every 30s even when nothing changes so the
    proxy doesn't kill the connection.

    Auth is cookie-based (same as the snapshot endpoint) — works with
    vanilla ``new EventSource(url)`` on the frontend, no custom
    headers required.
    """
    await _require_owner_ref(request)
    if limit is not None and (limit < 1 or limit > 50):
        raise HTTPException(
            status_code=400, detail="limit must be between 1 and 50",
        )
    ctx = TerminalContext(db=db)

    async def _event_generator():
        last_hash: str | None = None
        last_heartbeat = _dt.now(_tz.utc)
        # Kick off with an initial snapshot so the frontend has
        # something to render immediately (no waiting for the first
        # 10s tick).
        try:
            first = await get_top_actions(ctx, user_id=user_id, limit=limit)
            last_hash = _hash_actions(first.get("actions") or [])
            yield {
                "event": "snapshot",
                "data": _json.dumps(first, default=str),
            }
        except Exception as exc:  # noqa: BLE001
            _logging.getLogger(__name__).warning(
                "[top-actions-stream] initial snapshot failed: %s", exc,
            )

        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(10)
            now = _dt.now(_tz.utc)
            try:
                payload = await get_top_actions(
                    ctx, user_id=user_id, limit=limit,
                )
                new_hash = _hash_actions(payload.get("actions") or [])
                if new_hash != last_hash:
                    last_hash = new_hash
                    yield {
                        "event": "snapshot",
                        "data": _json.dumps(payload, default=str),
                    }
                elif (now - last_heartbeat).total_seconds() >= 30:
                    last_heartbeat = now
                    yield {
                        "event": "heartbeat",
                        "data": _json.dumps({
                            "timestamp": now.isoformat(),
                            "actions_hash": last_hash,
                        }),
                    }
            except Exception as exc:  # noqa: BLE001
                _logging.getLogger(__name__).warning(
                    "[top-actions-stream] tick failed: %s", exc,
                )

    return EventSourceResponse(_event_generator())


def _hash_actions(actions: list) -> str:
    """Cheap structural hash — concatenate (symbol, kind, rank,
    round(priority, 2)) for each action. Good enough to detect
    meaningful changes without being so sensitive it fires on every
    minor confidence-score tick."""
    import hashlib
    key = "|".join(
        f"{a.get('symbol', '')}:{a.get('kind', '')}:{a.get('rank', '')}:"
        f"{round(float(a.get('priority_score') or 0.0), 2)}"
        for a in actions
    )
    return hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()
