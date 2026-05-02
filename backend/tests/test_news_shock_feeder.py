"""
Tests for the News Shock feeder — Benzinga → equity_telemetry bridge.

Mocks ``benzinga_news_service.fetch_news`` + ``equity_telemetry`` so the
tests don't need live network or Mongo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.news_shock_feeder import (
    batch_feed_symbols,
    fetch_and_record_news_telemetry,
)


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _fake_fetch_success(article_count: int) -> dict:
    """Build a fake fetch_news result with ``article_count`` recent
    articles (all within the last 5 minutes)."""
    now = datetime.now(timezone.utc)
    articles = [
        {
            "id": i,
            "title": f"Headline {i}",
            "created": _rfc2822(now - timedelta(minutes=2)),
        }
        for i in range(article_count)
    ]
    return {
        "articles": articles,
        "meta": {
            "disabled": False,
            "rate_limited": False,
            "error_code": None,
            "fetched_at": now.isoformat(),
            "daily_calls_used": 1,
        },
    }


# ── Happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_and_record_counts_and_records():
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value=_fake_fetch_success(5)),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ) as mock_record:
        result = await fetch_and_record_news_telemetry(fake_db, "nvda")

    assert result["symbol"] == "NVDA"
    assert result["news_count"] == 5
    assert result["total_returned"] == 5
    assert result["recorded"] is True

    # Verify record_measurement was called with the count
    mock_record.assert_awaited_once()
    _, kwargs = mock_record.await_args
    assert kwargs["news_count"] == 5


@pytest.mark.asyncio
async def test_fetch_and_record_handles_zero_articles_legitimately():
    """A real 200 response with zero recent articles IS a valid sample —
    that's the baseline state, not an error. Must record as 0."""
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value=_fake_fetch_success(0)),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ) as mock_record:
        result = await fetch_and_record_news_telemetry(fake_db, "AAPL")

    assert result["news_count"] == 0
    assert result["recorded"] is True
    mock_record.assert_awaited_once()


# ── Upstream failure paths — never record ────────────────────────


@pytest.mark.asyncio
async def test_disabled_key_does_not_record():
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value={
            "articles": [],
            "meta": {"disabled": True, "error_code": "missing_key"},
        }),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ) as mock_record:
        result = await fetch_and_record_news_telemetry(fake_db, "AAPL")

    assert result["recorded"] is False
    assert result["meta"]["disabled"] is True
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_ceiling_hit_does_not_record():
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value={
            "articles": [],
            "meta": {"rate_limited": True, "error_code": "daily_ceiling_hit"},
        }),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ) as mock_record:
        result = await fetch_and_record_news_telemetry(fake_db, "AAPL")

    assert result["recorded"] is False
    assert result["meta"]["rate_limited"] is True
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_error_does_not_record():
    """A 5xx or network failure returns error_code != None + empty
    articles — we skip recording so the baseline isn't biased toward
    zero by upstream outages."""
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value={
            "articles": [],
            "meta": {"disabled": False, "rate_limited": False, "error_code": "upstream"},
        }),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ) as mock_record:
        result = await fetch_and_record_news_telemetry(fake_db, "AAPL")

    assert result["recorded"] is False
    assert result["meta"]["error_code"] == "upstream"
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_symbol_short_circuits():
    result = await fetch_and_record_news_telemetry(AsyncMock(), "")
    assert result["recorded"] is False
    assert result["meta"]["error_code"] == "empty_symbol"


@pytest.mark.asyncio
async def test_record_measurement_failure_returns_recorded_false():
    fake_db = AsyncMock()
    with patch(
        "services.benzinga_news_service.fetch_news",
        new=AsyncMock(return_value=_fake_fetch_success(3)),
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(side_effect=RuntimeError("mongo down")),
    ):
        result = await fetch_and_record_news_telemetry(fake_db, "MSFT")

    assert result["news_count"] == 3
    assert result["recorded"] is False


# ── batch_feed_symbols ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_feed_symbols_iterates():
    fake_db = AsyncMock()

    call_count = {"n": 0}

    async def fake_fetch(*args, **kwargs):
        call_count["n"] += 1
        return _fake_fetch_success(call_count["n"])

    with patch(
        "services.benzinga_news_service.fetch_news", new=fake_fetch,
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ):
        result = await batch_feed_symbols(fake_db, ["AAPL", "MSFT", "NVDA"])

    assert result["fed"] == 3
    assert result["skipped"] == 0
    # 1+2+3 articles across the 3 symbols
    assert result["total_articles"] == 6


@pytest.mark.asyncio
async def test_batch_feed_short_circuits_on_ceiling():
    """Once the daily ceiling trips, batch must stop iterating — not
    serialize through the remaining symbols and compound the skip."""
    fake_db = AsyncMock()
    call_count = {"n": 0}

    async def fake_fetch(*args, **kwargs):
        call_count["n"] += 1
        # First call succeeds, second trips the ceiling.
        if call_count["n"] == 1:
            return _fake_fetch_success(2)
        return {
            "articles": [],
            "meta": {"rate_limited": True, "error_code": "daily_ceiling_hit"},
        }

    with patch(
        "services.benzinga_news_service.fetch_news", new=fake_fetch,
    ), patch(
        "services.equity_telemetry.record_measurement",
        new=AsyncMock(),
    ):
        result = await batch_feed_symbols(
            fake_db, ["AAPL", "MSFT", "NVDA", "TSLA"],
        )

    # Should have stopped after the 2nd call (the ceiling-hit one)
    assert call_count["n"] == 2
    assert result["fed"] == 1
    assert result["skipped"] == 1
    assert len(result["per_symbol"]) == 2
