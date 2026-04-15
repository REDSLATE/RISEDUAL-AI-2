"""Market Data Provider Pool — Priority-based failover for price/quote data.

Pool chain (configurable via MARKET_DATA_PROVIDER_POOL env var):
  Alpha Vantage → Finnhub → TwelveData

Usage:
    from services.market_data_pool import market_quote, market_daily, market_pool_status
    quote = await market_quote("AAPL")
"""
import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests
import httpx

from services.provider_pool import ProviderPool, ProviderEntry
from services.pool_config import get_market_data_provider_pool

logger = logging.getLogger(__name__)

market_pool = ProviderPool(get_market_data_provider_pool(), name="MARKET_DATA_PROVIDER_POOL")

# Module-level db reference
_db = None


def set_db(database):
    global _db
    _db = database


# ─────────────────────────────────────────────
#  ALPHA VANTAGE QUOTE
# ─────────────────────────────────────────────
AV_BASE = "https://www.alphavantage.co/query"


def _av_quote_sync(api_key: str, symbol: str) -> Optional[Dict]:
    try:
        r = requests.get(AV_BASE, params={
            "function": "GLOBAL_QUOTE", "symbol": symbol.upper(), "apikey": api_key,
        }, timeout=10)
        data = r.json()
        if "Note" in data or "Information" in data:
            raise RuntimeError("AV rate limited")
        gq = data.get("Global Quote", {})
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
            "source": "alphavantage",
        }
    except Exception as e:
        raise RuntimeError(f"AV quote failed: {e}")


def _av_daily_sync(api_key: str, symbol: str, outputsize: str = "compact") -> Optional[List[Dict]]:
    try:
        r = requests.get(AV_BASE, params={
            "function": "TIME_SERIES_DAILY", "symbol": symbol.upper(),
            "outputsize": outputsize, "apikey": api_key,
        }, timeout=15)
        ts = r.json().get("Time Series (Daily)", {})
        if not ts:
            raise RuntimeError("No time series data")
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
        return rows
    except Exception as e:
        raise RuntimeError(f"AV daily failed: {e}")


# ─────────────────────────────────────────────
#  FINNHUB QUOTE
# ─────────────────────────────────────────────
FINNHUB_BASE = "https://finnhub.io/api/v1"


async def _finnhub_quote(api_key: str, symbol: str) -> Optional[Dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/quote",
                headers={"X-Finnhub-Token": api_key},
                params={"symbol": symbol.upper()},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Finnhub HTTP {resp.status_code}")
            data = resp.json()
            price = data.get("c", 0)
            if not price or price <= 0:
                raise RuntimeError("No price data")
            return {
                "symbol": symbol.upper(),
                "price": round(float(price), 2),
                "change": round(float(data.get("d", 0)), 2),
                "change_pct": round(float(data.get("dp", 0)), 2),
                "volume": 0,  # Finnhub quote doesn't include volume
                "open": round(float(data.get("o", 0)), 2),
                "high": round(float(data.get("h", 0)), 2),
                "low": round(float(data.get("l", 0)), 2),
                "prev_close": round(float(data.get("pc", 0)), 2),
                "source": "finnhub",
            }
    except Exception as e:
        raise RuntimeError(f"Finnhub quote failed: {e}")


async def _finnhub_daily(api_key: str, symbol: str, days: int = 90) -> Optional[List[Dict]]:
    """Fetch daily candles from Finnhub."""
    try:
        now = int(datetime.now(timezone.utc).timestamp())
        start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/stock/candle",
                headers={"X-Finnhub-Token": api_key},
                params={"symbol": symbol.upper(), "resolution": "D", "from": start, "to": now},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Finnhub HTTP {resp.status_code}")
            data = resp.json()
            if data.get("s") != "ok":
                raise RuntimeError("No candle data")
            rows = []
            for i in range(len(data.get("t", []))):
                rows.append({
                    "date": datetime.fromtimestamp(data["t"][i], tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": round(float(data["o"][i]), 2),
                    "high": round(float(data["h"][i]), 2),
                    "low": round(float(data["l"][i]), 2),
                    "close": round(float(data["c"][i]), 2),
                    "volume": int(data["v"][i]),
                })
            rows.reverse()
            return rows
    except Exception as e:
        raise RuntimeError(f"Finnhub daily failed: {e}")


# ─────────────────────────────────────────────
#  TWELVEDATA QUOTE
# ─────────────────────────────────────────────
TD_BASE = "https://api.twelvedata.com"


async def _twelvedata_quote(api_key: str, symbol: str) -> Optional[Dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{TD_BASE}/quote",
                params={"symbol": symbol.upper(), "apikey": api_key},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"TwelveData HTTP {resp.status_code}")
            data = resp.json()
            if "code" in data:
                raise RuntimeError(f"TwelveData error: {data.get('message', data['code'])}")
            price = float(data.get("close", 0))
            if price <= 0:
                raise RuntimeError("No price data")
            prev = float(data.get("previous_close", 0))
            change = round(price - prev, 2) if prev > 0 else 0
            change_pct = round((change / prev * 100), 2) if prev > 0 else 0
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "change": change,
                "change_pct": change_pct,
                "volume": int(data.get("volume", 0)),
                "open": round(float(data.get("open", 0)), 2),
                "high": round(float(data.get("high", 0)), 2),
                "low": round(float(data.get("low", 0)), 2),
                "prev_close": round(prev, 2),
                "source": "twelvedata",
            }
    except Exception as e:
        raise RuntimeError(f"TwelveData quote failed: {e}")


async def _twelvedata_daily(api_key: str, symbol: str, outputsize: int = 90) -> Optional[List[Dict]]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{TD_BASE}/time_series",
                params={
                    "symbol": symbol.upper(), "interval": "1day",
                    "outputsize": outputsize, "apikey": api_key,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"TwelveData HTTP {resp.status_code}")
            data = resp.json()
            if "code" in data:
                raise RuntimeError(f"TwelveData error: {data.get('message', data['code'])}")
            values = data.get("values", [])
            if not values:
                raise RuntimeError("No time series data")
            rows = []
            for v in values:
                rows.append({
                    "date": v["datetime"],
                    "open": round(float(v["open"]), 2),
                    "high": round(float(v["high"]), 2),
                    "low": round(float(v["low"]), 2),
                    "close": round(float(v["close"]), 2),
                    "volume": int(v.get("volume", 0)),
                })
            return rows
    except Exception as e:
        raise RuntimeError(f"TwelveData daily failed: {e}")


# ─────────────────────────────────────────────
#  MARKETSTACK QUOTE & DAILY
# ─────────────────────────────────────────────
MS_BASE = "https://api.marketstack.com/v2"


async def _marketstack_quote(api_key: str, symbol: str) -> Optional[Dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{MS_BASE}/eod/latest",
                params={"access_key": api_key, "symbols": symbol.upper(), "limit": 1},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Marketstack HTTP {resp.status_code}")
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Marketstack error: {data['error'].get('message', '')}")
            rows = data.get("data", [])
            if not rows:
                raise RuntimeError("No data from Marketstack")
            row = rows[0]
            price = float(row.get("close", 0))
            if price <= 0:
                raise RuntimeError("No price data")
            prev = float(row.get("open", 0))
            change = round(price - prev, 2) if prev > 0 else 0
            change_pct = round((change / prev * 100), 2) if prev > 0 else 0
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "change": change,
                "change_pct": change_pct,
                "volume": int(row.get("volume", 0)),
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "prev_close": round(prev, 2),
                "source": "marketstack",
            }
    except Exception as e:
        raise RuntimeError(f"Marketstack quote failed: {e}")


async def _marketstack_daily(api_key: str, symbol: str, limit: int = 90) -> Optional[List[Dict]]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{MS_BASE}/eod",
                params={"access_key": api_key, "symbols": symbol.upper(), "limit": limit},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Marketstack HTTP {resp.status_code}")
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Marketstack error: {data['error'].get('message', '')}")
            values = data.get("data", [])
            if not values:
                raise RuntimeError("No time series data")
            rows = []
            for v in values:
                rows.append({
                    "date": v.get("date", "")[:10],
                    "open": round(float(v.get("open", 0)), 2),
                    "high": round(float(v.get("high", 0)), 2),
                    "low": round(float(v.get("low", 0)), 2),
                    "close": round(float(v.get("close", 0)), 2),
                    "volume": int(v.get("volume", 0)),
                })
            return rows
    except Exception as e:
        raise RuntimeError(f"Marketstack daily failed: {e}")


# ─────────────────────────────────────────────
#  PROVIDER DISPATCH
# ─────────────────────────────────────────────

async def _dispatch_quote(provider: ProviderEntry, symbol: str) -> Dict:
    if provider.provider == "alphavantage":
        result = await asyncio.to_thread(_av_quote_sync, provider.api_key, symbol)
    elif provider.provider == "finnhub":
        result = await _finnhub_quote(provider.api_key, symbol)
    elif provider.provider == "twelvedata":
        result = await _twelvedata_quote(provider.api_key, symbol)
    elif provider.provider == "marketstack":
        result = await _marketstack_quote(provider.api_key, symbol)
    else:
        raise RuntimeError(f"Unknown market provider: {provider.provider}")
    if not result:
        raise RuntimeError(f"No data from {provider.name}")
    result["provider_name"] = provider.name
    return result


async def _dispatch_daily(provider: ProviderEntry, symbol: str, outputsize: str) -> List[Dict]:
    if provider.provider == "alphavantage":
        result = await asyncio.to_thread(_av_daily_sync, provider.api_key, symbol, outputsize)
    elif provider.provider == "finnhub":
        days = 365 if outputsize == "full" else 90
        result = await _finnhub_daily(provider.api_key, symbol, days)
    elif provider.provider == "twelvedata":
        size = 365 if outputsize == "full" else 90
        result = await _twelvedata_daily(provider.api_key, symbol, size)
    elif provider.provider == "marketstack":
        size = 365 if outputsize == "full" else 90
        result = await _marketstack_daily(provider.api_key, symbol, size)
    else:
        raise RuntimeError(f"Unknown market provider: {provider.provider}")
    if not result:
        raise RuntimeError(f"No data from {provider.name}")
    return result


# ─────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────

async def market_quote(symbol: str) -> Optional[Dict]:
    """Get a stock quote with pool failover + MongoDB cache."""
    cache_key = f"pool_quote_{symbol.upper()}"

    # Check cache first
    if _db is not None:
        cached = await _db.price_cache.find_one(
            {"key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()}},
            {"_id": 0}
        )
        if cached and cached.get("data", {}).get("price", 0) > 0:
            cached["data"]["source"] = "cache"
            return cached["data"]

    if not market_pool.available:
        return None

    try:
        async def _exec(provider: ProviderEntry) -> Dict:
            return await _dispatch_quote(provider, symbol)

        result = await market_pool.execute(_exec)

        # Cache successful result (5 min)
        if result and _db is not None:
            await _db.price_cache.update_one(
                {"key": cache_key},
                {"$set": {
                    "key": cache_key,
                    "data": result,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        return result
    except Exception as e:
        logger.error(f"Market quote pool exhausted for {symbol}: {e}")
        return None


async def market_daily(symbol: str, outputsize: str = "compact") -> Optional[List[Dict]]:
    """Get daily OHLCV history with pool failover + MongoDB cache."""
    cache_key = f"pool_daily_{symbol.upper()}_{outputsize}"

    if _db is not None:
        cached = await _db.price_cache.find_one(
            {"key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()}},
            {"_id": 0}
        )
        if cached and cached.get("data"):
            return cached["data"]

    if not market_pool.available:
        return None

    try:
        async def _exec(provider: ProviderEntry) -> List[Dict]:
            return await _dispatch_daily(provider, symbol, outputsize)

        result = await market_pool.execute(_exec)

        if result and _db is not None:
            await _db.price_cache.update_one(
                {"key": cache_key},
                {"$set": {
                    "key": cache_key,
                    "data": result,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        return result
    except Exception as e:
        logger.error(f"Market daily pool exhausted for {symbol}: {e}")
        return None


def market_pool_status() -> dict:
    return market_pool.status()
