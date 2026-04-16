"""Paper Trader (Tier 2) — auto-places paper trades from ML signals.

Activated when CalibrationGate.is_paper_trade_ready() returns True.
Creates paper trade records in MongoDB using Kelly position sizing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from risedual_core.ml.calibration_gate import is_paper_trade_ready, kelly_fraction
from risedual_core.schemas.market import BacktestResult, CalibrationStats, SignalResult

logger = logging.getLogger(__name__)
PAPER_TRADES_COLLECTION = "ml_paper_trades"
MIN_SIGNAL_CONFIDENCE = 0.65
DEFAULT_PORTFOLIO_VALUE = 100_000.0


async def maybe_paper_trade(
    signal: SignalResult,
    stats: CalibrationStats,
    backtest: BacktestResult,
    db: AsyncIOMotorDatabase,
    portfolio_value: float = DEFAULT_PORTFOLIO_VALUE,
) -> dict[str, Any] | None:
    """Check Tier 2 gate and place a paper trade if warranted."""
    if not is_paper_trade_ready(stats, backtest):
        return None

    if signal.confidence < MIN_SIGNAL_CONFIDENCE:
        return None

    # Position sizing via half-Kelly
    kelly = kelly_fraction(signal.confidence)
    dollar_amount = portfolio_value * kelly
    assert 0.0 <= kelly <= 0.25, f"Kelly fraction out of bounds: {kelly}"

    if signal.price is None or signal.price <= 0:
        return None

    shares = int(dollar_amount / signal.price) if signal.price > 0 else 0
    if shares == 0:
        return None

    trade_doc = {
        "ticker": signal.ticker,
        "direction": signal.direction.value,
        "confidence": signal.confidence,
        "kelly_fraction": kelly,
        "dollar_amount": round(dollar_amount, 2),
        "shares": shares,
        "entry_price": signal.price,
        "exit_price": None,
        "pnl": None,
        "regime": signal.regime,
        "patterns": signal.patterns_detected,
        "model_version": signal.model_version,
        "status": "open",
        "opened_at": datetime.now(timezone.utc),
        "closed_at": None,
        "tier": 2,
    }

    try:
        result = await db[PAPER_TRADES_COLLECTION].insert_one(trade_doc)
        logger.info(
            "ML Paper Trade: %s %s %d shares @ $%.2f (Kelly=%.2f, $%.0f)",
            signal.direction.value.upper(), signal.ticker,
            shares, signal.price, kelly, dollar_amount,
        )
        trade_doc["_id"] = str(result.inserted_id)
        return trade_doc
    except Exception:
        logger.exception("Failed to place paper trade for %s", signal.ticker)
        return None


async def close_paper_trade(
    trade_id: str,
    exit_price: float,
    db: AsyncIOMotorDatabase,
) -> dict | None:
    """Close an open paper trade and compute P&L."""
    from bson import ObjectId
    trade = await db[PAPER_TRADES_COLLECTION].find_one({"_id": ObjectId(trade_id)})
    if not trade or trade["status"] != "open":
        return None

    entry = trade["entry_price"]
    direction = trade["direction"]
    shares = trade["shares"]

    if direction == "up":
        pnl = (exit_price - entry) * shares
    else:
        pnl = (entry - exit_price) * shares

    now = datetime.now(timezone.utc)
    await db[PAPER_TRADES_COLLECTION].update_one(
        {"_id": ObjectId(trade_id)},
        {"$set": {"exit_price": exit_price, "pnl": round(pnl, 2),
                  "status": "closed", "closed_at": now}},
    )
    logger.info("Paper trade closed: %s P&L=$%.2f", trade["ticker"], pnl)
    return {"trade_id": trade_id, "pnl": round(pnl, 2), "closed_at": now.isoformat()}
