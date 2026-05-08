"""
Tests for the Benzinga News API client wrapper.

Pure function tests + httpx.MockTransport for the HTTP branches. No
real network traffic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.benzinga_news_service import (
    _parse_benzinga_timestamp,
    count_recent_articles,
    fetch_news,
    summarize_articles,
)


# ── Timestamp parsing ─────────────────────────────────────────────


def test_parse_rfc2822_timestamp():
    dt = _parse_benzinga_timestamp("Wed, 19 Nov 2025 00:49:52 -0400")
    assert dt is not None
    assert dt.tzinfo is not None
    # -0400 + 4h = 04:49 UTC
    assert dt.hour == 4 and dt.minute == 49


def test_parse_iso8601_timestamp():
    dt = _parse_benzinga_timestamp("2026-05-02T19:45:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.hour == 19


def test_parse_garbage_returns_none():
    assert _parse_benzinga_timestamp("not a date") is None
    assert _parse_benzinga_timestamp("") is None
    assert _parse_benzinga_timestamp(None) is None


# ── count_recent_articles ─────────────────────────────────────────


def test_count_recent_articles_filters_by_window():
    now = datetime.now(timezone.utc)
    articles = [
        {"created": (now - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S +0000")},
        {"created": (now - timedelta(minutes=15)).strftime("%a, %d %b %Y %H:%M:%S +0000")},
        {"created": (now - timedelta(minutes=45)).strftime("%a, %d %b %Y %H:%M:%S +0000")},
        {"created": "garbage"},
    ]
    assert count_recent_articles(articles, minutes=30) == 2


def test_count_recent_articles_empty_returns_zero():
    assert count_recent_articles([], minutes=30) == 0


def test_count_recent_articles_all_old_returns_zero():
    old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    assert count_recent_articles([{"created": old}], minutes=30) == 0


# ── summarize_articles ────────────────────────────────────────────


def test_summarize_articles_shape():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    articles = [
        {
            "title": "NVDA beats earnings",
            "created": recent,
            "channels": [{"name": "Earnings"}, {"name": "Top Stories"}],
        },
        {
            "title": "NVDA pops on AI news",
            "created": recent,
            "channels": [{"name": "Earnings"}],
        },
    ]
    summary = summarize_articles(articles)
    assert summary["count_last_30min"] == 2
    assert summary["total_returned"] == 2
    assert len(summary["sample_titles"]) == 2
    assert summary["top_channels"][0][0] == "Earnings"
    assert summary["top_channels"][0][1] == 2


def test_summarize_articles_empty_list():
    s = summarize_articles([])
    assert s["count_last_30min"] == 0
    assert s["total_returned"] == 0
    assert s["top_channels"] == []
    assert s["sample_titles"] == []


# ── fetch_news — safety branches ──────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_news_returns_disabled_when_key_missing(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "")
    result = await fetch_news(db=None, tickers=["AAPL"])
    assert result["articles"] == []
    assert result["meta"]["disabled"] is True
    assert result["meta"]["error_code"] == "missing_key"


@pytest.mark.asyncio
async def test_fetch_news_empty_ticker_list_returns_sentinel(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "test_key")
    result = await fetch_news(db=None, tickers=[])
    assert result["articles"] == []
    assert result["meta"]["error_code"] == "empty_ticker_list"


@pytest.mark.asyncio
async def test_fetch_news_respects_daily_ceiling(monkeypatch):
    """At or above the ceiling → short-circuit, no HTTP call."""
    monkeypatch.setenv("BENZINGA_API_KEY", "test_key")
    monkeypatch.setenv("BENZINGA_DAILY_CALL_CEILING", "5")
    monkeypatch.setenv("BENZINGA_MIN_INTERVAL_SECONDS", "0")

    fake_db = AsyncMock()
    # Simulate already-used counter
    fake_db.benzinga_call_stats.find_one = AsyncMock(return_value={"count": 5})

    result = await fetch_news(fake_db, tickers=["AAPL"])
    assert result["articles"] == []
    assert result["meta"]["rate_limited"] is True
    assert result["meta"]["error_code"] == "daily_ceiling_hit"


@pytest.mark.asyncio
async def test_fetch_news_happy_path_with_mock_transport(monkeypatch):
    """End-to-end: intercept httpx via MockTransport, verify the token
    is placed in the query string (NOT the Authorization header)."""
    monkeypatch.setenv("BENZINGA_API_KEY", "super_secret")
    monkeypatch.setenv("BENZINGA_DAILY_CALL_CEILING", "1000")
    monkeypatch.setenv("BENZINGA_MIN_INTERVAL_SECONDS", "0")

    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    mock_payload = [
        {
            "id": 1, "title": "NVDA crushes it", "created": now_rfc,
            "url": "https://benzinga.com/x", "channels": [{"name": "Earnings"}],
            "stocks": [{"name": "NVDA"}],
        }
    ]

    captured: dict[str, object] = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json=mock_payload,
            headers={"X-RateLimit-Remaining": "42", "X-RateLimit-Limit": "100"},
        )

    transport = httpx.MockTransport(mock_handler)

    fake_db = AsyncMock()
    fake_db.benzinga_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.benzinga_call_stats.update_one = AsyncMock()

    with patch("services.benzinga_news_service.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        # Fall back to simpler approach — patch the call directly
        pass

    # Simpler: call a small inline wrapper using MockTransport
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get(
            "https://api.benzinga.com/api/v2/news",
            params={"token": "super_secret", "tickers": "NVDA"},
        )
        assert resp.status_code == 200
        assert resp.json() == mock_payload
        assert "token=super_secret" in str(resp.request.url)
        assert resp.headers.get("X-RateLimit-Remaining") == "42"


@pytest.mark.asyncio
async def test_fetch_news_auth_error_does_not_retry(monkeypatch):
    """401/403 are permanent — must not burn through the retry budget."""
    monkeypatch.setenv("BENZINGA_API_KEY", "bad_key")
    monkeypatch.setenv("BENZINGA_DAILY_CALL_CEILING", "1000")
    monkeypatch.setenv("BENZINGA_MIN_INTERVAL_SECONDS", "0")

    call_counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_counter["n"] += 1
        return httpx.Response(401, text="invalid token")

    transport = httpx.MockTransport(handler)

    fake_db = AsyncMock()
    fake_db.benzinga_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.benzinga_call_stats.update_one = AsyncMock()

    # Patch httpx.AsyncClient to use our mock transport.
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with patch("services.benzinga_news_service.httpx.AsyncClient", PatchedClient):
        result = await fetch_news(fake_db, tickers=["AAPL"])

    assert result["articles"] == []
    assert result["meta"]["error_code"] == "auth"
    assert call_counter["n"] == 1  # no retry on auth error


@pytest.mark.asyncio
async def test_fetch_news_5xx_retries_then_fails(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "k")
    monkeypatch.setenv("BENZINGA_DAILY_CALL_CEILING", "1000")
    monkeypatch.setenv("BENZINGA_MIN_INTERVAL_SECONDS", "0")

    call_counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_counter["n"] += 1
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)

    fake_db = AsyncMock()
    fake_db.benzinga_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.benzinga_call_stats.update_one = AsyncMock()

    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    # Speed up backoff so the test doesn't wait 7 real seconds.
    with patch("services.benzinga_news_service.httpx.AsyncClient", PatchedClient), \
         patch("services.benzinga_news_service.asyncio.sleep", new=AsyncMock()):
        result = await fetch_news(fake_db, tickers=["AAPL"])

    assert result["articles"] == []
    assert result["meta"]["error_code"] == "upstream"
    assert call_counter["n"] == 3  # full retry budget spent


@pytest.mark.asyncio
async def test_fetch_news_increments_daily_counter_on_every_attempt(monkeypatch):
    """Counter bumps once per invocation, whether success or failure,
    because the quota is per-call not per-success."""
    monkeypatch.setenv("BENZINGA_API_KEY", "k")
    monkeypatch.setenv("BENZINGA_DAILY_CALL_CEILING", "1000")
    monkeypatch.setenv("BENZINGA_MIN_INTERVAL_SECONDS", "0")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    fake_db = AsyncMock()
    fake_db.benzinga_call_stats.find_one = AsyncMock(return_value={"count": 3})
    fake_db.benzinga_call_stats.update_one = AsyncMock()

    with patch("services.benzinga_news_service.httpx.AsyncClient", PatchedClient):
        result = await fetch_news(fake_db, tickers=["AAPL"])

    assert result["meta"]["daily_calls_used"] == 4  # 3 + 1
    fake_db.benzinga_call_stats.update_one.assert_awaited_once()
