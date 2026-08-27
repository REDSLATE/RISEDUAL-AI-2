"""Tests for Alpha's candidate-universe union source (2026-02).

Guardrails:
* Alpha must never sit on an empty universe when top_universe has
  symbols (the pre-fix bug that kept Alpha idle for 4+ hours a day).
* Operator watchlist picks always take priority over both other
  sources so the human's manual overrides get scanned first.
* All three sources are unioned + deduped; one failing source must
  not blank the whole universe.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.alpha_day_trader import _candidate_universe


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        async def _gen():
            for r in self._rows:
                yield r
        return _gen()

    async def to_list(self, length=None):
        return list(self._rows)

    def limit(self, _n):
        return self

    def sort(self, _spec):
        return self


class _FakeCollection:
    def __init__(self, rows, *, raise_on_read: bool = False,
                 aggregate_rows=None):
        self._rows = rows
        self._raise = raise_on_read
        self._agg_rows = aggregate_rows if aggregate_rows is not None else rows

    def find(self, *_args, **_kwargs):
        if self._raise:
            raise RuntimeError("mongo down")
        return _FakeCursor(self._rows)

    def aggregate(self, _pipeline):
        if self._raise:
            raise RuntimeError("mongo down")
        return _FakeCursor(self._agg_rows)


class _FakeDB:
    def __init__(self, *, operator_watchlist=(), predictions_agg=(),
                 top_universe=(),
                 fail=()):
        self.operator_watchlist = _FakeCollection(
            list(operator_watchlist),
            raise_on_read="operator_watchlist" in fail,
        )
        self.predictions = _FakeCollection(
            [], aggregate_rows=list(predictions_agg),
            raise_on_read="predictions" in fail,
        )
        self.top_universe = _FakeCollection(
            list(top_universe),
            raise_on_read="top_universe" in fail,
        )


@pytest.mark.asyncio
async def test_none_db_returns_empty():
    assert await _candidate_universe(None) == []


@pytest.mark.asyncio
async def test_top_universe_becomes_the_baseline():
    """When predictions and watchlist are empty, top_universe must
    still populate the candidate list. This is the fix for "Alpha
    sits idle for 4+ hours a day"."""
    db = _FakeDB(top_universe=[
        {"symbol": "NVDA", "tier": "A"},
        {"symbol": "AAPL", "tier": "A"},
        {"symbol": "MSFT", "tier": "A"},
    ])
    syms = await _candidate_universe(db)
    # First three must be the top_universe entries (in order); the
    # seeded-50 floor then pads out the tail so Alpha never sits on
    # a stub-sized pool.
    assert syms[:3] == ["NVDA", "AAPL", "MSFT"]
    assert len(syms) > 3   # seeded floor kicked in
    assert len(syms) <= 50


@pytest.mark.asyncio
async def test_operator_watchlist_takes_priority():
    """Human-added symbols always come first."""
    db = _FakeDB(
        operator_watchlist=[
            {"symbol": "SWVL"},
            {"symbol": "COIN"},
        ],
        predictions_agg=[{"_id": "GOOGL"}, {"_id": "NVDA"}],
        top_universe=[{"symbol": "AAPL", "tier": "A"}],
    )
    syms = await _candidate_universe(db)
    # Watchlist entries first, then predictions, then top_universe
    assert syms[:2] == ["SWVL", "COIN"]
    assert "GOOGL" in syms
    assert "NVDA" in syms
    assert "AAPL" in syms


@pytest.mark.asyncio
async def test_dedupe_across_sources():
    """A symbol appearing in multiple sources shows up exactly once
    even after the seeded-50 floor runs (SEEDED includes NVDA, AAPL,
    GOOGL, etc.)."""
    db = _FakeDB(
        operator_watchlist=[{"symbol": "GOOGL"}],
        predictions_agg=[{"_id": "GOOGL"}, {"_id": "NVDA"}],
        top_universe=[{"symbol": "GOOGL", "tier": "A"},
                       {"symbol": "AAPL", "tier": "A"}],
    )
    syms = await _candidate_universe(db)
    assert syms.count("GOOGL") == 1
    # NVDA + AAPL must still be in there (once each); the seeded
    # floor may add more names but must not duplicate the above.
    assert syms.count("NVDA") == 1
    assert syms.count("AAPL") == 1
    # First 3 slots reflect the priority ordering
    assert syms[:3] == ["GOOGL", "NVDA", "AAPL"]


@pytest.mark.asyncio
async def test_normalized_to_upper_case():
    db = _FakeDB(operator_watchlist=[{"symbol": "googl"}, {"symbol": "  aapl "}])
    syms = await _candidate_universe(db)
    # First two entries must be the case-normalised watchlist picks;
    # seeded floor may add more names after them.
    assert syms[:2] == ["GOOGL", "AAPL"]


@pytest.mark.asyncio
async def test_cap_is_respected():
    """Cap at 50 by default so a bloated universe never over-fetches."""
    watchlist = [{"symbol": f"WL{i}"} for i in range(10)]
    top = [{"symbol": f"TU{i}", "tier": "A"} for i in range(200)]
    db = _FakeDB(operator_watchlist=watchlist, top_universe=top)
    syms = await _candidate_universe(db, cap=50)
    assert len(syms) == 50
    # The 10 watchlist entries must survive the cap
    for i in range(10):
        assert f"WL{i}" in syms


@pytest.mark.asyncio
async def test_predictions_failure_does_not_blank_universe():
    """One source failing → we still return the others (fail-open)."""
    db = _FakeDB(
        operator_watchlist=[{"symbol": "SWVL"}],
        top_universe=[{"symbol": "AAPL", "tier": "A"}],
        fail=("predictions",),
    )
    syms = await _candidate_universe(db)
    assert "SWVL" in syms
    assert "AAPL" in syms


@pytest.mark.asyncio
async def test_all_sources_failing_returns_seeded_floor_not_empty():
    """Even when every Mongo source fails, Alpha must still get a
    real pool via the seeded-50 floor — no more 'collapse to nothing'."""
    db = _FakeDB(fail=("operator_watchlist", "predictions", "top_universe"))
    syms = await _candidate_universe(db)
    # Seeded floor guarantees a full pool
    assert len(syms) == 50
    assert "AAPL" in syms
    assert "NVDA" in syms


@pytest.mark.asyncio
async def test_empty_or_missing_symbol_fields_are_skipped():
    db = _FakeDB(
        operator_watchlist=[{"symbol": ""}, {"symbol": None}, {"symbol": "NVDA"}],
        predictions_agg=[{"_id": None}, {"_id": "GOOGL"}],
        top_universe=[{"symbol": None, "tier": "A"},
                       {"symbol": "AAPL", "tier": "A"}],
    )
    syms = await _candidate_universe(db)
    # Priority order still holds for the first 3 slots; seeded
    # floor may pad after.
    assert syms[:3] == ["NVDA", "GOOGL", "AAPL"]
