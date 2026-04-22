"""Tests for the Polygon.io adapter prototype.

Exercises the field-map between Polygon's raw response shape and the
normalised dict that matches `FinnhubClient`. Uses a shared mock
`httpx.AsyncClient` so we never hit the live API.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from risedual_core.clients.polygon import PolygonClient


# ────────────────────────────────────────────────────────────────────────────────
# Mock httpx transport helpers
# ────────────────────────────────────────────────────────────────────────────────

def _mock_transport(responses: dict[str, dict | list]) -> httpx.AsyncClient:
    """Return an AsyncClient whose every GET returns the payload
    matched against the request *path*. First path-substring match wins.
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        for path_substring, payload in responses.items():
            if path_substring in str(request.url):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "no mock"})

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _fast_client(responses: dict[str, Any]) -> PolygonClient:
    """Build a PolygonClient with a mock HTTP transport. Also bypasses
    the token bucket by setting a generous rate so the test suite
    doesn't sleep between calls."""
    client = _mock_transport(responses)
    pc = PolygonClient(api_key="test-key", http_client=client)
    # Override rate limiter to fire instantly — we're not testing the
    # bucket here, we're testing the field mapping.
    from risedual_core.clients.base import _TokenBucket
    pc._rate_limiter = _TokenBucket(rate=1000.0, capacity=1000.0)
    return pc


# ────────────────────────────────────────────────────────────────────────────────
# get_quote
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_quote_normalises_last_trade_plus_prev_bar():
    responses = {
        "/v2/last/trade/AAPL": {
            "results": {"p": 180.50, "t": 1739815200000, "s": 100},
        },
        "/v2/aggs/ticker/AAPL/prev": {
            "results": [
                {"o": 178.00, "h": 181.00, "l": 177.50, "c": 179.00, "v": 50000000}
            ],
        },
    }
    pc = _fast_client(responses)
    q = await pc.get_quote("AAPL")
    assert q["current_price"] == 180.50
    assert q["previous_close"] == 179.00
    assert q["change"] == pytest.approx(1.50, abs=0.01)
    assert q["percent_change"] == pytest.approx(0.838, abs=0.01)
    assert q["high"] == 181.00
    assert q["low"] == 177.50
    assert q["open"] == 178.00
    assert q["timestamp"] == 1739815200000


@pytest.mark.asyncio
async def test_get_quote_returns_empty_when_no_last_trade():
    responses = {
        "/v2/last/trade/AAPL": {"status": "NOT_FOUND"},
        "/v2/aggs/ticker/AAPL/prev": {"results": []},
    }
    pc = _fast_client(responses)
    assert await pc.get_quote("AAPL") == {}


@pytest.mark.asyncio
async def test_get_quote_handles_missing_previous_bar():
    """Fresh IPO — no prev day. change/pct_change should be None."""
    responses = {
        "/v2/last/trade/NEWCO": {"results": {"p": 25.00, "t": 1739815200000}},
        "/v2/aggs/ticker/NEWCO/prev": {"results": []},
    }
    pc = _fast_client(responses)
    q = await pc.get_quote("NEWCO")
    assert q["current_price"] == 25.00
    assert q["change"] is None
    assert q["percent_change"] is None
    assert q["previous_close"] is None


# ────────────────────────────────────────────────────────────────────────────────
# get_company_profile
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_company_profile_maps_polygon_shape_to_finnhub_keys():
    responses = {
        "/v3/reference/tickers/AAPL": {
            "results": {
                "name": "Apple Inc.",
                "ticker": "AAPL",
                "primary_exchange": "XNAS",
                "sic_description": "Electronic Computers",
                "market_cap": 3_100_000_000_000,     # full $3.1T
                "list_date": "1980-12-12",
                "currency_name": "usd",
                "locale": "us",
                "phone_number": "+1-408-996-1010",
                "branding": {"logo_url": "https://logo.clearbit.com/apple.com"},
                "homepage_url": "https://apple.com",
                "share_class_shares_outstanding": 15_500_000_000,
            }
        }
    }
    pc = _fast_client(responses)
    p = await pc.get_company_profile("AAPL")
    assert p["name"] == "Apple Inc."
    assert p["ticker"] == "AAPL"
    assert p["exchange"] == "XNAS"
    assert p["industry"] == "Electronic Computers"
    # Must be scaled to millions to match Finnhub's conventions.
    assert p["market_cap"] == pytest.approx(3_100_000, rel=1e-6)
    assert p["ipo_date"] == "1980-12-12"
    assert p["currency"] == "usd"
    assert p["country"] == "us"
    assert p["logo"] == "https://logo.clearbit.com/apple.com"
    assert p["shares_outstanding"] == 15_500_000_000


@pytest.mark.asyncio
async def test_get_company_profile_empty_on_no_results():
    responses = {
        "/v3/reference/tickers/ZZZZZ": {"results": None},
    }
    pc = _fast_client(responses)
    assert await pc.get_company_profile("ZZZZZ") == {}


@pytest.mark.asyncio
async def test_get_company_profile_handles_missing_branding():
    responses = {
        "/v3/reference/tickers/NOBRAND": {
            "results": {"name": "Nobrand Co", "ticker": "NOBRAND"}
        }
    }
    pc = _fast_client(responses)
    p = await pc.get_company_profile("NOBRAND")
    assert p["name"] == "Nobrand Co"
    assert p["logo"] is None
    assert p["market_cap"] is None


# ────────────────────────────────────────────────────────────────────────────────
# get_news
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_news_maps_polygon_articles_into_finnhub_schema():
    responses = {
        "/v2/reference/news": {
            "results": [
                {
                    "title": "Apple beats Q4 estimates",
                    "description": "Strong iPhone sales drive beat",
                    "article_url": "https://news.example.com/1",
                    "published_utc": "2026-02-19T14:30:00Z",
                    "publisher": {"name": "Reuters"},
                    "image_url": "https://img/1.jpg",
                    "keywords": ["earnings", "apple"],
                },
                {
                    "title": "Supply chain update",
                    "description": "Foxconn ramps output",
                    "article_url": "https://news.example.com/2",
                    "published_utc": "2026-02-18T09:15:00Z",
                    "publisher": {"name": "Bloomberg"},
                    "image_url": None,
                    "keywords": [],
                },
            ]
        }
    }
    pc = _fast_client(responses)
    news = await pc.get_news("AAPL", "2026-02-01", "2026-02-28")
    assert len(news) == 2

    item = news[0]
    assert item["headline"] == "Apple beats Q4 estimates"
    assert item["source"] == "Reuters"
    assert item["category"] == "earnings"
    assert item["datetime"] == "2026-02-19T14:30:00Z"
    # Second article has empty keywords → category falls through to None.
    assert news[1]["category"] is None


@pytest.mark.asyncio
async def test_get_news_empty_on_bad_response():
    responses = {"/v2/reference/news": {"status": "error"}}
    pc = _fast_client(responses)
    assert await pc.get_news("AAPL", "2026-02-01", "2026-02-28") == []


# ────────────────────────────────────────────────────────────────────────────────
# get_recommendation_trends — always empty (not supported by Polygon)
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_recommendation_trends_returns_empty_list():
    pc = _fast_client({})
    assert await pc.get_recommendation_trends("AAPL") == []


# ────────────────────────────────────────────────────────────────────────────────
# get_aggregates — historical bars
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_aggregates_normalises_ohlcv_rows():
    responses = {
        "/v2/aggs/ticker/AAPL/range": {
            "results": [
                {
                    "t": 1739750400000,    # 2026-02-17 00:00:00 UTC
                    "o": 178.00,
                    "h": 181.00,
                    "l": 177.50,
                    "c": 180.20,
                    "v": 55_000_000,
                    "vw": 179.40,
                    "n": 210_000,
                }
            ]
        }
    }
    pc = _fast_client(responses)
    bars = await pc.get_aggregates("AAPL", "2026-02-17", "2026-02-17")
    assert len(bars) == 1
    bar = bars[0]
    assert bar["open"] == 178.00
    assert bar["high"] == 181.00
    assert bar["low"] == 177.50
    assert bar["close"] == 180.20
    assert bar["volume"] == 55_000_000
    assert bar["vwap"] == 179.40
    assert bar["trades"] == 210_000
    assert bar["timestamp"].startswith("2025-02-17")
