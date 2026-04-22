"""Live Options Trading routes — multi-broker dispatch.

Pairs with `services.brokers.*`. Route layer responsibilities:

  1. Authenticate the user.
  2. Enforce ODD (Options Disclosure Document) acceptance — stored
     once per user on the User document. Any route that CAN submit
     an order MUST 403 if ODD isn't accepted.
  3. Dispatch to `get_options_adapter(user.options_provider)`.
  4. Translate adapter dataclasses → JSON response shapes the UI
     can consume verbatim.

Why ODD lives here and not in the adapter layer:
  * Alpaca also enforces ODD during account onboarding, so duplicate
    attestation is belt-and-suspenders. But a paper-mode stub
    adapter (future Tradier sandbox, say) might NOT enforce it, and
    we still want our own flag.
  * The flag is a single DB field — putting it behind route auth
    is O(1); making every adapter re-check would be O(N).

Paper vs live:
  * `/api/paper/trade` still handles the paper path — untouched.
  * `/api/options/*` here is LIVE only. The UI renders a big red
    "LIVE" badge when users click through from paper-mode scanners.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.auth_helpers import get_current_user
from services.brokers.occ_symbol import build_occ_symbol
from services.brokers.options_adapter import (
    BrokerNotImplementedError,
    OptionLeg,
    OrderSide,
)
from services.brokers.registry import (
    SUPPORTED_PROVIDERS,
    get_options_adapter,
)
from services.structured_log import log_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/options", tags=["options-trading"])

_db = None


def set_db(database):
    global _db
    _db = database


# ── Request models ─────────────────────────────────────────────────

class OptionOrderRequest(BaseModel):
    """Single-leg option order. Strike/expiry/type become an
    OCC symbol via `build_occ_symbol` on the server so clients
    never construct that string themselves."""
    underlying: str
    strike: float = Field(gt=0)
    expiry: str = Field(description="ISO date YYYY-MM-DD")
    option_type: str = Field(pattern="^(call|put)$")
    side: str = Field(
        pattern="^(buy_to_open|sell_to_close|buy_to_close|sell_to_open)$"
    )
    qty: int = Field(gt=0, le=1000)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    time_in_force: str = Field(default="day", pattern="^(day|gtc|ioc|fok)$")
    limit_price: Optional[float] = Field(default=None, gt=0)
    best_execution: bool = Field(
        default=False,
        description=(
            "When true, route to the broker with lowest estimated "
            "spread across all enabled connections (ignoring user's "
            "default provider choice). Requires ≥1 enabled broker."
        ),
    )


class ODDAcceptRequest(BaseModel):
    """Structural attestation. Client must POST this exact
    payload (acknowledging the OCC disclosure) before any
    live-order route will accept its calls."""
    accept: bool


# ── Helpers ────────────────────────────────────────────────────────

def _user_provider(user: dict) -> str:
    """Pick which broker this user's live options orders route to.

    Phase 1 reads from `user.broker.options_provider` with a fallback
    to `alpaca` for back-compat with existing accounts. Users will
    later pick their provider in a settings modal; for now Alpaca is
    the default because it's the only live adapter.
    """
    broker_prefs = user.get("broker") or {}
    return (broker_prefs.get("options_provider") or "alpaca").lower()


def _ensure_odd_accepted(user: dict) -> None:
    """Raise 403 if user hasn't accepted the OCC disclosure. Lives
    in one place so every order route inherits the guard."""
    odd = (user.get("compliance") or {}).get("odd_accepted_at")
    if not odd:
        raise HTTPException(
            status_code=403,
            detail=(
                "Options Disclosure Document must be accepted before "
                "live options orders. Call POST /api/options/odd/accept."
            ),
        )


# ── ODD routes ─────────────────────────────────────────────────────

@router.get("/odd/status")
async def odd_status(request: Request):
    """Return whether the current user has accepted the ODD."""
    user = await get_current_user(request)
    odd = (user.get("compliance") or {}).get("odd_accepted_at")
    return {
        "accepted": bool(odd),
        "accepted_at": odd.isoformat() if hasattr(odd, "isoformat") else odd,
    }


@router.post("/odd/accept")
async def odd_accept(body: ODDAcceptRequest, request: Request):
    """Record the user's acceptance of the Options Disclosure
    Document. Stored as a single UTC timestamp under
    `compliance.odd_accepted_at` on the User document. The client
    MUST have displayed the ODD text before POSTing this."""
    if not body.accept:
        raise HTTPException(status_code=400, detail="accept=true required")
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)
    await _db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"compliance.odd_accepted_at": now}},
    )
    return {"accepted": True, "accepted_at": now.isoformat()}


# ── Account + positions ────────────────────────────────────────────

@router.get("/providers")
async def list_providers(request: Request):
    """List supported brokers + the user's current selection."""
    user = await get_current_user(request)
    return {
        "supported": SUPPORTED_PROVIDERS,
        "current": _user_provider(user),
    }


@router.get("/status")
async def options_status(request: Request):
    """Options-enabled probe against the user's selected broker.
    Safe to call without ODD (it's an account info query, not an
    order action)."""
    user = await get_current_user(request)
    provider = _user_provider(user)
    try:
        adapter = get_options_adapter(provider)
        st = await adapter.is_options_enabled()
        return {
            "enabled": st.enabled,
            "level": st.level,
            "provider": st.provider,
            "details": st.details,
        }
    except BrokerNotImplementedError as exc:
        # Stub broker — 501, not 500.
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        log_error(logger, {
            "context": "options_routes",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"is_options_enabled failed for provider={provider}",
        })
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")


@router.get("/buying-power")
async def buying_power(request: Request):
    user = await get_current_user(request)
    provider = _user_provider(user)
    try:
        adapter = get_options_adapter(provider)
        bp = await adapter.get_options_buying_power()
        return {
            "provider": provider,
            "cash": bp.cash,
            "options_buying_power": bp.options_buying_power,
            "multiplier": bp.multiplier,
            "currency": bp.currency,
        }
    except BrokerNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))


@router.get("/positions")
async def list_positions(request: Request):
    user = await get_current_user(request)
    provider = _user_provider(user)
    try:
        adapter = get_options_adapter(provider)
        positions = await adapter.get_option_positions()
        return [
            {
                "occ_symbol": p.occ_symbol,
                "underlying": p.underlying,
                "strike": p.strike,
                "expiration": p.expiration,
                "option_type": p.option_type.value,
                "qty": p.qty,
                "avg_fill_price": p.avg_fill_price,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in positions
        ]
    except BrokerNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))


# ── Order entry ────────────────────────────────────────────────────

@router.post("/order")
async def place_order(body: OptionOrderRequest, request: Request):
    """Submit a live options order. ODD acceptance enforced.

    Construction pipeline:
      1. Auth + ODD guard.
      2. Build OCC symbol from underlying/strike/expiry/type. Any
         malformation (e.g. strike > max encodable) surfaces as 400
         with the `ValueError` message.
      3. Dispatch to provider adapter's `place_option_order`.
      4. Broker-level rejections (403 permission, 400 margin) are
         forwarded to the client as HTTP errors with the raw
         message so the UI can show "your account needs Level 2
         approval" style feedback.
    """
    user = await get_current_user(request)
    _ensure_odd_accepted(user)

    provider = _user_provider(user)
    try:
        occ = build_occ_symbol(
            body.underlying, body.expiry, body.option_type, body.strike,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid contract: {exc}")

    leg = OptionLeg(
        occ_symbol=occ,
        qty=body.qty,
        side=OrderSide(body.side),
    )

    # ── Smart-router path ──
    # When the client sets `best_execution=true`, route to the
    # lowest-spread enabled broker instead of the user's default
    # provider. Smart routing still honours ODD (gate already ran
    # above) and bubbles the same broker-level errors.
    if body.best_execution:
        from services.brokers.smart_router import SmartOrderRouter
        try:
            order, decision = await SmartOrderRouter().route_order(
                occ,
                [leg],
                order_type=body.order_type,
                time_in_force=body.time_in_force,
                limit_price=body.limit_price,
            )
        except RuntimeError as exc:
            # "no enabled brokers" — reachable but retryable.
            raise HTTPException(status_code=503, detail=str(exc))
        except BrokerNotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except Exception as exc:
            log_error(logger, {
                "context": "options_routes",
                "type": type(exc).__name__,
                "error": str(exc),
                "note": f"smart-routed order placement failed for user={user['_id']}",
            })
            raise HTTPException(status_code=502, detail=f"Broker error: {exc}")

        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "qty": order.qty,
            "filled_qty": order.filled_qty,
            "limit_price": order.limit_price,
            "time_in_force": order.time_in_force,
            "created_at": order.created_at.isoformat(),
            "occ_symbol": occ,
            "provider": decision.provider,
            "routing": {
                "mode": "smart",
                "estimated_spread": decision.estimated_spread,
                "candidates": [
                    {"provider": p, "estimated_spread": s}
                    for p, s in decision.all_candidates
                ],
            },
        }

    # ── Direct-broker path ──
    adapter = get_options_adapter(provider)
    try:
        order = await adapter.place_option_order(
            [leg],
            order_type=body.order_type,
            time_in_force=body.time_in_force,
            limit_price=body.limit_price,
        )
    except BrokerNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except PermissionError as exc:
        # Alpaca's 403 ("not enabled for options") bubbles up here.
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        # Adapter-level validation failure (e.g. empty legs).
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_error(logger, {
            "context": "options_routes",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"order placement failed for user={user['_id']} provider={provider}",
        })
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")

    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "qty": order.qty,
        "filled_qty": order.filled_qty,
        "limit_price": order.limit_price,
        "time_in_force": order.time_in_force,
        "created_at": order.created_at.isoformat(),
        "occ_symbol": occ,
        "provider": provider,
        "routing": {"mode": "direct"},
    }


@router.delete("/order/{order_id}")
async def cancel_order(order_id: str, request: Request):
    user = await get_current_user(request)
    _ensure_odd_accepted(user)
    provider = _user_provider(user)
    try:
        adapter = get_options_adapter(provider)
        ok = await adapter.cancel_option_order(order_id)
        return {"canceled": ok, "order_id": order_id}
    except BrokerNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
