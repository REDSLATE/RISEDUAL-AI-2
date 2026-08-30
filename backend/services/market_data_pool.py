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
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import requests
import httpx

from services.provider_pool import ProviderPool, ProviderEntry
from services.pool_config import get_market_data_provider_pool

logger = logging.getLogger(__name__)

market_pool = ProviderPool(get_market_data_provider_pool(), name="MARKET_DATA_PROVIDER_POOL")

# Module-level db reference
_db: Any = None

# ─────────────────────────────────────────────
#  PUBLIC.COM — lazy-init client + lookup
# ─────────────────────────────────────────────
#
# Public.com requires JWT exchange (secret → access token) before any
# call. We cache one ``PublicTradingService`` instance per process so
# every quote/daily call reuses the same JWT for ~60min before
# refreshing transparently. Credentials are resolved in this order:
#
#   1. Env: ``PUBLIC_API_KEY`` + ``PUBLIC_ACCOUNT_ID``
#   2. ``broker_connections`` row where ``broker_id="public"`` and
#      ``status="connected"`` (written by the broker-connect UI)
#
# If neither is available the provider entry silently returns ``None``
# and the pool fails over to AlphaVantage/Finnhub/etc.
_public_client: Any = None
_public_client_loaded: bool = False


async def _resolve_public_client() -> Any:
    """Resolve and cache a ``PublicTradingService`` instance.

    Returns ``None`` if no credentials can be found.
    """
    global _public_client, _public_client_loaded
    if _public_client_loaded:
        return _public_client
    _public_client_loaded = True
    api_key = os.environ.get("PUBLIC_API_KEY", "").strip()
    account_id = os.environ.get("PUBLIC_ACCOUNT_ID", "").strip()
    if not (api_key and account_id) and _db is not None:
        try:
            doc = await _db.broker_connections.find_one(
                {"broker_id": "public", "status": "connected"},
            )
            if doc:
                api_key = api_key or (doc.get("api_key") or "").strip()
                account_id = account_id or (doc.get("api_secret") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[broker_public] broker_connections lookup failed: %s", exc,
            )
    if not (api_key and account_id):
        logger.info(
            "[broker_public] no credentials in env or broker_connections; "
            "Public.com provider disabled (pool falls back to AV/Finnhub).",
        )
        _public_client = None
        return None
    try:
        from services.broker_service import PublicTradingService
        _public_client = PublicTradingService(api_key, account_id)
        logger.info(
            "[broker_public] market-data client cached: account=%s "
            "(JWT exchange on first call)", account_id,
        )
        return _public_client
    except Exception as exc:  # noqa: BLE001
        logger.warning("[broker_public] client init failed: %s", exc)
        _public_client = None
        return None


async def _public_quote_async(symbol: str) -> Optional[dict]:
    """Quote via Public.com market-data API. Returns None on any
    failure so the pool can fail over cleanly."""
    client = await _resolve_public_client()
    if client is None:
        raise RuntimeError("Public.com credentials unavailable")
    result = await asyncio.to_thread(client.get_quote, symbol)
    if not result:
        raise RuntimeError(f"Public.com quote returned empty for {symbol}")
    return result


async def _public_daily_async(
    symbol: str, days: int = 90,
) -> Optional[list[dict]]:
    """Daily OHLCV bars via Public.com. Returns None on failure."""
    client = await _resolve_public_client()
    if client is None:
        raise RuntimeError("Public.com credentials unavailable")
    result = await asyncio.to_thread(client.get_daily_bars, symbol, days=days)
    if not result:
        raise RuntimeError(f"Public.com daily returned empty for {symbol}")
    return result


def set_db(database: Any) -> None:
    global _db
    _db = database


# ─────────────────────────────────────────────
#  ALPHA VANTAGE QUOTE
# ─────────────────────────────────────────────
AV_BASE = "https://www.alphavantage.co/query"


def _av_quote_sync(api_key: str, symbol: str) -> Optional[dict]:
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


def _av_daily_sync(api_key: str, symbol: str, outputsize: str = "compact") -> Optional[list[dict]]:
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


async def _finnhub_quote(api_key: str, symbol: str) -> Optional[dict]:
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


async def _finnhub_daily(api_key: str, symbol: str, days: int = 90) -> Optional[list[dict]]:
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


async def _twelvedata_quote(api_key: str, symbol: str) -> Optional[dict]:
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


async def _twelvedata_daily(api_key: str, symbol: str, outputsize: int = 90) -> Optional[list[dict]]:
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


async def _marketstack_quote(api_key: str, symbol: str) -> Optional[dict]:
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


async def _marketstack_daily(api_key: str, symbol: str, limit: int = 90) -> Optional[list[dict]]:
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
                from services.datetime_utils import to_iso_date
                rows.append({
                    "date": to_iso_date(v.get("date")) or "",
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
#  POLYGON.IO QUOTE & DAILY
# ─────────────────────────────────────────────
# Polygon free tier: 5 req/min. The pool dedupes via 5-min cache
# so this rarely hits the limit on real workloads. Paid tiers
# (Starter $29/mo) are unlimited. Field mapping mirrors Finnhub
# so downstream callers don't need to know which provider served.
POLYGON_BASE = "https://api.polygon.io"


async def _polygon_quote(api_key: str, symbol: str) -> Optional[dict]:
    """Fetch a Polygon ticker snapshot and reshape to the pool's
    common quote schema. Returns ``None`` if the snapshot has no
    last-trade price (closed/delisted ticker)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol.upper()}",
                params={"apiKey": api_key},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Polygon HTTP {resp.status_code}")
            data = resp.json() or {}
            ticker = data.get("ticker") or {}
            day = ticker.get("day") or {}
            prev = ticker.get("prevDay") or {}
            last = ticker.get("lastTrade") or {}
            # Prefer last-trade price, then today's close, then prev close.
            price = float(
                last.get("p")
                or day.get("c")
                or prev.get("c")
                or 0
            )
            if price <= 0:
                raise RuntimeError("No price data")
            prev_close = float(prev.get("c", 0))
            change = round(price - prev_close, 2) if prev_close > 0 else 0
            change_pct = round((change / prev_close * 100), 2) if prev_close > 0 else 0
            return {
                "symbol": symbol.upper(),
                "price": round(price, 2),
                "change": change,
                "change_pct": change_pct,
                "volume": int(day.get("v", 0)),
                "open": round(float(day.get("o", 0)), 2),
                "high": round(float(day.get("h", 0)), 2),
                "low": round(float(day.get("l", 0)), 2),
                "prev_close": round(prev_close, 2),
                "source": "polygon",
            }
    except Exception as e:
        raise RuntimeError(f"Polygon quote failed: {e}")


async def _polygon_daily(api_key: str, symbol: str, days: int = 90) -> Optional[list[dict]]:
    """Daily OHLCV bars from Polygon's aggregates endpoint, sorted
    newest-first to match the other providers in this module."""
    try:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start}/{end}",
                params={
                    "apiKey": api_key,
                    "adjusted": "true",
                    "sort": "desc",
                    "limit": min(days, 500),
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Polygon HTTP {resp.status_code}")
            data = resp.json() or {}
            results = data.get("results") or []
            if not results:
                raise RuntimeError("No daily data")
            rows = []
            for bar in results:
                ts = bar.get("t")
                if ts is None:
                    continue
                rows.append({
                    "date": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": round(float(bar.get("o", 0)), 2),
                    "high": round(float(bar.get("h", 0)), 2),
                    "low": round(float(bar.get("l", 0)), 2),
                    "close": round(float(bar.get("c", 0)), 2),
                    "volume": int(bar.get("v", 0)),
                })
            return rows
    except Exception as e:
        raise RuntimeError(f"Polygon daily failed: {e}")


# ─────────────────────────────────────────────
#  PROVIDER DISPATCH
# ─────────────────────────────────────────────

async def _dispatch_quote(provider: ProviderEntry, symbol: str) -> dict:
    if provider.provider == "public":
        result = await _public_quote_async(symbol)
    elif provider.provider == "alphavantage":
        result = await asyncio.to_thread(_av_quote_sync, provider.api_key, symbol)
    elif provider.provider == "finnhub":
        result = await _finnhub_quote(provider.api_key, symbol)
    elif provider.provider == "twelvedata":
        result = await _twelvedata_quote(provider.api_key, symbol)
    elif provider.provider == "marketstack":
        result = await _marketstack_quote(provider.api_key, symbol)
    elif provider.provider == "polygon":
        result = await _polygon_quote(provider.api_key, symbol)
    else:
        raise RuntimeError(f"Unknown market provider: {provider.provider}")
    if not result:
        raise RuntimeError(f"No data from {provider.name}")
    result["provider_name"] = provider.name
    # Stamp the wire-off timestamp so ``provider_policy`` can compute
    # ``age_seconds`` for the freshness gate on the execution path.
    # Any quote that hasn't been stamped yet gets stamped here — the
    # per-provider fetchers above may set an earlier ts, but they're
    # allowed to (broker feeds may attach the broker's own tick time,
    # which is preferable to our receive time).
    result.setdefault("fetched_at", time.time())
    return result


async def _dispatch_daily(provider: ProviderEntry, symbol: str, outputsize: str) -> list[dict]:
    if provider.provider == "public":
        days = 365 if outputsize == "full" else 90
        result = await _public_daily_async(symbol, days)
    elif provider.provider == "alphavantage":
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
    elif provider.provider == "polygon":
        days = 365 if outputsize == "full" else 90
        result = await _polygon_daily(provider.api_key, symbol, days)
    else:
        raise RuntimeError(f"Unknown market provider: {provider.provider}")
    if not result:
        raise RuntimeError(f"No data from {provider.name}")
    return result


# ─────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────

async def market_quote(symbol: str) -> Optional[dict]:
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
        async def _dispatch_quote_task(provider: ProviderEntry) -> dict:
            return await _dispatch_quote(provider, symbol)

        result = await market_pool.execute(_dispatch_quote_task)

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


async def market_daily(symbol: str, outputsize: str = "compact") -> Optional[list[dict]]:
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
        async def _dispatch_daily_task(provider: ProviderEntry) -> list[dict]:
            return await _dispatch_daily(provider, symbol, outputsize)

        result = await market_pool.execute(_dispatch_daily_task)

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


# ─────────────────────────────────────────────
#  POLICY-AWARE HELPERS
# ─────────────────────────────────────────────
#
# ``fetch_broker_quote`` and ``fetch_vendor_quote`` split the pool
# so ``services.provider_policy.fetch_execution_quote`` can enforce
# broker-first freshness and drift rules on the execution path.
# They intentionally BYPASS the MongoDB price-cache used by
# ``market_quote`` — the freshness check needs a wire-time stamp,
# and a 5-minute cache hit would answer with a quote that's older
# than our freshness ceiling by construction.

# 2026-02 — providers we treat as "the broker" for policy purposes.
# Right now this is only Public.com, but it's a set so MooMoo /
# other broker feeds can be added without a code change.
_BROKER_PROVIDERS = {"public"}


async def fetch_broker_quote(symbol: str) -> Optional[dict]:
    """Return a live broker quote for ``symbol`` or ``None``.

    Bypasses the MongoDB price cache — every call goes to the
    broker on the wire so the freshness gate in
    :mod:`services.provider_policy` sees a real wire-time stamp.

    Guarded by :mod:`services.broker_circuit_breaker` — when the
    breaker is OPEN we return ``None`` immediately (no HTTP) so a
    rate-limited or flapping broker can't burn the tick loop with
    repeated timeouts. Callers on the execution path treat that
    ``None`` the same way they treat "broker doesn't cover this
    symbol" — the vendor fallback serves and auto-execute is
    blocked pending broker recovery.
    """
    if not market_pool.available:
        return None
    # Late import to avoid a circular dep at module load — the
    # breaker module itself does not depend on the pool.
    from services import broker_circuit_breaker as _cb  # noqa: PLC0415
    if not _cb.allow_call():
        return None
    broker_providers = [
        p for p in market_pool.providers if p.provider in _BROKER_PROVIDERS
    ]
    if not broker_providers:
        return None
    last_exc: Optional[Exception] = None
    for provider in broker_providers:
        try:
            result = await _dispatch_quote(provider, symbol)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.info(
                "[market_data_pool] broker quote via %s failed for %s: %s",
                provider.name, symbol, exc,
            )
            continue
        if result:
            _cb.record_success()
            return result
        # A provider returning ``None`` without raising counts as a
        # miss; we don't record it as a breaker failure because the
        # symbol may simply not be in the broker's coverage.
    if last_exc is not None:
        _cb.record_failure(reason=str(last_exc)[:80])
    return None


async def fetch_vendor_quote(symbol: str) -> Optional[dict]:
    """Return a live vendor quote for ``symbol`` (skipping broker
    providers), or ``None`` when every vendor errors out.

    Bypasses the MongoDB price cache for the same reason as
    :func:`fetch_broker_quote` — the drift gate needs a real
    wire-time reading, not a cached one.
    """
    if not market_pool.available:
        return None
    vendors = [
        p for p in market_pool.providers if p.provider not in _BROKER_PROVIDERS
    ]
    if not vendors:
        return None
    for provider in vendors:
        try:
            return await _dispatch_quote(provider, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[market_data_pool] vendor quote via %s failed for %s: %s",
                provider.name, symbol, exc,
            )
    return None


def market_pool_status() -> dict:
    return market_pool.status()


async def get_technical_indicators(ticker: str) -> dict:
    """Return RSI, MACD, SMA-20, SMA-50 for ticker via Alpha Vantage pool.

    Best-effort: returns whatever indicators are available. Missing fields are None.
    Added for ML signal pipeline — does not modify existing pool internals.
    """
    result: dict[str, Any] = {}
    symbol = ticker.upper()
    av_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not av_key:
        return result

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # RSI
            resp = await client.get(
                "https://www.alphavantage.co/query",
                params={"function": "RSI", "symbol": symbol, "interval": "daily",
                        "time_period": 14, "series_type": "close", "apikey": av_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                rsi_data = data.get("Technical Analysis: RSI", {})
                if rsi_data:
                    latest: dict = next(iter(rsi_data.values()), {})
                    result["rsi_14"] = float(latest.get("RSI", 0)) if latest.get("RSI") else None

            # MACD
            resp = await client.get(
                "https://www.alphavantage.co/query",
                params={"function": "MACD", "symbol": symbol, "interval": "daily",
                        "series_type": "close", "apikey": av_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                macd_data = data.get("Technical Analysis: MACD", {})
                if macd_data:
                    latest = next(iter(macd_data.values()), {})
                    result["macd"] = float(latest.get("MACD", 0)) if latest.get("MACD") else None
                    result["macd_signal"] = float(latest.get("MACD_Signal", 0)) if latest.get("MACD_Signal") else None

            # SMA 20 + 50
            for period in [20, 50]:
                resp = await client.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "SMA", "symbol": symbol, "interval": "daily",
                            "time_period": period, "series_type": "close", "apikey": av_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    sma_data = data.get("Technical Analysis: SMA", {})
                    if sma_data:
                        latest = next(iter(sma_data.values()), {})
                        result[f"sma_{period}"] = float(latest.get("SMA", 0)) if latest.get("SMA") else None
    except Exception as e:
        logger.warning(f"Technical indicators fetch failed for {symbol}: {e}")

    return result

