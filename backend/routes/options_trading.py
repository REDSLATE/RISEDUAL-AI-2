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
from ai_core.options_pricing import compute_greeks_for_contract
from bson import ObjectId

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/options", tags=["options-trading"])

_db = None


def set_db(database):
    global _db
    _db = database


async def _log_order_audit(
    *,
    user: dict,
    provider: str,
    occ_symbol: str | None,
    legs: list[dict],
    order_type: str,
    time_in_force: str,
    limit_price: Optional[float],
    order_id: str,
    status: str,
    routing_mode: str,
    is_spread: bool,
) -> None:
    """Persist a one-line audit record for every live options order.

    Writes to the `option_orders` collection. Key field is
    `odd_accepted_at` — the timestamp at which the user accepted
    the Options Disclosure Document, copied onto the order record
    so future audits can prove ODD was accepted BEFORE the order
    was placed without having to cross-reference the user document
    (which could be modified later).

    Best-effort: a Mongo write failure is logged but never blocks
    the order from completing. The broker already accepted the
    order at this point; losing the audit line is a recoverable
    regression, not a trading halt.
    """
    if _db is None:
        return
    try:
        odd_at = (user.get("compliance") or {}).get("odd_accepted_at")
        await _db.option_orders.insert_one({
            "user_id": str(user.get("_id")),
            "provider": provider,
            "occ_symbol": occ_symbol,
            "legs": legs,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "limit_price": limit_price,
            "order_id": order_id,
            "status": status,
            "routing_mode": routing_mode,
            "is_spread": is_spread,
            "odd_accepted_at": odd_at,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:
        log_error(logger, {
            "context": "options_routes",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"audit write failed for order_id={order_id}",
        })


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


class SpreadLegRequest(BaseModel):
    """Single leg within a multi-leg spread.

    `underlying` is shared across legs and declared on the parent;
    each leg carries its own strike/expiry/type/side to allow
    verticals (same expiry, two strikes), calendars (same strike,
    two expiries), diagonals (different both), and iron condors
    (4 legs, different strikes + sides)."""
    strike: float = Field(gt=0)
    expiry: str = Field(description="ISO date YYYY-MM-DD")
    option_type: str = Field(pattern="^(call|put)$")
    side: str = Field(
        pattern="^(buy_to_open|sell_to_close|buy_to_close|sell_to_open)$"
    )
    qty: int = Field(gt=0, le=1000)


class SpreadOrderRequest(BaseModel):
    """Multi-leg options order. 2-4 legs — supports verticals,
    calendars, diagonals, straddles, strangles, iron condors,
    butterflies. The route enforces the 2-4 range; the adapter
    additionally enforces broker-specific capacity.

    Phase 2 restriction: only routes through the user's configured
    provider (typically Alpaca). Smart-routed spreads wait for
    Phase 3 when more adapters support multi-leg natively.
    """
    underlying: str
    legs: list[SpreadLegRequest] = Field(min_length=2, max_length=4)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    time_in_force: str = Field(default="day", pattern="^(day|gtc|ioc|fok)$")
    limit_price: Optional[float] = Field(
        default=None,
        description=(
            "Net debit/credit across all legs. Positive = debit "
            "(you pay), negative = credit (you receive). Ignored "
            "for market orders."
        ),
    )
    best_execution: bool = Field(
        default=False,
        description=(
            "When true, route the spread to the multileg-capable "
            "broker with the lowest aggregate estimated spread. "
            "Filters out brokers whose adapter declares "
            "supports_multileg=False (e.g. Tradier quote-only)."
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
    # `get_current_user` stringifies `_id`; cast back so update_one
    # matches the real ObjectId document (otherwise $set silently
    # writes to zero docs — classic stale-auth-cache footgun).
    await _db.users.update_one(
        {"_id": ObjectId(user["_id"])},
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
        raise HTTPException(status_code=502, detail="Broker unavailable")


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

    Mode guard: caller must be in LIVE trading mode.
    """
    from services.trading_mode_guards import require_live_mode
    user = await require_live_mode(request)
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
            raise HTTPException(status_code=502, detail="Broker order placement failed")

        await _log_order_audit(
            user=user,
            provider=decision.provider,
            occ_symbol=occ,
            legs=[{"occ_symbol": occ, "qty": leg.qty, "side": leg.side.value}],
            order_type=body.order_type,
            time_in_force=body.time_in_force,
            limit_price=body.limit_price,
            order_id=order.order_id,
            status=order.status.value,
            routing_mode="smart",
            is_spread=False,
        )

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
        raise HTTPException(status_code=502, detail="Broker order placement failed")

    await _log_order_audit(
        user=user,
        provider=provider,
        occ_symbol=occ,
        legs=[{"occ_symbol": occ, "qty": leg.qty, "side": leg.side.value}],
        order_type=body.order_type,
        time_in_force=body.time_in_force,
        limit_price=body.limit_price,
        order_id=order.order_id,
        status=order.status.value,
        routing_mode="direct",
        is_spread=False,
    )

    # ── Patent J/K/M/I — post-fill guard audit ───────────────────────
    # Same pattern as broker.py: log the fill into the proof chain
    # for compliance + audit. Phase 1 = observation.
    try:
        _est_notional = float(body.qty) * float(body.limit_price or 0.0) * 100.0
        if _est_notional > 0:
            from services.manual_order_guard import run_manual_order_guard
            from server import db as _server_db
            await run_manual_order_guard(
                db=_server_db,
                user=user,
                asset_class="options",
                symbol=str(body.underlying),
                side=str(leg.side.value),
                base_notional=_est_notional,
                context={
                    "route": "options.place_order",
                    "occ_symbol": occ,
                    "order_id": order.order_id,
                    "provider": provider,
                    "phase": "post_fill_audit",
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[manual_guard] options post-fill audit failed: {e}")

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


# ── Greeks preview ─────────────────────────────────────────────────

@router.get("/greeks")
async def greeks_preview(
    underlying_price: float,
    strike: float,
    expiry: str,
    option_type: str,
    iv_percent: Optional[float] = None,
    request: Request = None,  # type: ignore[assignment]
):
    """Return delta/gamma/theta/vega/rho for a single contract.

    Auth-gated (any logged-in user) but ODD-ungated — Greeks are a
    read-only preview, not an order. The UI order-modal calls this
    before the user clicks Submit so they see the risk profile
    without having to place the trade.

    Conventions (retail-broker style, matching `compute_greeks` doc):
      * `theta` per calendar day (not per year).
      * `vega` per 1% IV change.
      * `rho` per 1% rate change.
    All per-contract; multiply by 100 for per-lot dollar P&L.
    """
    await get_current_user(request)
    if option_type.lower() not in ("call", "put"):
        raise HTTPException(status_code=400, detail="option_type must be call|put")
    if underlying_price <= 0 or strike <= 0:
        raise HTTPException(status_code=400, detail="prices must be positive")
    try:
        data = compute_greeks_for_contract(
            underlying_price=underlying_price,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            iv_percent=iv_percent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid inputs: {exc}")
    return {
        "underlying_price": underlying_price,
        "strike": strike,
        "expiry": expiry,
        "option_type": option_type.lower(),
        "iv_percent": iv_percent,
        **data,
    }


# ── Multi-leg spread orders ─────────────────────────────────────────

@router.post("/spread")
async def place_spread_order(body: SpreadOrderRequest, request: Request):
    """Submit a multi-leg option spread (2-4 legs).

    Supported structures (broker-agnostic — expressed as leg tuples):
      * Vertical: 2 legs, same expiry, same type, different strikes.
      * Calendar: 2 legs, same strike, different expiries.
      * Diagonal: 2 legs, different strike + different expiry.
      * Straddle/strangle: 2 legs, same side, call + put.
      * Iron condor/butterfly: 4 legs, mix.

    Phase 2 routes ALL spreads through the user's configured provider
    (no smart routing yet — provider-specific multi-leg semantics
    differ enough that we ship narrow first). Non-Alpaca providers
    return 501 since only Alpaca's mleg envelope is implemented
    today.
    """
    user = await get_current_user(request)
    _ensure_odd_accepted(user)

    provider = _user_provider(user)

    # Build OCC symbols + option legs up front so a bad input fails
    # before we hit the broker.
    occ_legs: list[tuple[str, OptionLeg]] = []
    for i, leg in enumerate(body.legs):
        try:
            occ = build_occ_symbol(
                body.underlying, leg.expiry, leg.option_type, leg.strike,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid leg {i + 1}: {exc}",
            )
        occ_legs.append((occ, OptionLeg(
            occ_symbol=occ,
            qty=leg.qty,
            side=OrderSide(leg.side),
        )))

    # Basic structural sanity: require at least one opening leg.
    # Pure "close/close" spreads are legal but rare; flag them as a
    # user-mistake hint instead of silently submitting.
    opens = [
        leg for _, leg in occ_legs
        if leg.side in (OrderSide.BUY_TO_OPEN, OrderSide.SELL_TO_OPEN)
    ]
    if not opens:
        raise HTTPException(
            status_code=400,
            detail=(
                "spread must contain at least one opening leg "
                "(buy_to_open or sell_to_open)"
            ),
        )

    adapter = get_options_adapter(provider)
    # ── Smart-routed path ──
    # When `best_execution=True`, the router picks the lowest-
    # aggregate-spread multileg-capable broker. Quote-only adapters
    # (Tradier today) are filtered out in `pick_for_spread` — they
    # can estimate spreads but can't submit the order, so they must
    # never win the contest.
    smart_decision = None
    if body.best_execution:
        from services.brokers.smart_router import SmartOrderRouter
        occ_list = [occ for occ, _ in occ_legs]
        leg_list = [leg for _, leg in occ_legs]
        try:
            order, smart_decision = await SmartOrderRouter().route_spread(
                occ_list, leg_list,
                order_type=body.order_type,
                time_in_force=body.time_in_force,
                limit_price=body.limit_price,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except BrokerNotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            log_error(logger, {
                "context": "options_routes",
                "type": type(exc).__name__,
                "error": str(exc),
                "note": f"smart-routed spread failed for user={user['_id']}",
            })
            raise HTTPException(status_code=502, detail="Broker spread routing failed")
    else:
        try:
            order = await adapter.place_option_order(
                [leg for _, leg in occ_legs],
                order_type=body.order_type,
                time_in_force=body.time_in_force,
                limit_price=body.limit_price,
            )
        except BrokerNotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, NotImplementedError) as exc:
            # Adapter-level validation (leg count, ratio mismatch) or
            # broker doesn't support multi-leg yet.
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            log_error(logger, {
                "context": "options_routes",
                "type": type(exc).__name__,
                "error": str(exc),
                "note": f"spread order failed for user={user['_id']} provider={provider}",
            })
            raise HTTPException(status_code=502, detail="Broker spread order failed")

    legs_response = [
        {
            "occ_symbol": occ,
            "qty": leg.qty,
            "side": leg.side.value,
            "strike": body.legs[i].strike,
            "expiry": body.legs[i].expiry,
            "option_type": body.legs[i].option_type,
        }
        for i, (occ, leg) in enumerate(occ_legs)
    ]

    winning_provider = smart_decision.provider if smart_decision else provider
    routing_mode = "smart" if smart_decision else "direct"

    await _log_order_audit(
        user=user,
        provider=winning_provider,
        occ_symbol=None,  # spreads have no single OCC
        legs=legs_response,
        order_type=body.order_type,
        time_in_force=body.time_in_force,
        limit_price=body.limit_price,
        order_id=order.order_id,
        status=order.status.value,
        routing_mode=routing_mode,
        is_spread=True,
    )

    response = {
        "order_id": order.order_id,
        "status": order.status.value,
        "qty": order.qty,
        "filled_qty": order.filled_qty,
        "limit_price": order.limit_price,
        "time_in_force": order.time_in_force,
        "created_at": order.created_at.isoformat(),
        "provider": winning_provider,
        "legs": legs_response,
        "underlying": body.underlying.upper(),
        "is_spread": True,
    }
    if smart_decision:
        response["routing"] = {
            "mode": "smart",
            "estimated_spread": smart_decision.estimated_spread,
            "spread_per_leg": smart_decision.spread_per_leg,
            "candidates": [
                {"provider": p, "estimated_spread": s}
                for p, s in smart_decision.all_candidates
            ],
        }
    else:
        response["routing"] = {"mode": "direct"}

    # ── Patent J/K/M/I — post-fill guard audit (spread) ──────────────
    # Notional is the sum of |qty * limit_price * 100| across all legs.
    # The proof chain entity is the order_id so all legs and audit
    # events get correlated under one chain.
    try:
        _spread_notional = sum(
            abs(float(leg.qty)) * float(body.limit_price or 0.0) * 100.0
            for leg in body.legs
        )
        if _spread_notional > 0:
            from services.manual_order_guard import run_manual_order_guard
            from server import db as _server_db
            # Side at the spread level is the "directional intent" —
            # if any leg is a buy, treat as BUY; otherwise SELL. Spreads
            # have no canonical single side, so this is approximation.
            _has_buy = any("buy" in str(leg.side.value).lower() for leg in body.legs)
            await run_manual_order_guard(
                db=_server_db,
                user=user,
                asset_class="options",
                symbol=str(body.underlying),
                side="BUY" if _has_buy else "SELL",
                base_notional=_spread_notional,
                context={
                    "route": "options.place_spread",
                    "underlying": str(body.underlying).upper(),
                    "order_id": order.order_id,
                    "provider": winning_provider,
                    "n_legs": len(body.legs),
                    "phase": "post_fill_audit",
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[manual_guard] options spread audit failed: {e}")

    return response


@router.delete("/order/{order_id}")
async def cancel_order(order_id: str, request: Request):
    """Cancel a pending live options order.

    Mode guard: caller must be in LIVE trading mode (mirrors POST /order).
    """
    from services.trading_mode_guards import require_live_mode
    user = await require_live_mode(request)
    _ensure_odd_accepted(user)
    provider = _user_provider(user)
    try:
        adapter = get_options_adapter(provider)
        ok = await adapter.cancel_option_order(order_id)
        return {"canceled": ok, "order_id": order_id}
    except BrokerNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
