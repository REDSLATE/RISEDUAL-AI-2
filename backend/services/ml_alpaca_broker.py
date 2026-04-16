"""Alpaca Broker (Tier 3) — thin async wrapper for live order execution.

Activated when CalibrationGate.is_live_ready() returns True AND user has
opted in via their account settings.

Position sizing: half-Kelly, hard cap at 2% of portfolio value.
Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in environment.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase
from risedual_core.ml.calibration_gate import is_live_ready, kelly_fraction
from risedual_core.schemas.market import BacktestResult, CalibrationStats, SignalResult

logger = logging.getLogger(__name__)
LIVE_TRADES_COLLECTION = "ml_live_trades"
MIN_SIGNAL_CONFIDENCE = 0.65
MAX_POSITION_PCT = 0.02  # Hard cap: 2% of portfolio

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"  # Switch to live URL for production
ALPACA_DATA_URL = "https://data.alpaca.markets"


class AlpacaBroker:
    """Async wrapper for Alpaca REST API."""

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    async def get_account(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{ALPACA_BASE_URL}/v2/account", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_portfolio_value(self) -> float:
        account = await self.get_account()
        return float(account.get("portfolio_value", 0))

    async def submit_order(
        self, symbol: str, qty: int, side: str, order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{ALPACA_BASE_URL}/v2/orders",
                headers=self._headers(),
                json={
                    "symbol": symbol,
                    "qty": str(qty),
                    "side": side,
                    "type": order_type,
                    "time_in_force": time_in_force,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_position(self, symbol: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{ALPACA_BASE_URL}/v2/positions/{symbol}",
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None


_broker = AlpacaBroker()


async def maybe_live_trade(
    signal: SignalResult,
    stats: CalibrationStats,
    backtest: BacktestResult,
    days_live: int,
    db: AsyncIOMotorDatabase,
    user_opt_in: bool = False,
) -> dict[str, Any] | None:
    """Check Tier 3 gate and submit a live order if all conditions are met."""
    if not user_opt_in:
        return None

    if not _broker.is_configured:
        logger.warning("Alpaca broker not configured (missing API keys)")
        return None

    if not is_live_ready(stats, backtest, days_live):
        return None

    if signal.confidence < MIN_SIGNAL_CONFIDENCE:
        return None

    try:
        portfolio_value = await _broker.get_portfolio_value()
    except Exception as exc:
        logger.error("Failed to get Alpaca portfolio value: %s", exc)
        return None

    # Position sizing: half-Kelly, hard cap at 2%
    kelly = kelly_fraction(signal.confidence)
    dollar_amount = portfolio_value * kelly
    max_dollar = portfolio_value * MAX_POSITION_PCT
    dollar_amount = min(dollar_amount, max_dollar)

    assert dollar_amount <= portfolio_value * MAX_POSITION_PCT, "Position size exceeds 2% cap"

    if signal.price is None or signal.price <= 0 or dollar_amount < signal.price:
        return None

    shares = int(dollar_amount / signal.price)
    if shares == 0:
        return None

    side = "buy" if signal.direction.value == "up" else "sell"

    try:
        order = await _broker.submit_order(signal.ticker, shares, side)
        logger.info(
            "LIVE ORDER: %s %d %s @ ~$%.2f (Kelly=%.2f, $%.0f, portfolio=$%.0f)",
            side.upper(), shares, signal.ticker, signal.price,
            kelly, dollar_amount, portfolio_value,
        )
    except Exception as exc:
        logger.error("Alpaca order failed for %s: %s", signal.ticker, exc)
        return None

    trade_doc = {
        "ticker": signal.ticker,
        "direction": signal.direction.value,
        "side": side,
        "shares": shares,
        "confidence": signal.confidence,
        "kelly_fraction": kelly,
        "dollar_amount": round(dollar_amount, 2),
        "entry_price": signal.price,
        "alpaca_order_id": order.get("id"),
        "alpaca_status": order.get("status"),
        "model_version": signal.model_version,
        "regime": signal.regime,
        "patterns": signal.patterns_detected,
        "status": "submitted",
        "submitted_at": datetime.now(timezone.utc),
        "tier": 3,
    }

    try:
        await db[LIVE_TRADES_COLLECTION].insert_one(trade_doc)
    except Exception:
        logger.exception("Failed to persist live trade record")

    return trade_doc
