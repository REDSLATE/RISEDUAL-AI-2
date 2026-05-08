"""
Tests for the Alpha Vantage sentiment feeder — Patent M NEWS_SHOCK
sentiment leg.

Uses httpx.MockTransport for HTTP branches + AsyncMock for the Mongo
counter. Pure function tests cover the magnitude computation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.av_sentiment_feeder import (
    _parse_av_timestamp,
    batch_feed_sentiment,
    compute_sentiment_magnitude,
    fetch_and_record_sentiment,
)


def _av_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _fake_feed(scores: list[float], window_minutes: int = 30) -> list[dict]:
    """Build a fake AV feed with the given sentiment scores, all inside
    the window."""
    now = datetime.now(timezone.utc)
    return [
        {
            "time_published": _av_timestamp(now - timedelta(minutes=2)),
            "overall_sentiment_score": s,
            "overall_sentiment_label": "Bullish",
            "title": f"story {i}",
        }
        for i, s in enumerate(scores)
    ]


# ── Pure compute_sentiment_magnitude ────────────────────────────


def test_magnitude_averages_then_abs():
    feed = _fake_feed([0.6, 0.4])
    mag, count = compute_sentiment_magnitude(feed)
    assert mag == pytest.approx(0.5)
    assert count == 2


def test_magnitude_negative_average_produces_positive():
    feed = _fake_feed([-0.8, -0.4])
    mag, count = compute_sentiment_magnitude(feed)
    assert mag == pytest.approx(0.6)
    assert count == 2


def test_magnitude_mixed_scores_cancel():
    feed = _fake_feed([0.7, -0.7])
    mag, count = compute_sentiment_magnitude(feed)
    assert mag == pytest.approx(0.0, abs=1e-9)
    assert count == 2


def test_magnitude_clamps_to_unit_interval():
    feed = _fake_feed([1.5, 1.5])  # AV should never emit >1 but defend anyway
    mag, _ = compute_sentiment_magnitude(feed)
    assert 0.0 <= mag <= 1.0


def test_magnitude_excludes_articles_outside_window():
    old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    fresh_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
    feed = [
        {"time_published": _av_timestamp(old_ts), "overall_sentiment_score": 0.9},
        {"time_published": _av_timestamp(fresh_ts), "overall_sentiment_score": 0.3},
    ]
    mag, count = compute_sentiment_magnitude(feed, window_minutes=30)
    assert mag == pytest.approx(0.3)
    assert count == 1


def test_magnitude_empty_feed_is_zero():
    mag, count = compute_sentiment_magnitude([])
    assert mag == 0.0
    assert count == 0


def test_magnitude_ignores_malformed_scores():
    feed = [
        {"time_published": _av_timestamp(datetime.now(timezone.utc)), "overall_sentiment_score": None},
        {"time_published": _av_timestamp(datetime.now(timezone.utc)), "overall_sentiment_score": "garbage"},
        {"time_published": _av_timestamp(datetime.now(timezone.utc)), "overall_sentiment_score": 0.4},
    ]
    mag, count = compute_sentiment_magnitude(feed)
    assert mag == pytest.approx(0.4)
    assert count == 1


def test_parse_av_timestamp_strict():
    assert _parse_av_timestamp("20260502T193000") is not None
    assert _parse_av_timestamp("") is None
    assert _parse_av_timestamp("not a date") is None
    assert _parse_av_timestamp(None) is None


# ── Feeder safety branches ──────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_key_short_circuits(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "")
    monkeypatch.setenv("ALPHAVANTAGEAPIKEY", "")
    result = await fetch_and_record_sentiment(db=None, symbol="AAPL")
    assert result["recorded"] is False
    assert result["meta"]["disabled"] is True
    assert result["meta"]["error_code"] == "missing_key"


@pytest.mark.asyncio
async def test_empty_symbol_short_circuits():
    result = await fetch_and_record_sentiment(db=None, symbol="")
    assert result["recorded"] is False
    assert result["meta"]["error_code"] == "empty_symbol"


@pytest.mark.asyncio
async def test_daily_ceiling_does_not_hit_upstream(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "k")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_DAILY_CEILING", "5")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", "0")

    fake_db = AsyncMock()
    fake_db.alpha_vantage_news_call_stats.find_one = AsyncMock(return_value={"count": 5})

    result = await fetch_and_record_sentiment(fake_db, "AAPL")
    assert result["recorded"] is False
    assert result["meta"]["rate_limited"] is True
    assert result["meta"]["error_code"] == "daily_ceiling_hit"


@pytest.mark.asyncio
async def test_happy_path_records_magnitude(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "k")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_DAILY_CEILING", "1000")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", "0")

    fake_feed = _fake_feed([0.8, 0.6])
    captured_records: list[tuple] = []

    async def fake_record(symbol, **kwargs):
        captured_records.append((symbol, kwargs))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"feed": fake_feed})

    transport = httpx.MockTransport(handler)
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    fake_db = AsyncMock()
    fake_db.alpha_vantage_news_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.alpha_vantage_news_call_stats.update_one = AsyncMock()

    with patch("services.av_sentiment_feeder.httpx.AsyncClient", PatchedClient), \
         patch("services.equity_telemetry.record_measurement", new=fake_record):
        result = await fetch_and_record_sentiment(fake_db, "aapl")

    assert result["symbol"] == "AAPL"
    assert result["recorded"] is True
    assert result["sentiment_abs"] == pytest.approx(0.7)
    assert result["article_count"] == 2
    assert len(captured_records) == 1
    assert captured_records[0][1]["news_sentiment_abs"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_av_rate_limit_signaled_in_body_does_not_record(monkeypatch):
    """AV signals throttle via `Note` / `Information` keys with HTTP 200.
    Must detect and skip recording."""
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "k")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_DAILY_CEILING", "1000")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", "0")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Information": "Our standard API call frequency is..."},
        )

    transport = httpx.MockTransport(handler)
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    fake_db = AsyncMock()
    fake_db.alpha_vantage_news_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.alpha_vantage_news_call_stats.update_one = AsyncMock()

    record_mock = AsyncMock()
    with patch("services.av_sentiment_feeder.httpx.AsyncClient", PatchedClient), \
         patch("services.equity_telemetry.record_measurement", new=record_mock):
        result = await fetch_and_record_sentiment(fake_db, "AAPL")

    assert result["recorded"] is False
    assert result["meta"]["rate_limited"] is True
    assert result["meta"]["error_code"] == "rate_limited"
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_5xx_does_not_record(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "k")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_DAILY_CEILING", "1000")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", "0")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    fake_db = AsyncMock()
    fake_db.alpha_vantage_news_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.alpha_vantage_news_call_stats.update_one = AsyncMock()

    with patch("services.av_sentiment_feeder.httpx.AsyncClient", PatchedClient), \
         patch("services.equity_telemetry.record_measurement", new=AsyncMock()) as record_mock:
        result = await fetch_and_record_sentiment(fake_db, "AAPL")

    assert result["recorded"] is False
    assert result["meta"]["error_code"] == "upstream"
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_feed_iterates_and_short_circuits(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "k")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_DAILY_CEILING", "1000")
    monkeypatch.setenv("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", "0")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, json={"feed": _fake_feed([0.5])})
        # Trigger rate-limit on 2nd call
        return httpx.Response(200, json={"Note": "throttled"})

    transport = httpx.MockTransport(handler)
    real_cls = httpx.AsyncClient

    class PatchedClient(real_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    fake_db = AsyncMock()
    fake_db.alpha_vantage_news_call_stats.find_one = AsyncMock(return_value=None)
    fake_db.alpha_vantage_news_call_stats.update_one = AsyncMock()

    with patch("services.av_sentiment_feeder.httpx.AsyncClient", PatchedClient), \
         patch("services.equity_telemetry.record_measurement", new=AsyncMock()):
        result = await batch_feed_sentiment(
            fake_db, ["AAPL", "NVDA", "TSLA", "MSFT"],
        )

    # Should stop after the 2nd call (which tripped the rate-limit).
    assert call_count["n"] == 2
    assert result["fed"] == 1
    assert result["skipped"] == 1
    assert len(result["per_symbol"]) == 2
