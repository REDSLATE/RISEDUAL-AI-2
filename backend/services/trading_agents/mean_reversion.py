"""Mean Reversion Agent — complements the momentum-bias of the
default paper trader by scanning for extreme deviations from a
20-day moving average and opening snapback paper trades.

Strategy (simple, rule-based — easy to reason about in logs):
  * Scan a curated watchlist of liquid large-caps
  * For each, compute 20d SMA + 20d std-dev of daily closes
  * If last close sits ≥ 2σ AWAY from the SMA, open a paper trade
    in the direction of the mean:
      - Above the mean by 2σ+ → SHORT (expect snapback down)
      - Below the mean by 2σ+ → LONG (expect snapback up)
  * Stop: 1σ beyond entry (tight risk)
  * Target: the SMA itself (≈ 2R reward when entry is at 2σ)

Every decision (trade + skip) is narrated into the agent activity
feed and tagged ``strategy: "mean_reversion"`` on the
``learning_engine_trades`` row so the ML pipeline can segment
performance by strategy.

Schedule: every 15 minutes during market hours (APScheduler job).
"""
from __future__ import annotations

import logging
from typing import Any

from services.trading_agents.base import (
    kill_switch_blocks,
    narrate_scan_skip,
    record_paper_trade,
)

logger = logging.getLogger(__name__)

_STRATEGY = "mean_reversion"
_Z_THRESHOLD = 2.0          # How many sigmas from mean triggers entry
_STOP_SIGMAS = 1.0          # Stop 1σ beyond entry
_POSITION_USD = 1_000.0     # Fixed paper-size per trade
_MIN_PRICE = 5.0            # Avoid penny-stock noise
_WATCHLIST: list[str] = [
    # Liquid large-caps where mean-reversion is a proven edge. Kept
    # small on purpose — rule-based agents shouldn't shotgun the
    # universe. Expand via an admin endpoint later if needed.
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    "AMD", "AVGO", "CRM", "ORCL", "ADBE", "NFLX", "INTC",
    "SPY", "QQQ", "IWM", "DIA",
]


def _compute_zscore(closes: list[float]) -> tuple[float, float, float, float] | None:
    """Return (last_close, sma, std, z_score) or None on bad data."""
    if len(closes) < 21:
        return None
    window = closes[-20:]  # trailing 20 sessions
    try:
        sma = sum(window) / len(window)
        var = sum((c - sma) ** 2 for c in window) / len(window)
        std = var ** 0.5
        last = closes[-1]
        if std <= 0:
            return None
        return last, sma, std, (last - sma) / std
    except Exception:
        return None


async def _fetch_closes(ticker: str) -> list[float]:
    """Get ~30 trailing daily closes via yfinance. Short in-proc call
    — yfinance is the cheapest path and matches what
    ``market_sentiment_service`` already uses."""
    import yfinance as yf
    try:
        data = yf.Ticker(ticker).history(period="45d", interval="1d")
        if data is None or data.empty:
            return []
        return [float(x) for x in data["Close"].tolist() if x and x > 0]
    except Exception as e:
        logger.debug("[mean_rev] fetch failed for %s: %s", ticker, e)
        return []


async def run(db: Any) -> None:
    """APScheduler entry point — scan the watchlist, open at most
    3 paper trades per run (keeps the book from flooding on a
    broad-market selloff).

    Never raises — logged and swallowed. Schedule reliability
    beats tight error handling here.
    """
    if db is None:
        return
    if await kill_switch_blocks():
        return  # kill switch event already narrated at activation time

    opened = 0
    considered = 0
    MAX_OPENS = 3
    for ticker in _WATCHLIST:
        if opened >= MAX_OPENS:
            break
        considered += 1
        try:
            closes = await _fetch_closes(ticker)
            stats = _compute_zscore(closes)
            if stats is None:
                continue
            last, sma, std, z = stats
            if last < _MIN_PRICE:
                continue
            if abs(z) < _Z_THRESHOLD:
                continue

            # Price stretched ABOVE mean → short; BELOW → long.
            if z > 0:
                direction = "SHORT"
                entry = last
                stop = last + std * _STOP_SIGMAS
                target = sma
            else:
                direction = "LONG"
                entry = last
                stop = last - std * _STOP_SIGMAS
                target = sma

            # Rough R validation — if reward < risk, skip. Shouldn't
            # happen at z=2 but guards against a tiny-sigma edge case.
            reward = abs(target - entry)
            risk = abs(entry - stop)
            if risk <= 0 or reward / risk < 1.5:
                continue

            # "why" payload reusing the same structured shape as the
            # ML paper trader so the UI renders it identically.
            why = [
                {"feature": "zscore_20d", "value": round(z, 3),
                 "importance": 0.6, "impact": round(-z * 0.6, 4),
                 "abs_impact": round(abs(z * 0.6), 4),
                 "direction": "bullish" if z < 0 else "bearish"},
                {"feature": "distance_from_sma", "value": round((last - sma) / sma, 4),
                 "importance": 0.4, "impact": round(-(last - sma) / sma * 0.4, 4),
                 "abs_impact": round(abs((last - sma) / sma * 0.4), 4),
                 "direction": "bullish" if last < sma else "bearish"},
            ]
            thesis = (
                f"Price {z:+.2f}σ from 20d SMA ${sma:.2f}. "
                f"Targeting snapback to mean."
            )
            trade_id = await record_paper_trade(
                db=db,
                strategy=_STRATEGY,
                ticker=ticker,
                direction=direction,
                entry=entry,
                stop_loss=stop,
                target=target,
                position_usd=_POSITION_USD,
                confidence=min(0.5 + abs(z) * 0.1, 0.85),  # more stretch → more conviction, capped
                thesis=thesis,
                why=why,
                extra_metadata={"zscore": round(z, 3), "sma": round(sma, 4),
                                "std": round(std, 4)},
            )
            if trade_id:
                opened += 1
        except Exception as e:
            logger.warning("[mean_rev] per-ticker error on %s: %s", ticker, e)

    if opened == 0 and considered > 0:
        await narrate_scan_skip(
            _STRATEGY,
            "no 2σ dislocations in watchlist",
            count_considered=considered,
        )
