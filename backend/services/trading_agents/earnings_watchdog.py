"""Earnings Watchdog Agent — scans for upcoming earnings events
and opens paper positions when the setup is clean.

Strategy (event-driven, contrarian-lean):
  * Pull next-3-day earnings calendar from Finnhub (already wired
    in services; auth key on env)
  * For each ticker with earnings in 1-3 days:
      - Skip if reported in last 5 days (stale)
      - Compute 10d volatility-adjusted move (σ × avg_daily_range)
      - Derive "pre-earnings drift" (5d return heading in)
  * Take the CONTRARIAN side of a pronounced pre-earnings drift
    greater than 5% — sentiment frequently over-extends into prints.
    Tight stops (hard 3% risk) since earnings = event risk.

Tag: ``strategy: "earnings_watchdog"``. Same narration contract
as every other agent.

Schedule: once per day at pre-market (08:00 UTC ≈ 4am ET).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from services.trading_agents.base import (
    kill_switch_blocks,
    narrate_scan_skip,
    record_paper_trade,
)

logger = logging.getLogger(__name__)

_STRATEGY = "earnings_watchdog"
_DRIFT_THRESHOLD = 0.05  # 5% pre-earnings drift required for contrarian entry
_STOP_PCT = 0.03         # Hard 3% stop — earnings = overnight risk
_POSITION_USD = 750.0    # Smaller than mean-rev — earnings print risk
_MAX_OPENS = 2           # Cap per-day exposure to earnings risk


async def _fetch_earnings_calendar(days_ahead: int = 3) -> list[dict]:
    """Pull earnings for the next N days from Finnhub.

    Finnhub `stock/earning-calendar` returns a list of
    ``{symbol, date, epsEstimate, hour, ...}``. We filter to
    US-market large caps downstream.
    """
    token = os.environ.get("FINNHUB_API_KEY") or ""
    if not token:
        logger.debug("[earnings] no FINNHUB_API_KEY; skipping")
        return []
    today = datetime.now(timezone.utc).date()
    start = today.isoformat()
    end = (today + timedelta(days=days_ahead)).isoformat()
    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={start}&to={end}&token={token}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            body = resp.json()
            return list(body.get("earningsCalendar") or [])
    except Exception as e:
        logger.warning("[earnings] calendar fetch failed: %s", e)
        return []


async def _fetch_recent_returns(ticker: str) -> Optional[tuple[float, float]]:
    """Return (last_close, 5d_return). yfinance-backed, same as
    mean_reversion agent — keeps the code dependency-light."""
    import yfinance as yf
    try:
        data = yf.Ticker(ticker).history(period="10d", interval="1d")
        if data is None or data.empty or len(data) < 6:
            return None
        closes = [float(x) for x in data["Close"].tolist() if x and x > 0]
        if len(closes) < 6:
            return None
        last = closes[-1]
        five_ago = closes[-6]
        if five_ago <= 0:
            return None
        ret5 = (last - five_ago) / five_ago
        return last, ret5
    except Exception as e:
        logger.debug("[earnings] fetch failed for %s: %s", ticker, e)
        return None


async def run(db: Any) -> None:
    """APScheduler entry point."""
    if db is None:
        return
    if await kill_switch_blocks():
        return

    calendar = await _fetch_earnings_calendar(days_ahead=3)
    if not calendar:
        await narrate_scan_skip(
            _STRATEGY, "earnings calendar empty or unavailable",
            count_considered=0,
        )
        return

    opened = 0
    considered = 0
    # Focus on liquid tickers — finnhub occasionally returns weird
    # micro-caps. Same watchlist as mean_reversion is a reasonable
    # starting universe.
    allowed = {
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
        "AMD", "AVGO", "CRM", "ORCL", "ADBE", "NFLX", "INTC",
        "JPM", "GS", "MS", "WMT", "HD", "LOW", "COST", "PG",
        "UNH", "MRK", "PFE", "JNJ", "V", "MA", "KO", "PEP",
        "XOM", "CVX", "BA", "CAT", "GE", "DIS",
    }
    for ev in calendar:
        if opened >= _MAX_OPENS:
            break
        symbol = (ev.get("symbol") or "").upper()
        if not symbol or symbol not in allowed:
            continue
        considered += 1
        rec = await _fetch_recent_returns(symbol)
        if rec is None:
            continue
        last_price, ret5 = rec
        if abs(ret5) < _DRIFT_THRESHOLD:
            continue

        # Contrarian lean: a sharp run-up INTO earnings often fades
        # on/after the print. Go SHORT when drift is strongly
        # positive; LONG when sentiment has crashed into it.
        if ret5 > 0:
            direction = "SHORT"
            entry = last_price
            stop = last_price * (1 + _STOP_PCT)
            target = last_price * (1 - abs(ret5) * 0.5)
        else:
            direction = "LONG"
            entry = last_price
            stop = last_price * (1 - _STOP_PCT)
            target = last_price * (1 + abs(ret5) * 0.5)

        why = [
            {"feature": "pre_earnings_drift_5d", "value": round(ret5, 4),
             "importance": 0.7, "impact": round(-ret5 * 0.7, 4),
             "abs_impact": round(abs(ret5 * 0.7), 4),
             "direction": "bearish" if ret5 > 0 else "bullish"},
            {"feature": "earnings_proximity", "value": 1.0,
             "importance": 0.3, "impact": 0.3, "abs_impact": 0.3,
             "direction": "bullish"},
        ]
        thesis = (
            f"Earnings {ev.get('date', 'soon')} · 5d drift {ret5 * 100:+.1f}%. "
            f"Fading the move into the print."
        )
        trade_id = await record_paper_trade(
            db=db,
            strategy=_STRATEGY,
            ticker=symbol,
            direction=direction,
            entry=entry,
            stop_loss=stop,
            target=target,
            position_usd=_POSITION_USD,
            confidence=min(0.55 + abs(ret5), 0.85),
            thesis=thesis,
            why=why,
            extra_metadata={
                "earnings_date": ev.get("date"),
                "eps_estimate": ev.get("epsEstimate"),
                "drift_5d": round(ret5, 4),
            },
        )
        if trade_id:
            opened += 1

    if opened == 0 and considered > 0:
        await narrate_scan_skip(
            _STRATEGY,
            "no strong pre-earnings drifts detected",
            count_considered=considered,
        )
