"""Paper Trading Service — simulated portfolio with real market prices."""
import logging
from datetime import datetime, timezone
from typing import Optional, Any
from services.price_provider import get_quote, get_crypto_quote

logger = logging.getLogger(__name__)

_db: Any = None
STARTING_CASH = 100_000.0
CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK"}


def set_db(database: Any) -> None:
    global _db
    _db = database


async def _get_live_price(symbol: str) -> Optional[float]:
    """Get live price for a symbol (stock or crypto)."""
    sym = symbol.upper()
    if sym in CRYPTO_TICKERS:
        q = await get_crypto_quote(sym)
    else:
        q = await get_quote(sym)
    if q and q.get("price"):
        try:
            return float(q["price"])
        except (ValueError, TypeError):
            pass
    return None


async def get_or_create_portfolio(user_id: str) -> dict:
    """Get existing portfolio or create a new one with starting cash."""
    doc = await _db.paper_portfolios.find_one({"user_id": user_id}, {"_id": 0})
    if doc:
        return doc
    new_portfolio = {
        "user_id": user_id,
        "cash": STARTING_CASH,
        "positions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await _db.paper_portfolios.insert_one(new_portfolio)
    return {k: v for k, v in new_portfolio.items() if k != "_id"}


async def get_portfolio_snapshot(user_id: str) -> dict:
    """Get portfolio with live market values and P&L."""
    portfolio = await get_or_create_portfolio(user_id)
    positions_enriched = []
    total_market_value = 0.0
    total_cost_basis = 0.0

    for pos in portfolio.get("positions", []):
        symbol = pos["symbol"]
        qty = pos["qty"]
        avg_cost = pos["avg_cost"]
        cost_basis = qty * avg_cost

        live_price = await _get_live_price(symbol)
        if live_price is None:
            live_price = avg_cost  # fallback

        market_value = qty * live_price
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = ((live_price / avg_cost) - 1) * 100 if avg_cost > 0 else 0

        positions_enriched.append({
            "symbol": symbol,
            "qty": qty,
            "avg_cost": round(avg_cost, 4),
            "live_price": round(live_price, 4),
            "market_value": round(market_value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        })
        total_market_value += market_value
        total_cost_basis += cost_basis

    cash = portfolio.get("cash", STARTING_CASH)
    equity = cash + total_market_value
    total_pnl = equity - STARTING_CASH

    return {
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "total_market_value": round(total_market_value, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / STARTING_CASH) * 100, 2),
        "positions": positions_enriched,
        "position_count": len(positions_enriched),
        "starting_cash": STARTING_CASH,
        "updated_at": portfolio.get("updated_at"),
    }


async def execute_trade(
    user_id: str,
    symbol: str,
    side: str,
    qty: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> dict:
    """Execute a paper trade (BUY or SELL).

    Returns a dict with an explicit `status` field on every path:
      - "filled"   → trade accepted, cash/positions updated
      - "rejected" → validation (invalid side, insufficient cash, no
                     position to sell, price lookup failed)

    Downstream callers (signal dispatcher, conviction tagging) MUST gate
    on `status == "filled"` before attributing PnL or logging outcomes.
    Previously rejections returned `{"error": "..."}` without status,
    which let the calling loop misclassify a rejection as a bad trade.

    `stop_loss` / `take_profit` are optional. When provided on a BUY,
    they're stamped on the resulting position so a later SELL can compute
    `r_multiple = (exit - entry) / abs(entry - stop_loss)` — an
    expectancy-ready metric. When the position already has an SL (from a
    prior BUY with stop_loss set), the SELL trade record picks it up
    automatically; passing SL again on SELL is ignored.
    """
    symbol = symbol.upper()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"status": "rejected", "error": "Side must be BUY or SELL"}
    if qty <= 0:
        return {"status": "rejected", "error": "Quantity must be positive"}

    live_price = await _get_live_price(symbol)
    if live_price is None:
        return {"status": "rejected", "error": f"Cannot get live price for {symbol}"}

    portfolio = await get_or_create_portfolio(user_id)
    cash = portfolio["cash"]
    positions = portfolio.get("positions", [])

    # Find existing position
    pos_idx = next((i for i, p in enumerate(positions) if p["symbol"] == symbol), None)

    # Pulled out so both BUY and SELL branches can read it for the trade
    # record. On BUY, we persist the supplied SL alongside the position
    # so SELL can compute r_multiple without the caller having to
    # re-plumb it. On SELL we prefer the SL stored on the position.
    trade_sl: Optional[float] = None
    trade_tp: Optional[float] = None
    trade_risk_per_share: Optional[float] = None
    trade_r_multiple: Optional[float] = None
    entry_avg_for_exit: Optional[float] = None

    if side == "BUY":
        total_cost = live_price * qty
        if total_cost > cash:
            return {
                "status": "rejected",
                "error": f"Insufficient cash. Need ${total_cost:.2f}, have ${cash:.2f}",
            }
        cash -= total_cost
        if pos_idx is not None:
            old = positions[pos_idx]
            new_qty = old["qty"] + qty
            new_avg = ((old["avg_cost"] * old["qty"]) + (live_price * qty)) / new_qty
            # Preserve the earlier SL/TP unless explicit fresh ones are
            # provided on this top-up. Treating SL as "first write wins
            # unless overridden" matches how traders think about
            # averaging-up — SL was set on the original thesis.
            merged = {
                "symbol": symbol,
                "qty": new_qty,
                "avg_cost": new_avg,
                "stop_loss": stop_loss if stop_loss is not None else old.get("stop_loss"),
                "take_profit": take_profit if take_profit is not None else old.get("take_profit"),
            }
            positions[pos_idx] = merged
        else:
            positions.append({
                "symbol": symbol,
                "qty": qty,
                "avg_cost": live_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            })
        trade_sl = stop_loss if stop_loss is not None else (
            positions[pos_idx].get("stop_loss") if pos_idx is not None else None
        )
        trade_tp = take_profit if take_profit is not None else (
            positions[pos_idx].get("take_profit") if pos_idx is not None else None
        )
        if trade_sl is not None:
            trade_risk_per_share = abs(live_price - float(trade_sl))

    elif side == "SELL":
        if pos_idx is None:
            return {"status": "rejected", "error": f"No position in {symbol} to sell"}
        old = positions[pos_idx]
        if qty > old["qty"]:
            return {
                "status": "rejected",
                "error": f"Cannot sell {qty} shares. Only hold {old['qty']}",
            }
        proceeds = live_price * qty
        cash += proceeds
        entry_avg_for_exit = old["avg_cost"]
        trade_sl = old.get("stop_loss")
        trade_tp = old.get("take_profit")
        if trade_sl is not None and entry_avg_for_exit:
            risk_per_share = abs(float(entry_avg_for_exit) - float(trade_sl))
            if risk_per_share > 0:
                trade_risk_per_share = risk_per_share
                # R-multiple: positive = win above 1R, negative = loss
                # below -1R. Works identically for longs; would need a
                # sign flip for dedicated short positions, which this
                # service doesn't model yet.
                trade_r_multiple = round(
                    (live_price - float(entry_avg_for_exit)) / risk_per_share, 3
                )
        new_qty = old["qty"] - qty
        if new_qty < 0.0001:  # effectively zero
            positions.pop(pos_idx)
        else:
            # Preserve SL/TP on partial sells so a later residual SELL
            # still produces a valid r_multiple.
            positions[pos_idx] = {
                "symbol": symbol,
                "qty": new_qty,
                "avg_cost": old["avg_cost"],
                "stop_loss": old.get("stop_loss"),
                "take_profit": old.get("take_profit"),
            }

    now = datetime.now(timezone.utc).isoformat()
    await _db.paper_portfolios.update_one(
        {"user_id": user_id},
        {"$set": {"cash": cash, "positions": positions, "updated_at": now}}
    )

    # Record trade — only non-None SL/TP/risk/r_multiple land on the
    # document so legacy analytics scripts don't see a flood of nulls.
    # `opened_at` is a BSON-date duplicate of `timestamp` so the Tier 3
    # distinct-day aggregator (services.paper_trading_progress) counts
    # these manual-UI rows too. Keep `timestamp` as the canonical
    # ISO-string field for backwards compatibility with legacy readers.
    now_dt = datetime.now(timezone.utc)
    trade_record = {
        "user_id": user_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": round(live_price, 4),
        "total": round(live_price * qty, 2),
        "timestamp": now,
        "opened_at": now_dt,
    }
    if trade_sl is not None:
        trade_record["stop_loss"] = float(trade_sl)
    if trade_tp is not None:
        trade_record["take_profit"] = float(trade_tp)
    if trade_risk_per_share is not None:
        trade_record["risk_per_share"] = round(trade_risk_per_share, 4)
    if trade_r_multiple is not None:
        trade_record["r_multiple"] = trade_r_multiple
    if entry_avg_for_exit is not None:
        trade_record["entry_price"] = round(float(entry_avg_for_exit), 4)

    # Operator trading gate — single doctrine rule.
    try:
        from services.operator_trading_gate import gate_or_synthetic
        if not await gate_or_synthetic(
            _db,
            lane="equity_paper",
            symbol=symbol,
            intended_decision=str(side or "").upper(),
            confidence=float(trade_record.get("confidence") or 0.0),
            extras={
                "qty": qty, "price": float(live_price),
                "side": side,
            },
        ):
            return {
                "status": "paused_by_operator",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": round(live_price, 4),
                "total": round(live_price * qty, 2),
                "cash_remaining": round(cash, 2),
                "timestamp": now,
                "message": (
                    "OPERATOR_TRADING_AUTHORIZATION is OFF — synthetic "
                    "ADL receipt was written so MLs can still learn."
                ),
            }
    except Exception:  # noqa: BLE001
        pass

    await _db.paper_trades.insert_one(trade_record)

    response = {
        "status": "filled",
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": round(live_price, 4),
        "total": round(live_price * qty, 2),
        "cash_remaining": round(cash, 2),
        "timestamp": now,
    }
    if trade_r_multiple is not None:
        response["r_multiple"] = trade_r_multiple
    if trade_risk_per_share is not None:
        response["risk_per_share"] = round(trade_risk_per_share, 4)
    return response


async def get_trade_history(user_id: str, symbol: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Get trade history for a user."""
    query = {"user_id": user_id}
    if symbol:
        query["symbol"] = symbol.upper()
    cursor = _db.paper_trades.find(query, {"_id": 0}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def reset_portfolio(user_id: str) -> dict:
    """Reset portfolio to starting state."""
    now = datetime.now(timezone.utc).isoformat()
    await _db.paper_portfolios.update_one(
        {"user_id": user_id},
        {"$set": {"cash": STARTING_CASH, "positions": [], "updated_at": now}}
    )
    await _db.paper_trades.delete_many({"user_id": user_id})
    return {"status": "reset", "cash": STARTING_CASH, "positions": [], "timestamp": now}


async def get_portfolio_context(user_id: str) -> str:
    """Generate formatted portfolio context string for AI injection."""
    try:
        snap = await get_portfolio_snapshot(user_id)
        lines = [
            "=== USER'S PAPER TRADING PORTFOLIO ===",
            f"Cash: ${snap['cash']:,.2f}",
            f"Total Equity: ${snap['equity']:,.2f}",
            f"Total P&L: ${snap['total_pnl']:+,.2f} ({snap['total_pnl_pct']:+.2f}%)",
            f"Positions ({snap['position_count']}):",
        ]
        for p in snap["positions"]:
            lines.append(
                f"  {p['symbol']}: {p['qty']} shares @ ${p['avg_cost']:.2f} avg | "
                f"Now ${p['live_price']:.2f} | "
                f"P&L: ${p['unrealized_pnl']:+,.2f} ({p['unrealized_pnl_pct']:+.1f}%)"
            )
        if not snap["positions"]:
            lines.append("  (No open positions)")

        # Recent trades
        trades = await get_trade_history(user_id, limit=5)
        if trades:
            lines.append("\nRecent Trades:")
            for t in trades:
                lines.append(f"  {t['side']} {t['qty']} {t['symbol']} @ ${t['price']:.2f} ({t['timestamp'][:10]})")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error getting portfolio context: {e}")
        return ""


# ── Confirmation-Gated Paper Order Flow ──────────────────────────────

async def place_paper_order_intent(
    user_id: str, symbol: str, side: str, qty: float,
    order_type: str = "MARKET", limit_price: float = None
) -> dict:
    """Create a pending order proposal. Does NOT execute — requires confirmation."""
    symbol = symbol.upper()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"error": "Side must be BUY or SELL"}
    if qty <= 0:
        return {"error": "Quantity must be positive"}
    if order_type == "LIMIT" and limit_price is None:
        return {"error": "limitPrice required for LIMIT orders"}

    live_price = await _get_live_price(symbol)
    if live_price is None:
        return {"error": f"Cannot get live price for {symbol}"}

    # Validate cash/position before creating proposal
    portfolio = await get_or_create_portfolio(user_id)
    exec_price = limit_price if order_type == "LIMIT" else live_price

    if side == "BUY":
        total_cost = exec_price * qty
        if total_cost > portfolio["cash"]:
            return {"error": f"Insufficient cash. Need ${total_cost:,.2f}, have ${portfolio['cash']:,.2f}"}
    elif side == "SELL":
        pos = next((p for p in portfolio.get("positions", []) if p["symbol"] == symbol), None)
        if not pos:
            return {"error": f"No position in {symbol} to sell"}
        if qty > pos["qty"]:
            return {"error": f"Cannot sell {qty}. Only hold {pos['qty']}"}

    import uuid
    proposal_id = f"po_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    proposal = {
        "proposal_id": proposal_id,
        "user_id": user_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "order_type": order_type,
        "limit_price": limit_price,
        "estimated_price": round(live_price, 4),
        "estimated_total": round(exec_price * qty, 2),
        "status": "PENDING_CONFIRMATION",
        "created_at": now,
    }
    await _db.pending_orders.insert_one(proposal)

    return {
        "message": "Paper order proposal created. Ask the user to confirm before execution.",
        "proposal": {k: v for k, v in proposal.items() if k != "_id"},
        "next_step": f"Ask the user to confirm proposal {proposal_id}."
    }


async def confirm_paper_order(user_id: str, proposal_id: str) -> dict:
    """Confirm and execute a pending paper order proposal."""
    proposal = await _db.pending_orders.find_one(
        {"proposal_id": proposal_id, "user_id": user_id},
        {"_id": 0}
    )
    if not proposal:
        return {"error": f"Proposal {proposal_id} not found"}
    if proposal["status"] != "PENDING_CONFIRMATION":
        return {"error": f"Proposal status is {proposal['status']}, cannot confirm"}

    # Execute the actual trade
    result = await execute_trade(
        user_id, proposal["symbol"], proposal["side"], proposal["qty"]
    )
    if "error" in result:
        return result

    # Mark proposal as confirmed
    await _db.pending_orders.update_one(
        {"proposal_id": proposal_id},
        {"$set": {"status": "CONFIRMED", "confirmed_at": datetime.now(timezone.utc).isoformat()}}
    )

    return {
        "message": "Paper order confirmed and executed.",
        "executed_trade": result,
        "proposal_id": proposal_id,
    }


async def cancel_paper_order(user_id: str, proposal_id: str) -> dict:
    """Cancel a pending paper order proposal."""
    result = await _db.pending_orders.update_one(
        {"proposal_id": proposal_id, "user_id": user_id, "status": "PENDING_CONFIRMATION"},
        {"$set": {"status": "CANCELLED", "cancelled_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.modified_count == 0:
        return {"error": f"Proposal {proposal_id} not found or already processed"}
    return {"message": f"Proposal {proposal_id} cancelled.", "status": "CANCELLED"}


async def get_pending_orders(user_id: str) -> list[dict]:
    """Get all pending order proposals for a user."""
    cursor = _db.pending_orders.find(
        {"user_id": user_id, "status": "PENDING_CONFIRMATION"},
        {"_id": 0}
    ).sort("created_at", -1)
    return await cursor.to_list(length=20)
