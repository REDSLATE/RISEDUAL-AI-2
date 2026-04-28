"""Symbol → Sector resolver with Mongo caching + Finnhub fallback.

Used by the Toxic Spike Autopsy to group failed predictions by sector
without hammering Finnhub on every admin-panel open. Lookups are cheap
after the first hit: the sector for a given ticker only changes when
the company is acquired / re-listed, so we cache for 7 days.

Failure-safe: on any error we return "Unknown" so the Autopsy view still
renders the other dimensions. Never raises.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_COLLECTION = "symbol_sector_cache"
_CACHE_TTL_DAYS = 7

# Curated static map for the most-traded tickers. Lets the autopsy
# render a useful "by_sector" breakdown even when Finnhub is down or
# the symbol-sector cache is cold. Intentionally minimal — Finnhub
# fills in the long tail.
_STATIC_SECTORS: dict[str, str] = {
    # Tech
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "INTC": "Technology", "AVGO": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "CSCO": "Technology", "PANW": "Technology", "PLTR": "Technology",
    "SMCI": "Technology", "QCOM": "Technology", "TXN": "Technology",
    # Comm / Internet
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "META": "Communication Services", "NFLX": "Communication Services",
    "DIS": "Communication Services", "TMUS": "Communication Services",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "WFC": "Financials",
    "GS": "Financials", "MS": "Financials", "C": "Financials",
    "V": "Financials", "MA": "Financials", "AXP": "Financials",
    "BRK.B": "Financials", "BLK": "Financials", "SCHW": "Financials",
    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "PFE": "Healthcare",
    "LLY": "Healthcare", "ABBV": "Healthcare", "MRK": "Healthcare",
    "TMO": "Healthcare", "ABT": "Healthcare", "DHR": "Healthcare",
    "BMY": "Healthcare", "CVS": "Healthcare",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "SLB": "Energy", "EOG": "Energy",
    # Industrials
    "BA": "Industrials", "CAT": "Industrials", "GE": "Industrials",
    "HON": "Industrials", "UPS": "Industrials", "RTX": "Industrials",
    "LMT": "Industrials", "DE": "Industrials",
    # Consumer Staples
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "PG": "Consumer Staples", "KO": "Consumer Staples",
    "PEP": "Consumer Staples", "MDLZ": "Consumer Staples",
    # Materials / Utilities / Real Estate
    "LIN": "Materials", "SHW": "Materials",
    "NEE": "Utilities", "DUK": "Utilities",
    "PLD": "Real Estate", "AMT": "Real Estate",
    # ETFs (treated as their dominant sector for grouping)
    "SPY": "Index", "QQQ": "Index", "VOO": "Index", "IWM": "Index",
    "DIA": "Index",
}


async def _cache_get(db, symbol: str) -> Optional[str]:
    if db is None:
        return None
    try:
        doc = await db[_CACHE_COLLECTION].find_one(
            {"_id": symbol.upper()}, {"_id": 0, "sector": 1, "updated_at": 1}
        )
        if not doc:
            return None
        updated_at = doc.get("updated_at")
        if updated_at:
            try:
                ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - ts > timedelta(days=_CACHE_TTL_DAYS):
                    return None
            except Exception:  # noqa: BLE001 — bad timestamp = stale
                return None
        return doc.get("sector") or None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[sector-cache] get failed for {symbol}: {e}")
        return None


async def _cache_put(db, symbol: str, sector: str) -> None:
    if db is None or not sector:
        return
    try:
        await db[_CACHE_COLLECTION].update_one(
            {"_id": symbol.upper()},
            {"$set": {
                "sector": sector,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[sector-cache] put failed for {symbol}: {e}")


async def _finnhub_lookup(symbol: str) -> Optional[str]:
    """Best-effort Finnhub profile2 call — returns None on any failure."""
    try:
        from services.finnhub_service import FinnhubService
        svc = FinnhubService()
        if not svc._is_configured():
            return None
        data = await svc._get("/stock/profile2", {"symbol": symbol.upper()})
        if isinstance(data, dict):
            sector = data.get("finnhubIndustry") or data.get("gind") or None
            if sector and isinstance(sector, str):
                return sector.strip()
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[sector-resolver] finnhub lookup failed for {symbol}: {e}")
        return None


async def resolve_sector(db, symbol: str) -> str:
    """Resolve a symbol to a sector string.

    Order: Mongo cache → static map → Finnhub profile2 → "Unknown".
    Populates the cache on Finnhub hits so repeat lookups stay cheap.
    """
    if not symbol:
        return "Unknown"
    sym = symbol.upper()

    cached = await _cache_get(db, sym)
    if cached:
        return cached

    if sym in _STATIC_SECTORS:
        sector = _STATIC_SECTORS[sym]
        await _cache_put(db, sym, sector)
        return sector

    sector = await _finnhub_lookup(sym)
    if sector:
        await _cache_put(db, sym, sector)
        return sector

    return "Unknown"


async def resolve_sectors_bulk(db, symbols: list[str]) -> dict[str, str]:
    """Resolve multiple symbols in parallel. Returns {symbol: sector}.

    Bounded concurrency (8) to keep Finnhub happy on bursty admin loads.
    """
    if not symbols:
        return {}
    unique = list({(s or "").upper() for s in symbols if s})
    sem = asyncio.Semaphore(8)

    async def _one(sym: str) -> tuple[str, str]:
        async with sem:
            return sym, await resolve_sector(db, sym)

    results = await asyncio.gather(*[_one(s) for s in unique])
    return dict(results)
