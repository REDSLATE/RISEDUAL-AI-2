"""Tests for the Polygon.io adapter wired into market_data_pool.

These tests focus on the JSON-shape adapter logic (snapshot →
common quote schema, aggregates → common daily schema). HTTP
calls are stubbed via httpx.MockTransport so the suite never
hits Polygon.

Pool registration tests cover:
- POLYGON_API_KEY in env → entry registered
- env unset → entry not registered (no breakage)
- MARKET_DATA_POLYGON_PRIORITY override applied
- Malformed priority falls back to default 4
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

from services import market_data_pool as mdp
from services import pool_config

# Capture the real AsyncClient so our patches don't recurse into
# themselves on the next call.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_factory(transport):
    """Return a callable that yields a fresh AsyncClient bound to
    the given mock transport. Used as the patch replacement so we
    bypass any installed transport on the global module."""
    def _make(*_a, **_kw):
        return _REAL_ASYNC_CLIENT(transport=transport, timeout=15)
    return _make


# ── Pool registration ─────────────────────────────────────────────


def _isolated_env(**kw):
    """Wipe known market-data env vars and set kw."""
    keys = (
        "MARKET_DATA_PROVIDER_POOL", "ALPHAVANTAGEAPIKEY",
        "FINNHUB_API_KEY", "MARKETSTACK_API_KEY", "POLYGON_API_KEY",
        "MARKET_DATA_POLYGON_PRIORITY",
    )
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        for k, v in kw.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        for k in kw:
            os.environ.pop(k, None)


@pytest.fixture
def env_isolated():
    """Wipe all market-data env vars for the duration of a test."""
    keys = (
        "MARKET_DATA_PROVIDER_POOL", "ALPHAVANTAGEAPIKEY",
        "FINNHUB_API_KEY", "MARKETSTACK_API_KEY", "POLYGON_API_KEY",
        "MARKET_DATA_POLYGON_PRIORITY",
    )
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_polygon_registers_when_key_set(env_isolated):
    os.environ["POLYGON_API_KEY"] = "pk_test"
    pool = pool_config.get_market_data_provider_pool()
    polygon = next((p for p in pool if p["provider"] == "polygon"), None)
    assert polygon is not None
    assert polygon["api_key"] == "pk_test"
    assert polygon["priority"] == 4
    assert polygon["name"] == "polygon-ab"


def test_polygon_absent_when_key_missing(env_isolated):
    pool = pool_config.get_market_data_provider_pool()
    assert all(p["provider"] != "polygon" for p in pool)


def test_polygon_priority_env_override(env_isolated):
    os.environ["POLYGON_API_KEY"] = "pk_test"
    os.environ["MARKET_DATA_POLYGON_PRIORITY"] = "1"
    pool = pool_config.get_market_data_provider_pool()
    polygon = next(p for p in pool if p["provider"] == "polygon")
    assert polygon["priority"] == 1


def test_polygon_priority_malformed_falls_back(env_isolated):
    os.environ["POLYGON_API_KEY"] = "pk_test"
    os.environ["MARKET_DATA_POLYGON_PRIORITY"] = "not-a-number"
    pool = pool_config.get_market_data_provider_pool()
    polygon = next(p for p in pool if p["provider"] == "polygon")
    assert polygon["priority"] == 4


# ── _polygon_quote shape adapter ──────────────────────────────────


@pytest.mark.asyncio
async def test_polygon_quote_maps_snapshot_to_common_schema():
    """Polygon snapshot.lastTrade.p → quote.price; snapshot.day.{o,h,l,c,v}
    → quote.{open,high,low,close ignored,volume}; snapshot.prevDay.c
    → quote.prev_close. Change/change_pct computed from current vs
    prev_close so the field set matches FinnhubClient.get_quote."""

    snapshot = {
        "ticker": {
            "lastTrade": {"p": 195.50},
            "day": {"o": 194.0, "h": 196.5, "l": 193.2, "c": 195.4, "v": 12345678},
            "prevDay": {"c": 192.0},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v2/snapshot/locale/us/markets/stocks/tickers/AAPL" in str(request.url)
        return httpx.Response(200, json=snapshot)

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        out = await mdp._polygon_quote("pk_test", "AAPL")

    assert out is not None
    assert out["symbol"] == "AAPL"
    assert out["price"] == 195.50
    assert out["prev_close"] == 192.00
    assert out["change"] == round(195.50 - 192.00, 2)
    assert out["change_pct"] == round((195.50 - 192.00) / 192.00 * 100, 2)
    assert out["open"] == 194.00
    assert out["high"] == 196.50
    assert out["low"] == 193.20
    assert out["volume"] == 12345678
    assert out["source"] == "polygon"


@pytest.mark.asyncio
async def test_polygon_quote_falls_back_to_day_close():
    """When lastTrade is missing (e.g. closed market), use day.c as
    the price. This is the documented behaviour of Polygon's
    snapshot endpoint outside RTH."""
    snapshot = {
        "ticker": {
            "lastTrade": {},  # empty
            "day": {"c": 195.4, "o": 194.0, "h": 196.5, "l": 193.2, "v": 100},
            "prevDay": {"c": 192.0},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot)

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        out = await mdp._polygon_quote("pk_test", "AAPL")
    assert out["price"] == 195.40


@pytest.mark.asyncio
async def test_polygon_quote_raises_on_zero_price():
    """Snapshot with no usable price → raise RuntimeError so the
    pool advances to the next provider."""
    snapshot = {"ticker": {"lastTrade": {}, "day": {}, "prevDay": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot)

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        with pytest.raises(RuntimeError, match="Polygon quote failed"):
            await mdp._polygon_quote("pk_test", "AAPL")


@pytest.mark.asyncio
async def test_polygon_quote_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": "ERROR"})

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        with pytest.raises(RuntimeError, match="Polygon quote failed"):
            await mdp._polygon_quote("pk_test", "AAPL")


# ── _polygon_daily shape adapter ──────────────────────────────────


@pytest.mark.asyncio
async def test_polygon_daily_maps_aggregates_to_common_schema():
    """Polygon /v2/aggs returns results=[{t,o,h,l,c,v}, ...].
    The adapter should preserve the (descending) order Polygon
    returns when sort=desc and convert ms-epoch t → 'YYYY-MM-DD'."""

    # 2026-04-01 00:00 UTC = 1774915200000ms
    aggs = {
        "results": [
            {"t": 1774915200000, "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0, "v": 1_000_000},
            {"t": 1774828800000, "o":  99.0, "h": 100.5, "l": 98.5, "c":  99.5, "v":   900_000},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v2/aggs/ticker/AAPL" in str(request.url)
        return httpx.Response(200, json=aggs)

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        out = await mdp._polygon_daily("pk_test", "AAPL", days=30)

    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["date"] == "2026-03-31"
    assert out[0]["close"] == 101.0
    assert out[0]["volume"] == 1_000_000
    assert out[1]["close"] == 99.5


@pytest.mark.asyncio
async def test_polygon_daily_raises_when_no_results():
    aggs = {"results": []}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=aggs)

    transport = httpx.MockTransport(handler)
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        with pytest.raises(RuntimeError, match="Polygon daily failed"):
            await mdp._polygon_daily("pk_test", "AAPL", days=30)


# ── Dispatch wire-up ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_quote_routes_polygon():
    """_dispatch_quote must route provider='polygon' through
    _polygon_quote and tag the result with provider_name."""
    from services.provider_pool import ProviderEntry

    snapshot = {
        "ticker": {
            "lastTrade": {"p": 100.0},
            "day": {"o": 99.0, "h": 101.0, "l": 98.0, "c": 100.0, "v": 1000},
            "prevDay": {"c": 99.0},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot)

    transport = httpx.MockTransport(handler)
    entry = ProviderEntry(
        name="polygon-ab", provider="polygon",
        api_key="pk_test", priority=4,
    )
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        out = await mdp._dispatch_quote(entry, "AAPL")
    assert out["source"] == "polygon"
    assert out["provider_name"] == "polygon-ab"


@pytest.mark.asyncio
async def test_dispatch_daily_routes_polygon():
    from services.provider_pool import ProviderEntry

    aggs = {
        "results": [
            {"t": 1774915200000, "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0, "v": 1000},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=aggs)

    transport = httpx.MockTransport(handler)
    entry = ProviderEntry(
        name="polygon-ab", provider="polygon",
        api_key="pk_test", priority=4,
    )
    with patch("services.market_data_pool.httpx.AsyncClient", _client_factory(transport)):
        out = await mdp._dispatch_daily(entry, "AAPL", "compact")
    assert isinstance(out, list)
    assert out[0]["close"] == 101.0
