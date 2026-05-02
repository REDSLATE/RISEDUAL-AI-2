"""
Tests for the 15-min news feeders scheduler.

Covers:
* market-hours gate (weekend / off-hours / RTH)
* rotation offset wraps correctly across Tier A
* tick short-circuits on empty Tier A
* tick advances offset only after both feeders run
* feeder failures don't crash the tick
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.news_feeders_scheduler import (
    _is_market_hours,
    _pick_symbols,
    run_news_feeders_tick,
)


# ── Market-hours gate ─────────────────────────────────────────────


def test_market_hours_accepts_weekday_rth():
    """Wednesday 15:00 UTC (10am ET roughly) is in session."""
    dt = datetime(2026, 2, 4, 15, 0, 0, tzinfo=timezone.utc)  # Wed
    assert _is_market_hours(dt) is True


def test_market_hours_rejects_weekend():
    dt = datetime(2026, 2, 7, 15, 0, 0, tzinfo=timezone.utc)  # Sat
    assert _is_market_hours(dt) is False


def test_market_hours_rejects_pre_market():
    dt = datetime(2026, 2, 4, 11, 0, 0, tzinfo=timezone.utc)  # Wed 11 UTC (pre-market)
    assert _is_market_hours(dt) is False


def test_market_hours_rejects_post_market():
    dt = datetime(2026, 2, 4, 22, 0, 0, tzinfo=timezone.utc)  # Wed 22 UTC
    assert _is_market_hours(dt) is False


def test_market_hours_edges():
    """13:00 UTC is in; 21:00 UTC is OUT (exclusive right boundary)."""
    assert _is_market_hours(datetime(2026, 2, 4, 13, 0, 0, tzinfo=timezone.utc)) is True
    assert _is_market_hours(datetime(2026, 2, 4, 21, 0, 0, tzinfo=timezone.utc)) is False
    assert _is_market_hours(datetime(2026, 2, 4, 20, 59, 0, tzinfo=timezone.utc)) is True


# ── _pick_symbols rotation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_symbols_rotates_cleanly():
    """Offset 0 → first 15, offset advances to 15. Offset 90 on a
    100-symbol universe → symbols 90-99 + 0-4 (wrap), offset advances
    to 5."""
    tier_a = [{"symbol": f"SYM{i:03d}", "tier": "A", "rank": i} for i in range(100)]

    class FakeDB:
        def __init__(self, starting_offset=0):
            self._offset = starting_offset

        async def _find_tier_a(self, *args, **kwargs):
            return tier_a

        @property
        def scheduler_state(self):
            parent = self
            class _SS:
                async def find_one(self, *a, **kw):
                    return {"offset": parent._offset}
                async def update_one(self, *a, **kw):
                    pass
            return _SS()

    # We need to mock the internal _get_active_tiered_symbols call
    fake_db = FakeDB(starting_offset=0)
    with patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=tier_a),
    ):
        symbols, new_offset = await _pick_symbols(fake_db, batch_size=15)
    assert len(symbols) == 15
    assert symbols[0] == "SYM000"
    assert symbols[-1] == "SYM014"
    assert new_offset == 15

    # Wrap-around case
    fake_db = FakeDB(starting_offset=90)
    with patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=tier_a),
    ):
        symbols, new_offset = await _pick_symbols(fake_db, batch_size=15)
    assert len(symbols) == 15
    assert symbols[0] == "SYM090"
    assert symbols[9] == "SYM099"
    assert symbols[10] == "SYM000"
    assert symbols[14] == "SYM004"
    assert new_offset == 5


@pytest.mark.asyncio
async def test_pick_symbols_empty_tier_a():
    class FakeDB:
        @property
        def scheduler_state(self):
            class _SS:
                async def find_one(self, *a, **kw):
                    return None
            return _SS()

    with patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=[]),
    ):
        symbols, offset = await _pick_symbols(FakeDB(), batch_size=15)
    assert symbols == []
    assert offset == 0


# ── run_news_feeders_tick integration ────────────────────────────


@pytest.mark.asyncio
async def test_tick_skips_outside_market_hours():
    fake_db = AsyncMock()
    with patch("services.news_feeders_scheduler.datetime") as mock_dt:
        # Saturday → off-hours
        mock_dt.now.return_value = datetime(2026, 2, 7, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        result = await run_news_feeders_tick(fake_db)

    assert result["status"] == "skipped"
    assert result["reason"] == "outside_market_hours"


@pytest.mark.asyncio
async def test_tick_runs_both_feeders_and_advances_offset():
    class FakeDB:
        def __init__(self):
            self.saved_offset = None
        @property
        def scheduler_state(self):
            parent = self
            class _SS:
                async def find_one(self, *a, **kw):
                    return {"offset": 0}
                async def update_one(self, query, update, upsert=False):
                    parent.saved_offset = update["$set"]["offset"]
            return _SS()

    fake_db = FakeDB()
    tier_a = [{"symbol": f"SYM{i}", "tier": "A", "rank": i} for i in range(20)]

    with patch(
        "services.news_feeders_scheduler.datetime"
    ) as mock_dt, patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=tier_a),
    ), patch(
        "services.news_shock_feeder.batch_feed_symbols",
        new=AsyncMock(return_value={"fed": 15, "skipped": 0, "total_articles": 30}),
    ) as mock_benz, patch(
        "services.av_sentiment_feeder.batch_feed_sentiment",
        new=AsyncMock(return_value={"fed": 15, "skipped": 0, "avg_sentiment_abs": 0.2}),
    ) as mock_av:
        # Wednesday 15:00 UTC → in session
        mock_dt.now.return_value = datetime(2026, 2, 4, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        result = await run_news_feeders_tick(fake_db)

    assert result["status"] == "ran"
    assert result["symbols_requested"] == 15
    assert result["new_offset"] == 15
    mock_benz.assert_awaited_once()
    mock_av.assert_awaited_once()
    # Offset must be persisted
    assert fake_db.saved_offset == 15


@pytest.mark.asyncio
async def test_tick_advances_offset_even_when_a_feeder_fails():
    """Benzinga fails, AV succeeds — the offset must still advance
    (baseline will be biased toward AV only this tick, but next tick
    won't re-feed the same symbols)."""
    class FakeDB:
        def __init__(self):
            self.saved_offset = None
        @property
        def scheduler_state(self):
            parent = self
            class _SS:
                async def find_one(self, *a, **kw):
                    return {"offset": 5}
                async def update_one(self, query, update, upsert=False):
                    parent.saved_offset = update["$set"]["offset"]
            return _SS()

    fake_db = FakeDB()
    tier_a = [{"symbol": f"SYM{i}", "tier": "A", "rank": i} for i in range(100)]

    with patch(
        "services.news_feeders_scheduler.datetime"
    ) as mock_dt, patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=tier_a),
    ), patch(
        "services.news_shock_feeder.batch_feed_symbols",
        new=AsyncMock(side_effect=RuntimeError("benzinga down")),
    ), patch(
        "services.av_sentiment_feeder.batch_feed_sentiment",
        new=AsyncMock(return_value={"fed": 15, "skipped": 0, "avg_sentiment_abs": 0.3}),
    ):
        mock_dt.now.return_value = datetime(2026, 2, 4, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        result = await run_news_feeders_tick(fake_db)

    assert result["status"] == "ran"
    # Benzinga died → zero fed; AV succeeded → 15 fed
    assert result["benzinga"]["fed"] == 0
    assert result["av"]["fed"] == 15
    assert fake_db.saved_offset == 20  # 5 + 15


@pytest.mark.asyncio
async def test_tick_short_circuits_on_empty_tier_a():
    class FakeDB:
        @property
        def scheduler_state(self):
            class _SS:
                async def find_one(self, *a, **kw):
                    return None
            return _SS()

    with patch(
        "services.news_feeders_scheduler.datetime"
    ) as mock_dt, patch(
        "services.top_universe_service._get_active_tiered_symbols",
        new=AsyncMock(return_value=[]),
    ):
        mock_dt.now.return_value = datetime(2026, 2, 4, 15, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        result = await run_news_feeders_tick(FakeDB())

    assert result["status"] == "skipped"
    assert result["reason"] == "tier_a_empty"
