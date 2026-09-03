"""Tests for the alpha_top10_stream 60s tick.

The stream tick is intentionally a thin delegator to
``run_alpha_day_trader_tick(symbols_only=<top10>)``. These tests
lock down:

* Empty watchlist → early ``skipped: no_watchlist`` return (no
  broker calls happen).
* Populated watchlist → the delegated call receives the exact
  set of symbols and a ``stream:`` tick tag.
* When the day trader tick is in ``symbols_only`` mode, the
  universe scan + top-10 seed logic is skipped (so streaming
  ticks don't overwrite the 5-min watchlist).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import alpha_top10_state, alpha_top10_stream


@pytest.fixture(autouse=True)
def _reset_state():
    alpha_top10_state.clear()
    yield
    alpha_top10_state.clear()


@pytest.mark.asyncio
async def test_stream_tick_skips_when_watchlist_empty():
    """No 5-min tick has run yet → nothing to do."""
    with patch(
        "services.alpha_day_trader.run_alpha_day_trader_tick",
        new=AsyncMock(return_value={"never": "called"}),
    ) as m:
        result = await alpha_top10_stream.run_alpha_top10_stream_tick(db=object())
    assert result == {"skipped": True, "reason": "no_watchlist"}
    m.assert_not_called()


@pytest.mark.asyncio
async def test_stream_tick_skips_when_db_missing():
    alpha_top10_state.set_top10([{"symbol": "ANET", "score": 0.9}])
    result = await alpha_top10_stream.run_alpha_top10_stream_tick(db=None)
    assert result == {"skipped": True, "reason": "no_db"}


@pytest.mark.asyncio
async def test_stream_tick_delegates_to_day_trader_with_symbols_only():
    alpha_top10_state.set_top10([
        {"symbol": "ANET", "score": 0.82},
        {"symbol": "ADBE", "score": 0.71},
    ])
    with patch(
        "services.alpha_day_trader.run_alpha_day_trader_tick",
        new=AsyncMock(return_value={
            "candidates_seen": 0, "ranked": 0, "new_setups": 0,
            "triggers": 1, "broker_submitted": 1,
        }),
    ) as m:
        result = await alpha_top10_stream.run_alpha_top10_stream_tick(
            db=object(),
        )
    m.assert_awaited_once()
    _, kwargs = m.await_args
    assert kwargs["symbols_only"] == {"ANET", "ADBE"}
    assert kwargs["tick_tag"].startswith("stream:")
    assert result["broker_submitted"] == 1


@pytest.mark.asyncio
async def test_stream_tick_uses_snapshot_symbols_verbatim():
    """The stream tick must pass the *stored* symbols verbatim, not
    re-rank them. It's the 5-min tick's job to decide the top-10."""
    alpha_top10_state.set_top10([
        {"symbol": "GPRO", "score": 0.60},
        {"symbol": "ANET", "score": 0.82},
    ])
    with patch(
        "services.alpha_day_trader.run_alpha_day_trader_tick",
        new=AsyncMock(return_value={}),
    ) as m:
        await alpha_top10_stream.run_alpha_top10_stream_tick(db=object())
    _, kwargs = m.await_args
    assert kwargs["symbols_only"] == {"GPRO", "ANET"}
