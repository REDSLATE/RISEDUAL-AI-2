"""Paper Options Trading — simulated options orders for the Radar/Flow
scanners.

Shares the `paper_portfolios` cash balance with the equity paper
service (one wallet per user) but stores option legs on the same
portfolio document under a separate `option_positions` list. That
way a user's paper cash is a single source of truth but options vs
equities stay discriminable in the UI + analytics.

A "leg" is stored as:
    {
      "symbol": "ASHR",          # underlying
      "strike": 38.0,
      "expiry": "2026-06-18",    # ISO date
      "option_type": "call",     # "call" | "put"
      "qty": 1,                  # contracts (each = 100 shares)
      "avg_fill": 0.23,          # per-contract entry price (per share)
      "opened_at": <isoformat>,
    }

Contract economics: one options contract controls 100 shares of the
underlying. Cash flows are `price × qty × 100`. We don't simulate
exercise/assignment — expiry simply leaves the leg on the books with
intrinsic value at settlement, and users can manually close via
`sell_to_close` at any time.

Side vocabulary (OCC-standard):
  * `buy_to_open`   — new long leg, debit the account
  * `sell_to_close` — close an existing long leg, credit proceeds
  * `sell_to_open`  — new short leg, credit premium (NOT supported
                      in this minimal plumbing; short options need
                      margin tracking)
  * `buy_to_close`  — close an existing short leg (same — deferred)

For now we support BTO + STC only. That covers 100% of the Buy/Sell
intent the scanner buttons represent (a "Buy" on a Call row is
BTO on that call; "Sell" on a held leg is STC).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ai_core.options_pricing import estimate_fill_price
from services.price_provider import get_quote
from services.paper_trading_service import get_or_create_portfolio

logger = logging.getLogger(__name__)

# One contract = 100 shares of the underlying. OCC standard; no asset
# class in our scope uses a different multiplier.
CONTRACT_MULTIPLIER = 100

_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _leg_key(symbol: str, strike: float, expiry: str, option_type: str) -> tuple:
    """Normalized leg identity. Two legs with the same
    `(symbol, strike, expiry, option_type)` tuple are the same
    contract and must stack on open / close."""
    return (symbol.upper(), round(float(strike), 4), expiry, option_type.lower())


def _find_leg_index(legs: list[dict], key: tuple) -> Optional[int]:
    for i, leg in enumerate(legs):
        if _leg_key(
            leg["symbol"], leg["strike"], leg["expiry"], leg["option_type"]
        ) == key:
            return i
    return None


async def _get_underlying_price(symbol: str) -> Optional[float]:
    """Delegate to the existing stock quote provider."""
    quote = await get_quote(symbol)
    if quote and quote.get("price") is not None:
        try:
            return float(quote["price"])
        except (ValueError, TypeError):
            return None
    return None


async def execute_option_trade(
    user_id: str,
    symbol: str,
    strike: float,
    expiry: str,
    option_type: str,
    side: str,
    qty: int,
    iv_percent: Optional[float] = None,
) -> dict:
    """Execute a paper options trade.

    Returns a status-tagged dict identical in shape to the equity
    path (`status: "filled" | "rejected"` + details) so downstream
    UI can treat both asset classes uniformly.

    Contract validation:
      * qty must be a positive integer (fractional contracts don't exist)
      * option_type in {"call","put"}
      * side in {"buy_to_open","sell_to_close"}
      * BTO debits `fill × qty × 100` from cash
      * STC requires an existing leg of matching
        `(symbol, strike, expiry, type)` with at least `qty` contracts
    """
    # ── Normalise + validate ───────────────────────────────────────
    symbol = symbol.upper().strip()
    option_type = option_type.lower().strip()
    side = side.lower().strip()
    if option_type not in ("call", "put"):
        return {"status": "rejected", "error": "option_type must be 'call' or 'put'"}
    if side not in ("buy_to_open", "sell_to_close"):
        return {
            "status": "rejected",
            "error": "side must be 'buy_to_open' or 'sell_to_close' (short legs not yet supported)",
        }
    if not isinstance(qty, int) or qty <= 0:
        return {"status": "rejected", "error": "qty must be a positive integer (contracts)"}

    try:
        strike = float(strike)
        if strike <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return {"status": "rejected", "error": "strike must be a positive number"}

    # ── Price the contract via Black-Scholes ────────────────────────
    underlying = await _get_underlying_price(symbol)
    if underlying is None:
        return {
            "status": "rejected",
            "error": f"Cannot get underlying price for {symbol}",
        }
    fill_price_per_share = estimate_fill_price(
        underlying_price=underlying,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        iv_percent=iv_percent,
        # BS fills reflect the side's direction (buy pays ask,
        # sell hits bid). Map BTO→buy, STC→sell.
        side="buy" if side == "buy_to_open" else "sell",
    )
    contract_cost = fill_price_per_share * CONTRACT_MULTIPLIER * qty

    # ── Load portfolio ──────────────────────────────────────────────
    portfolio = await get_or_create_portfolio(user_id)
    cash: float = float(portfolio.get("cash", 0.0))
    # option_positions lives alongside the equity positions list but
    # in its own key so each asset class can iterate its own set
    # without discriminator checks.
    option_positions: list[dict] = list(portfolio.get("option_positions", []))
    key = _leg_key(symbol, strike, expiry, option_type)
    idx = _find_leg_index(option_positions, key)

    if side == "buy_to_open":
        if contract_cost > cash:
            return {
                "status": "rejected",
                "error": f"Insufficient cash. Need ${contract_cost:.2f}, have ${cash:.2f}",
            }
        cash -= contract_cost
        if idx is not None:
            existing = option_positions[idx]
            new_qty = existing["qty"] + qty
            new_avg = (
                (existing["avg_fill"] * existing["qty"]) + (fill_price_per_share * qty)
            ) / new_qty
            option_positions[idx] = {
                **existing,
                "qty": new_qty,
                "avg_fill": round(new_avg, 4),
            }
        else:
            option_positions.append({
                "symbol": symbol,
                "strike": strike,
                "expiry": expiry,
                "option_type": option_type,
                "qty": qty,
                "avg_fill": fill_price_per_share,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            })

    else:  # sell_to_close
        if idx is None:
            return {
                "status": "rejected",
                "error": f"No open {option_type} position at {symbol} ${strike} {expiry} to close",
            }
        existing = option_positions[idx]
        if qty > existing["qty"]:
            return {
                "status": "rejected",
                "error": f"Cannot close {qty} contracts — only hold {existing['qty']}",
            }
        proceeds = fill_price_per_share * CONTRACT_MULTIPLIER * qty
        cash += proceeds
        new_qty = existing["qty"] - qty
        if new_qty <= 0:
            option_positions.pop(idx)
        else:
            option_positions[idx] = {**existing, "qty": new_qty}

    # ── Persist ─────────────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    await _db.paper_portfolios.update_one(
        {"user_id": user_id},
        {"$set": {
            "cash": round(cash, 2),
            "option_positions": option_positions,
            "updated_at": now_iso,
        }},
    )

    trade_record = {
        "user_id": user_id,
        "asset_class": "option",
        "symbol": symbol,
        "strike": strike,
        "expiry": expiry,
        "option_type": option_type,
        "side": side,
        "qty": qty,
        "fill_price": fill_price_per_share,
        "contract_cost": round(contract_cost, 2),
        "underlying_price": round(underlying, 4),
        "iv_percent": iv_percent,
        "timestamp": now_iso,
        "opened_at": datetime.now(timezone.utc),
    }

    # Operator trading gate — single doctrine rule.
    try:
        from services.operator_trading_gate import gate_or_synthetic
        if not await gate_or_synthetic(
            _db,
            lane="options_paper",
            symbol=symbol,
            intended_decision=f"{side}_{option_type}".upper(),
            confidence=0.0,
            extras={
                "strike": strike, "expiry": expiry,
                "option_type": option_type, "side": side, "qty": qty,
            },
        ):
            return {
                "status": "paused_by_operator",
                "asset_class": "option",
                "symbol": symbol,
                "strike": strike,
                "expiry": expiry,
                "option_type": option_type,
                "side": side,
                "qty": qty,
                "message": (
                    "OPERATOR_TRADING_AUTHORIZATION is OFF — synthetic "
                    "ADL receipt was written so MLs can still learn."
                ),
            }
    except Exception:  # noqa: BLE001
        pass

    await _db.paper_trades.insert_one(trade_record)

    return {
        "status": "filled",
        "asset_class": "option",
        "symbol": symbol,
        "strike": strike,
        "expiry": expiry,
        "option_type": option_type,
        "side": side,
        "qty": qty,
        "fill_price": fill_price_per_share,
        "contract_cost": round(contract_cost, 2),
        "underlying_price": round(underlying, 4),
        "cash_after": round(cash, 2),
    }
