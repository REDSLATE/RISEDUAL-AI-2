"""P0 regression: market_daily normalization on BOTH paths + downstream _snapshot_symbol correctness.

Root cause (2026-06): providers return newest-first daily bars but every
consumer reads ``bars[-1]`` as today. ``_normalize_daily`` fixes it at the
single choke point ``services.market_data_pool.market_daily``. This test
locks the contract on:
  1. fresh-fetch path (monkeypatch market_pool.execute -> descending)
  2. cache-read path (seed _db.price_cache with descending 'data')
  3. downstream _snapshot_symbol picks the LATEST bar as "today"
  4. market-data integrity guard still holds and does not false-flag
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import market_data_pool
from services.market_data_pool import _normalize_daily, market_daily


def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _bars_desc(dates):
    """Newest-first, like every provider actually returns."""
    return [
        {"date": d, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0 + i * 0.1, "volume": 1000 + i}
        for i, d in enumerate(dates)
    ]


# ─── _normalize_daily unit ───────────────────────────────────────────

def test_normalize_daily_descending_input():
    out = _normalize_daily(_bars_desc(["2026-09-14", "2026-09-11", "2026-04-22"]))
    assert [b["date"] for b in out] == ["2026-04-22", "2026-09-11", "2026-09-14"]
    assert out[-1]["date"] == "2026-09-14"


def test_normalize_daily_empty_list():
    assert _normalize_daily([]) == []


def test_normalize_daily_none():
    assert _normalize_daily(None) is None


# ─── fresh-fetch path: monkeypatch market_pool.execute ───────────────

def test_market_daily_fresh_fetch_normalizes(monkeypatch):
    """When cache miss and pool returns newest-first, market_daily must sort ascending."""
    monkeypatch.setattr(market_data_pool, "_db", None)  # force cache miss

    desc = _bars_desc(["2026-09-14", "2026-09-11", "2026-09-10", "2026-04-22"])

    async def fake_execute(task):
        return desc

    # patch execute; if pool not available it short-circuits, so also patch available via type
    monkeypatch.setattr(market_data_pool.market_pool, "execute", fake_execute)
    monkeypatch.setattr(
        type(market_data_pool.market_pool),
        "available",
        property(lambda self: True),
    )

    out = asyncio.run(market_daily("AAPL"))
    assert out is not None
    dates = [b["date"] for b in out]
    assert dates == sorted(dates), f"expected ascending, got {dates}"
    assert out[-1]["date"] == "2026-09-14"


# ─── cache-read path: seed _db.price_cache with descending 'data' ────

def test_market_daily_cache_read_normalizes(monkeypatch):
    """A pre-fix cache entry stored newest-first must be served ascending."""
    desc = _bars_desc(["2026-09-14", "2026-09-11", "2026-04-22"])

    fake_db = MagicMock()
    fake_db.price_cache.find_one = AsyncMock(return_value={"key": "pool_daily_AAPL_compact", "data": desc})
    monkeypatch.setattr(market_data_pool, "_db", fake_db)

    out = asyncio.run(market_daily("AAPL"))
    assert out is not None
    dates = [b["date"] for b in out]
    assert dates == sorted(dates)
    assert out[-1]["date"] == "2026-09-14"


# ─── downstream: _snapshot_symbol picks LATEST bar as "today" ────────

def _fresh_bars_ascending():
    """Build fresh ascending bars where 'today' is within max_lag_days=4."""
    today = date.today()
    d0 = _iso(today)
    d1 = _iso(today - timedelta(days=1))
    d2 = _iso(today - timedelta(days=2))
    d3 = _iso(today - timedelta(days=3))
    # ascending: oldest first, newest last (what market_daily returns after fix)
    return [
        {"date": d3, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1_000_000},
        {"date": d2, "open": 100, "high": 102, "low": 99, "close": 101.0, "volume": 1_100_000},
        {"date": d1, "open": 101, "high": 103, "low": 100, "close": 102.0, "volume": 1_050_000},  # prev
        {"date": d0, "open": 102, "high": 104, "low": 101, "close": 103.02, "volume": 1_200_000},  # today
    ]


def test_snapshot_symbol_reads_latest_bar(monkeypatch):
    from services import alpha_day_trader

    bars = _fresh_bars_ascending()
    newest_date = bars[-1]["date"]
    prev_close = bars[-2]["close"]
    latest_close = bars[-1]["close"]
    expected_pct = (latest_close - prev_close) / prev_close * 100.0

    async def fake_daily(sym, outputsize="compact"):
        return bars

    async def fake_quote(sym):
        return {"price": 103.02, "bid": 103.00, "ask": 103.04, "last": 103.02}

    monkeypatch.setattr("services.market_data_pool.market_daily", fake_daily)
    monkeypatch.setattr("services.market_data_pool.market_quote", fake_quote)

    snap = asyncio.run(alpha_day_trader._snapshot_symbol("AAPL"))
    assert snap is not None
    assert snap.bar_date == newest_date, f"bar_date={snap.bar_date}, expected {newest_date}"
    assert snap.data_degraded is False, f"unexpected degrade: {snap.degraded_reason}"
    assert snap.pct_change == pytest.approx(expected_pct, rel=1e-4)


# ─── integrity guard: does not false-flag a healthy price-only feed ──

def test_snapshot_price_only_quote_is_not_degraded(monkeypatch):
    """Fresh bars + price-only quote (bid/ask None) => data_degraded False."""
    from services import alpha_day_trader

    bars = _fresh_bars_ascending()

    async def fake_daily(sym, outputsize="compact"):
        return bars

    async def fake_quote(sym):
        return {"price": 103.02, "bid": None, "ask": None, "last": 103.02}

    monkeypatch.setattr("services.market_data_pool.market_daily", fake_daily)
    monkeypatch.setattr("services.market_data_pool.market_quote", fake_quote)

    snap = asyncio.run(alpha_day_trader._snapshot_symbol("AAPL"))
    assert snap is not None
    assert snap.data_degraded is False
    assert snap.quote_available is True


def test_snapshot_no_quote_is_degraded(monkeypatch):
    from services import alpha_day_trader

    bars = _fresh_bars_ascending()

    async def fake_daily(sym, outputsize="compact"):
        return bars

    async def fake_quote(sym):
        return None

    monkeypatch.setattr("services.market_data_pool.market_daily", fake_daily)
    monkeypatch.setattr("services.market_data_pool.market_quote", fake_quote)

    snap = asyncio.run(alpha_day_trader._snapshot_symbol("AAPL"))
    assert snap is not None
    assert snap.data_degraded is True
    assert "quote_fetch_failed" in snap.degraded_reason


def test_snapshot_stale_bar_is_degraded(monkeypatch):
    """When latest bar is > 4 days old, degrade with stale_bar."""
    from services import alpha_day_trader

    old = date.today() - timedelta(days=30)
    older = old - timedelta(days=1)
    bars = [
        {"date": _iso(older), "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1_000_000},
        {"date": _iso(old),   "open": 100, "high": 102, "low": 99, "close": 101.0, "volume": 1_100_000},
    ]

    async def fake_daily(sym, outputsize="compact"):
        return bars

    async def fake_quote(sym):
        return {"price": 101.0, "bid": 100.9, "ask": 101.1}

    monkeypatch.setattr("services.market_data_pool.market_daily", fake_daily)
    monkeypatch.setattr("services.market_data_pool.market_quote", fake_quote)

    snap = asyncio.run(alpha_day_trader._snapshot_symbol("AAPL"))
    assert snap is not None
    assert snap.data_degraded is True
    assert "stale_bar" in snap.degraded_reason


def test_snapshot_zero_volume_today_is_degraded(monkeypatch):
    from services import alpha_day_trader

    bars = _fresh_bars_ascending()
    bars[-1]["volume"] = 0  # zero-volume today

    async def fake_daily(sym, outputsize="compact"):
        return bars

    async def fake_quote(sym):
        return {"price": 103.02, "bid": 103.00, "ask": 103.04}

    monkeypatch.setattr("services.market_data_pool.market_daily", fake_daily)
    monkeypatch.setattr("services.market_data_pool.market_quote", fake_quote)

    snap = asyncio.run(alpha_day_trader._snapshot_symbol("AAPL"))
    assert snap is not None
    assert snap.data_degraded is True
    assert "no_today_volume" in snap.degraded_reason
