"""Tests for the universe + composite-regime fixes (2026-02).

Operator complaint: "Alpha only analyses 20 symbols and only reads
SPY for regime." Both are now fixed:

* Tick loops through up to 50 symbols (env-tunable, bounded 1..100)
* ``fast_intraday_regime`` composites across SPY + QQQ + IWM by
  default with a majority-vote family aggregator
* When ``top_universe`` is empty, a seeded 50-symbol floor guarantees
  Alpha still has a real pool to scan
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ─── Seeded 50 floor ────────────────────────────────────────────


def test_seeded_50_has_50_symbols():
    from services.alpha_day_trader import SEEDED_50_SYMBOLS
    # Deduplicate defensively — the tuple is written by hand so a
    # dupe is easy to introduce.
    assert len(set(SEEDED_50_SYMBOLS)) == len(SEEDED_50_SYMBOLS)
    assert len(SEEDED_50_SYMBOLS) == 50


def test_seeded_50_contains_operator_named_names():
    """Names the operator explicitly requested must be present."""
    from services.alpha_day_trader import SEEDED_50_SYMBOLS
    must_have = {"AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
                  "TSLA", "JPM", "V", "JNJ", "WMT"}
    assert must_have.issubset(set(SEEDED_50_SYMBOLS))


def test_seeded_50_contains_benchmarks():
    """SPY/QQQ/IWM must be in the seeded floor so Alpha can analyze
    the benchmarks themselves (not just use them for regime context)."""
    from services.alpha_day_trader import SEEDED_50_SYMBOLS
    assert {"SPY", "QQQ", "IWM"}.issubset(set(SEEDED_50_SYMBOLS))


# ─── _max_symbols_per_tick ─────────────────────────────────────


def test_max_symbols_per_tick_default_is_50(monkeypatch):
    monkeypatch.delenv("ALPHA_MAX_SYMBOLS_PER_TICK", raising=False)
    from services.alpha_day_trader import _max_symbols_per_tick
    assert _max_symbols_per_tick() == 50


def test_max_symbols_per_tick_env_override(monkeypatch):
    monkeypatch.setenv("ALPHA_MAX_SYMBOLS_PER_TICK", "35")
    from services.alpha_day_trader import _max_symbols_per_tick
    assert _max_symbols_per_tick() == 35


def test_max_symbols_per_tick_bounded_high(monkeypatch):
    monkeypatch.setenv("ALPHA_MAX_SYMBOLS_PER_TICK", "9999")
    from services.alpha_day_trader import _max_symbols_per_tick
    assert _max_symbols_per_tick() == 100


def test_max_symbols_per_tick_bounded_low(monkeypatch):
    monkeypatch.setenv("ALPHA_MAX_SYMBOLS_PER_TICK", "-5")
    from services.alpha_day_trader import _max_symbols_per_tick
    assert _max_symbols_per_tick() == 1


def test_max_symbols_per_tick_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("ALPHA_MAX_SYMBOLS_PER_TICK", "abc")
    from services.alpha_day_trader import _max_symbols_per_tick
    assert _max_symbols_per_tick() == 50


# ─── seeded-50 floor kicks in ───────────────────────────────────


class _EmptyCursor:
    def __init__(self):
        pass

    def __aiter__(self):
        async def _g():
            if False:
                yield  # never
        return _g()

    async def to_list(self, length=None):
        return []

    def limit(self, _n):
        return self

    def sort(self, _spec):
        return self


class _EmptyCollection:
    def find(self, *a, **k):
        return _EmptyCursor()

    def aggregate(self, _p):
        return _EmptyCursor()


class _EmptyDB:
    operator_watchlist = _EmptyCollection()
    predictions = _EmptyCollection()
    top_universe = _EmptyCollection()


@pytest.mark.asyncio
async def test_seeded_floor_saves_alpha_when_all_sources_empty():
    """This is the fail-safe against the 'collapse to nothing' bug."""
    from services.alpha_day_trader import _candidate_universe
    syms = await _candidate_universe(_EmptyDB(), cap=50)
    # Must not be empty — seeded floor guarantees a full pool
    assert len(syms) == 50
    # Must include the operator's named workhorses
    assert "AAPL" in syms
    assert "NVDA" in syms
    assert "GOOGL" in syms


# ─── fast_intraday_regime composite ─────────────────────────────


def test_benchmarks_default_is_spy_qqq_iwm(monkeypatch):
    monkeypatch.delenv("ALPHA_REGIME_BENCHMARKS", raising=False)
    from services.fast_intraday_regime import _benchmarks
    assert _benchmarks() == ["SPY", "QQQ", "IWM"]


def test_benchmarks_env_override(monkeypatch):
    monkeypatch.setenv("ALPHA_REGIME_BENCHMARKS", "spy, qqq , IWM , DIA")
    from services.fast_intraday_regime import _benchmarks
    assert _benchmarks() == ["SPY", "QQQ", "IWM", "DIA"]


def test_benchmarks_dedupes_and_falls_back(monkeypatch):
    monkeypatch.setenv("ALPHA_REGIME_BENCHMARKS", "SPY,SPY, SPY")
    from services.fast_intraday_regime import _benchmarks
    assert _benchmarks() == ["SPY"]


def test_aggregate_majority_chop():
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "session_chop",
        "QQQ": "session_chop",
        "IWM": "trend_up",
    })
    assert label == "session_chop"
    assert breakdown["family"] == "chop"
    assert breakdown["family_count"] == 2


def test_aggregate_majority_up():
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "trend_up",
        "QQQ": "momentum_ignition_up",
        "IWM": "session_chop",
    })
    # Majority is UP family; modal label within the family is picked
    # (SPY = trend_up and QQQ = momentum_ignition_up so both are 1×)
    assert label in ("trend_up", "momentum_ignition_up")
    assert breakdown["family"] == "up"


def test_aggregate_no_majority_returns_mode():
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "trend_up",
        "QQQ": "session_chop",
        "IWM": "risk_off",
    })
    # No family has ≥2 → falls back to modal single label
    assert breakdown["family"] == "mixed"


def test_aggregate_all_unknown_returns_unknown():
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "UNKNOWN",
        "QQQ": "UNKNOWN",
        "IWM": "UNKNOWN",
    })
    assert label == "UNKNOWN"


def test_aggregate_ignores_unknown_and_uses_the_rest():
    """One benchmark's market_data_pool 500'd — the other two still
    produce a valid composite."""
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "UNKNOWN",
        "QQQ": "trend_up",
        "IWM": "trend_up",
    })
    assert label == "trend_up"
    assert breakdown["family"] == "up"


def test_aggregate_risk_off_is_down_family():
    """The risk_off label rolls up into the DOWN family so an
    across-the-board sell-off across 3 benchmarks correctly composites."""
    from services.fast_intraday_regime import _aggregate
    label, breakdown = _aggregate({
        "SPY": "risk_off",
        "QQQ": "trend_down",
        "IWM": "session_chop",
    })
    assert breakdown["family"] == "down"
