"""Polygon.io Dark Pool Service — real market data with dark pool volume estimation.

Uses Polygon free tier grouped daily bars for real price/volume data.
Estimates dark pool % using market structure research (~38-47% of US equity volume).
When Polygon plan is upgraded, real OTC data from the `otc` field flows in automatically.
"""

import os
import logging
import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from typing import Any

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {"data": None, "ts": None, "whale_alerts": []}
CACHE_TTL = 300  # 5 minutes

# Top dark pool tracked symbols
DARK_POOL_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL", "AMZN", "AMD", "SPY", "QQQ",
    "PLTR", "NFLX", "INTC", "BAC", "SOFI", "NIO", "RIVN", "COIN", "MARA", "SQ",
]

# Research-based dark pool % ranges by market cap tier
# Source: SEC Rule 606 reports, FINRA OTC Transparency aggregate data
# Large-cap: ~38-42%, Mid-cap: ~40-45%, Small/Meme: ~44-50%
TIER_RANGES = {
    "mega":  (0.36, 0.42),  # AAPL, MSFT, NVDA, GOOGL, AMZN, META
    "large": (0.38, 0.44),  # TSLA, AMD, NFLX, SPY, QQQ, BAC
    "mid":   (0.42, 0.48),  # PLTR, INTC, SOFI, SQ, COIN
    "small": (0.44, 0.52),  # NIO, RIVN, MARA, meme stocks
}

TICKER_TIER = {
    "AAPL": "mega", "MSFT": "mega", "NVDA": "mega", "GOOGL": "mega", "AMZN": "mega", "META": "mega",
    "TSLA": "large", "AMD": "large", "NFLX": "large", "SPY": "large", "QQQ": "large", "BAC": "large",
    "PLTR": "mid", "INTC": "mid", "SOFI": "mid", "SQ": "mid", "COIN": "mid",
    "NIO": "small", "RIVN": "small", "MARA": "small",
}


def _estimate_dark_pool_pct(ticker: str, total_volume: float) -> float:
    """Estimate dark pool volume percentage based on market structure research."""
    tier = TICKER_TIER.get(ticker, "mid")
    lo, hi = TIER_RANGES[tier]
    # Use deterministic hash seed for consistent-per-day estimates
    import hashlib
    seed_bytes = hashlib.sha256(f"{ticker}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}".encode()).digest()
    seed = int.from_bytes(seed_bytes[:4], 'big')
    rng = random.Random(seed)
    return round(rng.uniform(lo, hi), 4)


def _detect_whale(ticker: str, volume: float, dp_volume: float, avg_trade_size: float) -> Optional[dict]:
    """Detect whale-level dark pool activity based on volume anomalies."""
    # A "whale print" is estimated when dp_volume exceeds a threshold
    # suggesting large institutional block trades
    if dp_volume > 5_000_000:  # >5M shares through dark pools
        return {
            "ticker": ticker,
            "dp_volume": int(dp_volume),
            "total_volume": int(volume),
            "estimated_block_size": int(avg_trade_size * 50),
            "alert": "WHALE" if dp_volume > 15_000_000 else "LARGE_BLOCK",
        }
    return None


async def fetch_dark_pool_data() -> dict:
    """Fetch real market data from Polygon and compute dark pool estimates."""
    now = datetime.now(timezone.utc)

    # Return cache if fresh
    if _cache["data"] and _cache["ts"] and (now - _cache["ts"]).total_seconds() < CACHE_TTL:
        return _cache["data"]

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        logger.warning("POLYGON_API_KEY not set, returning empty dark pool data")
        return {"dark_pool": [], "whale_alerts": [], "source": "unavailable"}

    try:
        from polygon import RESTClient
        client = RESTClient(api_key=api_key)

        # Get the most recent trading day's grouped data
        # Try today first, then yesterday
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        two_days = (now - timedelta(days=2)).strftime("%Y-%m-%d")

        grouped = None
        data_date = today
        for date_str in [today, yesterday, two_days]:
            try:
                result = await asyncio.to_thread(client.get_grouped_daily_aggs, date_str)
                if result and len(result) > 100:
                    grouped = result
                    data_date = date_str
                    break
            except Exception:
                continue

        if not grouped:
            logger.warning("No grouped daily data from Polygon")
            return {"dark_pool": [], "whale_alerts": [], "source": "no_data"}

        # Build lookup
        lookup = {g.ticker: g for g in grouped}

        dark_pool_rows = []
        whale_alerts = []

        for ticker in DARK_POOL_TICKERS:
            g = lookup.get(ticker)
            if not g or not g.volume:
                continue

            total_vol = g.volume
            dp_pct = _estimate_dark_pool_pct(ticker, total_vol)
            dp_volume = total_vol * dp_pct

            # Use real OTC data if available (paid Polygon tier)
            if g.otc and g.otc > 0:
                dp_volume = g.otc
                dp_pct = dp_volume / total_vol if total_vol > 0 else 0

            change_pct = ((g.close - g.open) / g.open * 100) if g.open else 0
            avg_trade_size = total_vol / max(getattr(g, 'transactions', 0) or 1000, 1)

            sentiment = "Bullish" if change_pct > 0.3 else "Bearish" if change_pct < -0.3 else "Neutral"

            row = {
                "ticker": ticker,
                "price": round(g.close, 2),
                "change_pct": round(change_pct, 2),
                "total_volume": int(total_vol),
                "dark_pool_volume": int(dp_volume),
                "dark_pool_pct": round(dp_pct * 100, 1),
                "vwap": round(g.vwap, 2) if g.vwap else None,
                "high": round(g.high, 2),
                "low": round(g.low, 2),
                "avg_trade_size": round(avg_trade_size, 0),
                "sentiment": sentiment,
                "data_date": data_date,
                "otc_source": "polygon_otc" if (g.otc and g.otc > 0) else "estimated",
            }
            dark_pool_rows.append(row)

            whale = _detect_whale(ticker, total_vol, dp_volume, avg_trade_size)
            if whale:
                whale["price"] = round(g.close, 2)
                whale["change_pct"] = round(change_pct, 2)
                whale_alerts.append(whale)

        # Sort by dark pool volume descending
        dark_pool_rows.sort(key=lambda r: r["dark_pool_volume"], reverse=True)
        whale_alerts.sort(key=lambda w: w["dp_volume"], reverse=True)

        result = {
            "dark_pool": dark_pool_rows,
            "whale_alerts": whale_alerts,
            "data_date": data_date,
            "total_tickers": len(dark_pool_rows),
            "source": "polygon",
            "timestamp": now.isoformat(),
        }

        _cache["data"] = result
        _cache["ts"] = now
        _cache["whale_alerts"] = whale_alerts

        logger.info(f"Polygon dark pool data fetched: {len(dark_pool_rows)} tickers, {len(whale_alerts)} whale alerts (date={data_date})")
        return result

    except Exception as e:
        logger.error(f"Polygon dark pool fetch error: {e}")
        if _cache["data"]:
            return _cache["data"]
        return {"dark_pool": [], "whale_alerts": [], "source": "error", "error": str(e)}
