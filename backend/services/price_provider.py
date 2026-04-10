"""
Unified Price Provider — Smart routing: Alpha Vantage → yfinance → MongoDB cache.

Centralizes all market data fetching behind a single interface.
Handles rate limiting, fallbacks, and caching transparently.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"

# Module-level db reference (set via set_db)
_db = None


def set_db(database):
    global _db
    _db = database


def _av_key() -> str:
    return os.environ.get("ALPHA_VANTAGE_API_KEY", "")


# ──────────────────────────────────────────────
#  QUOTE (current price, change, volume)
# ──────────────────────────────────────────────

def _av_quote(symbol: str) -> Optional[Dict]:
    """Fetch quote from Alpha Vantage GLOBAL_QUOTE."""
    try:
        r = requests.get(AV_BASE, params={
            "function": "GLOBAL_QUOTE", "symbol": symbol.upper(), "apikey": _av_key()
        }, timeout=10)
        gq = r.json().get("Global Quote", {})
        price = float(gq.get("05. price", 0))
        if price <= 0:
            return None
        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": float(gq.get("09. change", 0)),
            "change_pct": float(gq.get("10. change percent", "0").replace("%", "")),
            "volume": int(gq.get("06. volume", 0)),
            "open": float(gq.get("02. open", 0)),
            "high": float(gq.get("03. high", 0)),
            "low": float(gq.get("04. low", 0)),
            "prev_close": float(gq.get("08. previous close", 0)),
            "source": "alpha_vantage",
        }
    except Exception as e:
        logger.warning(f"AV quote failed for {symbol}: {e}")
        return None


def _yf_quote(symbol: str) -> Optional[Dict]:
    """Fetch quote from yfinance (free, no API key)."""
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.fast_info
        price = float(info.get("lastPrice", 0) or info.get("last_price", 0))
        prev = float(info.get("previousClose", 0) or info.get("previous_close", 0))
        if price <= 0:
            return None
        change = price - prev if prev > 0 else 0
        change_pct = (change / prev * 100) if prev > 0 else 0
        return {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(info.get("lastVolume", 0) or info.get("last_volume", 0)),
            "open": float(info.get("open", 0)),
            "high": float(info.get("dayHigh", 0) or info.get("day_high", 0)),
            "low": float(info.get("dayLow", 0) or info.get("day_low", 0)),
            "prev_close": round(prev, 2),
            "source": "yfinance",
        }
    except Exception as e:
        logger.warning(f"yfinance quote failed for {symbol}: {e}")
        return None


async def get_quote(symbol: str) -> Optional[Dict]:
    """
    Smart quote: AV → yfinance → cache.
    Caches successful results in MongoDB for 5 minutes.
    """
    cache_key = f"quote_{symbol.upper()}"

    # Check MongoDB cache first
    if _db:
        cached = await _db.price_cache.find_one(
            {"key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()}},
            {"_id": 0}
        )
        if cached and cached.get("data", {}).get("price", 0) > 0:
            cached["data"]["source"] = "cache"
            return cached["data"]

    # Try AV first
    quote = await asyncio.to_thread(_av_quote, symbol)

    # Fallback to yfinance
    if not quote:
        logger.info(f"AV failed for {symbol}, trying yfinance")
        quote = await asyncio.to_thread(_yf_quote, symbol)

    # Cache successful result
    if quote and _db:
        await _db.price_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "data": quote,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    return quote


def get_quote_sync(symbol: str) -> Optional[Dict]:
    """Synchronous version for use in sync contexts (data fetchers, etc.)."""
    quote = _av_quote(symbol)
    if not quote:
        logger.info(f"AV failed for {symbol}, trying yfinance (sync)")
        quote = _yf_quote(symbol)
    return quote


# ──────────────────────────────────────────────
#  DAILY HISTORY (OHLCV time series)
# ──────────────────────────────────────────────

def _av_daily(symbol: str, outputsize: str = "compact") -> Optional[List[Dict]]:
    """Fetch daily OHLCV from Alpha Vantage."""
    try:
        r = requests.get(AV_BASE, params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol.upper(),
            "outputsize": outputsize,
            "apikey": _av_key(),
        }, timeout=15)
        ts = r.json().get("Time Series (Daily)", {})
        if not ts:
            return None
        rows = []
        for date_str, vals in sorted(ts.items(), reverse=True):
            rows.append({
                "date": date_str,
                "open": float(vals["1. open"]),
                "high": float(vals["2. high"]),
                "low": float(vals["3. low"]),
                "close": float(vals["4. close"]),
                "volume": int(vals["5. volume"]),
            })
        return rows if rows else None
    except Exception as e:
        logger.warning(f"AV daily failed for {symbol}: {e}")
        return None


def _yf_daily(symbol: str, period: str = "3mo") -> Optional[List[Dict]]:
    """Fetch daily OHLCV from yfinance."""
    try:
        ticker = yf.Ticker(symbol.upper())
        hist = ticker.history(period=period)
        if hist.empty:
            return None
        rows = []
        for date, row in hist.iterrows():
            rows.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        rows.reverse()
        return rows if rows else None
    except Exception as e:
        logger.warning(f"yfinance daily failed for {symbol}: {e}")
        return None


async def get_daily_history(symbol: str, outputsize: str = "compact") -> Optional[List[Dict]]:
    """
    Smart daily history: AV → yfinance → cache.
    Caches in MongoDB for 30 minutes.
    """
    cache_key = f"daily_{symbol.upper()}_{outputsize}"

    if _db:
        cached = await _db.price_cache.find_one(
            {"key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()}},
            {"_id": 0}
        )
        if cached and cached.get("data"):
            return cached["data"]

    history = await asyncio.to_thread(_av_daily, symbol, outputsize)
    if not history:
        logger.info(f"AV daily failed for {symbol}, trying yfinance")
        period = "full" if outputsize == "full" else "3mo"
        history = await asyncio.to_thread(_yf_daily, symbol, period)

    if history and _db:
        await _db.price_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "data": history,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    return history


def get_daily_history_sync(symbol: str, outputsize: str = "compact") -> Optional[List[Dict]]:
    """Synchronous version for sync contexts."""
    history = _av_daily(symbol, outputsize)
    if not history:
        logger.info(f"AV daily failed for {symbol}, trying yfinance (sync)")
        period = "full" if outputsize == "full" else "3mo"
        history = _yf_daily(symbol, period)
    return history


# ──────────────────────────────────────────────
#  COMPANY OVERVIEW
# ──────────────────────────────────────────────

def _av_overview(symbol: str) -> Optional[Dict]:
    """Fetch company overview from Alpha Vantage."""
    try:
        r = requests.get(AV_BASE, params={
            "function": "OVERVIEW", "symbol": symbol.upper(), "apikey": _av_key()
        }, timeout=10)
        data = r.json()
        if "Symbol" not in data:
            return None
        return data
    except Exception as e:
        logger.warning(f"AV overview failed for {symbol}: {e}")
        return None


def _yf_overview(symbol: str) -> Optional[Dict]:
    """Fetch company info from yfinance as fallback."""
    try:
        info = yf.Ticker(symbol.upper()).info
        if not info or "symbol" not in info:
            return None
        return {
            "Symbol": info.get("symbol", symbol.upper()),
            "Name": info.get("longName", info.get("shortName", "")),
            "Sector": info.get("sector", "N/A"),
            "Industry": info.get("industry", "N/A"),
            "MarketCapitalization": str(info.get("marketCap", 0)),
            "PERatio": str(info.get("trailingPE", "N/A")),
            "EPS": str(info.get("trailingEps", "N/A")),
            "DividendYield": str(info.get("dividendYield", 0)),
            "52WeekHigh": str(info.get("fiftyTwoWeekHigh", 0)),
            "52WeekLow": str(info.get("fiftyTwoWeekLow", 0)),
            "50DayMovingAverage": str(info.get("fiftyDayAverage", 0)),
            "200DayMovingAverage": str(info.get("twoHundredDayAverage", 0)),
            "Beta": str(info.get("beta", "N/A")),
            "Description": info.get("longBusinessSummary", ""),
            "Exchange": info.get("exchange", ""),
            "source": "yfinance",
        }
    except Exception as e:
        logger.warning(f"yfinance overview failed for {symbol}: {e}")
        return None


def get_overview_sync(symbol: str) -> Optional[Dict]:
    """Smart company overview: AV → yfinance."""
    data = _av_overview(symbol)
    if not data:
        logger.info(f"AV overview failed for {symbol}, trying yfinance")
        data = _yf_overview(symbol)
    return data
