"""Paper Trading Service — simulated portfolio with real market prices."""
import logging
from datetime import datetime, timezone
from typing import Optional
from services.price_provider import get_quote, get_crypto_quote

logger = logging.getLogger(__name__)

_db = None
STARTING_CASH = 100_000.0
CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK"}


def set_db(database):
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


async def execute_trade(user_id: str, symbol: str, side: str, qty: float) -> dict:
    """Execute a paper trade (BUY or SELL)."""
    symbol = symbol.upper()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"error": "Side must be BUY or SELL"}
    if qty <= 0:
        return {"error": "Quantity must be positive"}

    live_price = await _get_live_price(symbol)
    if live_price is None:
        return {"error": f"Cannot get live price for {symbol}"}

    portfolio = await get_or_create_portfolio(user_id)
    cash = portfolio["cash"]
    positions = portfolio.get("positions", [])

    # Find existing position
    pos_idx = next((i for i, p in enumerate(positions) if p["symbol"] == symbol), None)

    if side == "BUY":
        total_cost = live_price * qty
        if total_cost > cash:
            return {"error": f"Insufficient cash. Need ${total_cost:.2f}, have ${cash:.2f}"}
        cash -= total_cost
        if pos_idx is not None:
            old = positions[pos_idx]
            new_qty = old["qty"] + qty
            new_avg = ((old["avg_cost"] * old["qty"]) + (live_price * qty)) / new_qty
            positions[pos_idx] = {"symbol": symbol, "qty": new_qty, "avg_cost": new_avg}
        else:
            positions.append({"symbol": symbol, "qty": qty, "avg_cost": live_price})

    elif side == "SELL":
        if pos_idx is None:
            return {"error": f"No position in {symbol} to sell"}
        old = positions[pos_idx]
        if qty > old["qty"]:
            return {"error": f"Cannot sell {qty} shares. Only hold {old['qty']}"}
        proceeds = live_price * qty
        cash += proceeds
        new_qty = old["qty"] - qty
        if new_qty < 0.0001:  # effectively zero
            positions.pop(pos_idx)
        else:
            positions[pos_idx] = {"symbol": symbol, "qty": new_qty, "avg_cost": old["avg_cost"]}

    now = datetime.now(timezone.utc).isoformat()
    await _db.paper_portfolios.update_one(
        {"user_id": user_id},
        {"$set": {"cash": cash, "positions": positions, "updated_at": now}}
    )

    # Record trade
    trade_record = {
        "user_id": user_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": round(live_price, 4),
        "total": round(live_price * qty, 2),
        "timestamp": now,
    }
    await _db.paper_trades.insert_one(trade_record)

    return {
        "status": "filled",
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": round(live_price, 4),
        "total": round(live_price * qty, 2),
        "cash_remaining": round(cash, 2),
        "timestamp": now,
    }


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
