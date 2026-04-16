"""Tier 2 autonomous action: paper trading with Kelly position sizing.

When the CalibrationGate unlocks Tier 2, this service auto-creates paper
trades in MongoDB after every qualifying signal.  No real money is involved.

Architecture
------------
- Writes to the ``paper_trades`` collection in MongoDB.
- Uses half-Kelly sizing (capped at 25% of ``PAPER_PORTFOLIO_VALUE``).
- A separate ``paper_positions`` collection tracks open exposure.
- Designed to be called by :mod:`app.services.ml_orchestrator`.

Gate requirements (Tier 2)
---------------------------
- All Tier 1 gates cleared.
- accuracy   >= 60%
- Sharpe     >= 1.0  (from backtest)
- Max DD     <  15%  (from backtest)

Signal-level requirements
--------------------------
- confidence >= 65%
- At least one pattern detected
- Regime is trending (not sideways/unknown)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from risedual_core.ml.calibration import half_kelly_position
from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

log = logging.getLogger(__name__)

# Paper portfolio starting value (USD)
_PAPER_PORTFOLIO_VALUE: float = float(
    os.getenv("PAPER_PORTFOLIO_VALUE", "100000")
)

# Per-signal confidence threshold above gate accuracy
_MIN_PAPER_CONFIDENCE: float = 0.65

# Regimes eligible for paper trades
_TRADEABLE_REGIMES: frozenset[str] = frozenset({"trending_up", "trending_down"})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detected_patterns(snapshot: FeaturesSnapshot) -> list[str]:
    """Return names of all patterns flagged True on this snapshot."""
    fields = {
        "double_bottom": snapshot.pattern_double_bottom,
        "bullish_engulfing": snapshot.pattern_bullish_engulfing,
        "bearish_engulfing": snapshot.pattern_bearish_engulfing,
        "bull_flag": snapshot.pattern_bull_flag,
        "rsi_divergence": snapshot.pattern_rsi_divergence,
        "macd_crossover": snapshot.pattern_macd_crossover,
        "volume_surge": snapshot.pattern_volume_surge,
        "head_and_shoulders": snapshot.pattern_head_and_shoulders,
    }
    return [name for name, val in fields.items() if val]


async def _current_portfolio_value(db: Any) -> float:
    """Estimate current portfolio value from open positions + base value.

    Simple heuristic: base value minus total open position cost basis.
    Production upgrade: fetch current prices and mark-to-market.
    """
    try:
        positions_coll = db["paper_positions"]
        open_pos = await positions_coll.find({"status": "open"}).to_list(length=500)
        allocated = sum(float(p.get("position_size_usd", 0)) for p in open_pos)
        return max(_PAPER_PORTFOLIO_VALUE - allocated, 0.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_paper] Failed to load positions: %s — using base value.", exc)
        return _PAPER_PORTFOLIO_VALUE


# ── Public API ────────────────────────────────────────────────────────────────


async def maybe_paper_trade(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    regime: str,
    db: Any,
    http_client: httpx.AsyncClient | None = None,  # noqa: ARG001 (reserved)
) -> str | None:
    """Open a paper trade if all Tier 2 signal-level conditions are met.

    Gate-level conditions (accuracy / Sharpe / DD) are verified by the
    orchestrator before this function is called.

    Parameters
    ----------
    ticker:
        Trading symbol, e.g. ``"AAPL"``.
    signal:
        Completed :class:`SignalResult` from the signal model.
    snapshot:
        Enriched :class:`FeaturesSnapshot` (schema_version=2).
    regime:
        Current market regime label.
    db:
        Motor AsyncIOMotorDatabase handle.
    http_client:
        Reserved for future use (e.g., price feed calls).

    Returns
    -------
    str | None
        The ``trade_id`` of the newly opened paper trade, or ``None`` if
        conditions were not met or the write failed.
    """
    # ── Per-signal gate ──────────────────────────────────────────────────────
    if signal.confidence < _MIN_PAPER_CONFIDENCE:
        log.debug(
            "[ml_paper] Confidence %.2f < %.2f — skipping paper trade for %s.",
            signal.confidence,
            _MIN_PAPER_CONFIDENCE,
            ticker,
        )
        return None

    patterns = _detected_patterns(snapshot)
    if not patterns:
        log.debug("[ml_paper] No patterns on %s — skipping paper trade.", ticker)
        return None

    if regime not in _TRADEABLE_REGIMES:
        log.debug(
            "[ml_paper] Regime %r not tradeable — skipping paper trade for %s.",
            regime,
            ticker,
        )
        return None

    # ── Position sizing ──────────────────────────────────────────────────────
    portfolio_value = await _current_portfolio_value(db)
    position_usd = half_kelly_position(
        win_probability=signal.confidence,
        portfolio_value=portfolio_value,
    )

    if position_usd <= 0.0:
        log.debug("[ml_paper] Kelly sizing returned $0 — skipping paper trade for %s.", ticker)
        return None

    # Infer shares from last close price (use ATR proxy if close unavailable)
    entry_price = snapshot.close_price if snapshot.close_price and snapshot.close_price > 0 else None
    shares: float | None = None
    if entry_price:
        shares = round(position_usd / entry_price, 4)

    # ── Persist to MongoDB ───────────────────────────────────────────────────
    trade_id = str(uuid4())
    now = datetime.now(timezone.utc)
    direction_val = str(signal.direction.value)

    trade_doc = {
        "trade_id": trade_id,
        "ticker": ticker,
        "direction": direction_val,
        "confidence": signal.confidence,
        "prediction_id": signal.prediction_id,
        "position_size_usd": position_usd,
        "entry_price": entry_price,
        "shares": shares,
        "patterns": patterns,
        "regime": regime,
        "status": "open",
        "opened_at": now,
        "closed_at": None,
        "exit_price": None,
        "pnl_usd": None,
        "pnl_pct": None,
        "outcome": None,   # filled by labeling pipeline
        "schema_version": 1,
    }

    try:
        await db["paper_trades"].insert_one(trade_doc)
        # Also upsert into positions collection for portfolio tracking
        await db["paper_positions"].update_one(
            {"ticker": ticker, "status": "open"},
            {
                "$setOnInsert": {
                    "ticker": ticker,
                    "trade_id": trade_id,
                    "direction": direction_val,
                    "position_size_usd": position_usd,
                    "entry_price": entry_price,
                    "status": "open",
                    "opened_at": now,
                }
            },
            upsert=True,
        )
        log.info(
            "[ml_paper] Paper trade opened — %s %s $%.2f (conf=%.0f%%, patterns=%s)",
            ticker,
            direction_val.upper(),
            position_usd,
            signal.confidence * 100,
            ", ".join(patterns),
        )
        return trade_id

    except Exception as exc:  # noqa: BLE001
        log.error("[ml_paper] Failed to persist paper trade for %s: %s", ticker, exc)
        return None
