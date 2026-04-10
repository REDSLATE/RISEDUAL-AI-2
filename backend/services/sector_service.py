"""Sector Rotation Heatmap service — fetches sector ETF performance data."""
import logging
import asyncio
from typing import Dict, List

from services.price_provider import get_quote, get_daily_history

logger = logging.getLogger(__name__)

SECTOR_ETFS = {
    "XLK": {"name": "Technology", "weight": 30},
    "XLF": {"name": "Financials", "weight": 13},
    "XLV": {"name": "Healthcare", "weight": 12},
    "XLY": {"name": "Consumer Disc.", "weight": 11},
    "XLC": {"name": "Communication", "weight": 9},
    "XLI": {"name": "Industrials", "weight": 8},
    "XLP": {"name": "Consumer Staples", "weight": 6},
    "XLE": {"name": "Energy", "weight": 4},
    "XLRE": {"name": "Real Estate", "weight": 3},
    "XLB": {"name": "Materials", "weight": 2},
    "XLU": {"name": "Utilities", "weight": 2},
}


async def _fetch_etf_quote(symbol: str) -> Dict:
    """Fetch a single ETF quote using smart price provider."""
    quote = await get_quote(symbol)
    if quote:
        return {
            "price": quote["price"],
            "change": quote["change"],
            "change_pct": quote["change_pct"],
            "prev_close": quote.get("prev_close", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "volume": quote.get("volume", 0),
        }
    return {"price": 0, "change": 0, "change_pct": 0}


async def _fetch_etf_daily(symbol: str) -> List[Dict]:
    """Fetch daily time series using smart price provider."""
    history = await get_daily_history(symbol)
    if history:
        return [{"date": d["date"], "close": d["close"]} for d in reversed(history)]
    return []


def _calc_returns(prices: List[Dict]) -> Dict:
    """Calculate returns over various periods from daily price data."""
    if not prices:
        return {"1d": 0, "1w": 0, "1m": 0, "3m": 0, "ytd": 0}

    current = prices[-1]["close"]
    returns = {}

    def pct(old, new):
        return round((new - old) / old * 100, 2) if old else 0

    returns["1d"] = pct(prices[-2]["close"], current) if len(prices) >= 2 else 0
    returns["1w"] = pct(prices[-6]["close"], current) if len(prices) >= 6 else 0
    returns["1m"] = pct(prices[-22]["close"], current) if len(prices) >= 22 else 0
    returns["3m"] = pct(prices[-66]["close"], current) if len(prices) >= 66 else 0

    # YTD: find first trading day of current year
    import datetime as dt
    year_start = str(dt.date.today().year)
    ytd_prices = [p for p in prices if p["date"].startswith(year_start)]
    if ytd_prices:
        returns["ytd"] = pct(ytd_prices[0]["close"], current)
    else:
        returns["ytd"] = returns.get("3m", 0)

    return returns


async def get_sector_heatmap() -> Dict:
    """Build the full sector rotation heatmap data."""
    sectors = []

    # Fetch all ETF data in parallel
    tasks = {sym: _fetch_etf_daily(sym) for sym in SECTOR_ETFS}
    quote_tasks = {sym: _fetch_etf_quote(sym) for sym in SECTOR_ETFS}

    daily_results = {}
    quote_results = {}

    # Batch fetch — do quotes and dailies in parallel
    all_keys = list(SECTOR_ETFS.keys())
    for sym in all_keys:
        daily_results[sym], quote_results[sym] = await asyncio.gather(
            tasks[sym], quote_tasks[sym]
        )

    # Build sector data
    for sym, info in SECTOR_ETFS.items():
        returns = _calc_returns(daily_results.get(sym, []))
        quote = quote_results.get(sym, {})

        sectors.append({
            "symbol": sym,
            "name": info["name"],
            "weight": info["weight"],
            "price": quote.get("price", 0),
            "change_1d": returns.get("1d", 0),
            "change_1w": returns.get("1w", 0),
            "change_1m": returns.get("1m", 0),
            "change_3m": returns.get("3m", 0),
            "change_ytd": returns.get("ytd", 0),
            "volume": quote.get("volume", 0),
            "day_high": quote.get("high", 0),
            "day_low": quote.get("low", 0),
        })

    # Sort by weight (largest sectors first)
    sectors.sort(key=lambda s: s["weight"], reverse=True)

    # Calculate market-wide metrics
    total_weight = sum(s["weight"] for s in sectors)
    weighted_1d = sum(s["change_1d"] * s["weight"] for s in sectors) / total_weight if total_weight else 0
    weighted_1w = sum(s["change_1w"] * s["weight"] for s in sectors) / total_weight if total_weight else 0

    best = max(sectors, key=lambda s: s["change_1d"]) if sectors else None
    worst = min(sectors, key=lambda s: s["change_1d"]) if sectors else None

    return {
        "sectors": sectors,
        "market_summary": {
            "weighted_change_1d": round(weighted_1d, 2),
            "weighted_change_1w": round(weighted_1w, 2),
            "best_sector": {"name": best["name"], "change": best["change_1d"]} if best else None,
            "worst_sector": {"name": worst["name"], "change": worst["change_1d"]} if worst else None,
            "total_sectors": len(sectors),
        },
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
