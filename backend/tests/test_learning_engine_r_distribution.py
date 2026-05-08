"""Tests for the `LearningEngine.get_summary` R-distribution
block and the private `_r_distribution` helper.

These don't hit MongoDB — a small async stub replays the
`find(...).sort(...).limit(...).to_list(...)` chain so we can
feed canned `r_multiple` rows and pin the output shape.

Locks the "pending trades don't poison R aggregates" invariant
(resolved-only filter) and the "never raise — reporting sidecar"
contract.
"""
from __future__ import annotations

import pytest

from ai_core.learning_engine import LearningEngine, _STATS_DOC_ID


# ── DB stubs ──────────────────────────────────────────────────────


class _Cursor:
    """Mimics a Motor cursor's fluent API for `find().sort().limit().to_list()`."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n: int):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length: int | None = None):
        if length is not None:
            return list(self._rows[:length])
        return list(self._rows)


class _TradesColl:
    def __init__(self, rows: list[dict], raise_on_find: bool = False):
        self._rows = rows
        self._raise = raise_on_find
        self.last_query: dict | None = None

    def find(self, query: dict, projection: dict | None = None):
        if self._raise:
            raise RuntimeError("simulated mongo failure")
        self.last_query = query
        return _Cursor(list(self._rows))


class _StatsColl:
    def __init__(self, doc: dict | None):
        self._doc = doc

    async def find_one(self, query: dict, projection: dict | None = None):
        if self._doc is None:
            return None
        # Simulate the `{"_id": 0}` projection by returning a shallow copy
        # sans _id — mirrors Motor semantics.
        return {k: v for k, v in self._doc.items() if k != "_id"}


class _StubDB:
    def __init__(self, trades_rows: list[dict], stats_doc: dict | None,
                 trades_raise: bool = False):
        self._trades = _TradesColl(trades_rows, raise_on_find=trades_raise)
        self._stats = _StatsColl(stats_doc)

    def __getitem__(self, name: str):
        if name == "learning_engine_trades":
            return self._trades
        if name == "learning_engine_stats":
            return self._stats
        raise KeyError(name)


# ── Empty-state behavior ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_summary_still_exposes_r_distribution_key():
    """Cold start (no stats doc yet): the `r_distribution` key must
    always be present so the admin tile JSON contract is stable."""
    le = LearningEngine(_StubDB([], None))
    out = await le.get_summary()
    assert out["r_distribution"] == {"mean_r": 0.0, "strong_r_frac": 0.0}


# ── R-distribution math ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_r_distribution_reads_resolved_trades_only():
    """The cursor query must filter `status in (win, loss)` — pending
    trades have `r_multiple=None` and would poison the mean."""
    rows = [
        {"r_multiple": 2.0},
        {"r_multiple": -1.0},
        {"r_multiple": 0.5},
    ]
    stats = {"counts": {"win": 2, "loss": 1, "total_resolved": 3},
             "running": {"r_multiple_sum": 1.5, "pnl_sum": 0.0}}
    db = _StubDB(rows, stats)
    le = LearningEngine(db)
    out = await le.get_summary()
    # 3 trades, mean = (2 + -1 + 0.5) / 3 = 0.5
    assert out["r_distribution"]["mean_r"] == pytest.approx(0.5)
    # Only |R|=2.0 is ≥ 1.5 → strong_frac = 1/3
    assert out["r_distribution"]["strong_r_frac"] == pytest.approx(1 / 3, abs=1e-3)

    # Pin the resolved-only filter — the #1 regression hazard is
    # accidentally widening this query and picking up pending rows.
    q = db._trades.last_query
    assert q is not None
    assert q["status"] == {"$in": ["win", "loss"]}
    assert q["r_multiple"] == {"$ne": None}


@pytest.mark.asyncio
async def test_r_distribution_none_rows_filtered():
    """Defensive: even if a resolved row slipped through with a None
    r_multiple, the aggregator should drop it rather than crash."""
    rows = [
        {"r_multiple": 1.0},
        {"r_multiple": None},
        {"r_multiple": -1.0},
    ]
    stats = {"counts": {"win": 1, "loss": 1, "total_resolved": 2}, "running": {}}
    le = LearningEngine(_StubDB(rows, stats))
    out = await le.get_summary()
    # Only 2 clean rows: mean = 0.
    assert out["r_distribution"]["mean_r"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_r_distribution_rounded_to_four_decimals():
    """Round output to 4 decimals — prevents noisy 17-digit float
    tails from churning the admin tile on every refresh."""
    rows = [{"r_multiple": 1 / 3}, {"r_multiple": 1 / 3}, {"r_multiple": 1 / 3}]
    stats = {"counts": {"win": 3, "total_resolved": 3}, "running": {}}
    le = LearningEngine(_StubDB(rows, stats))
    out = await le.get_summary()
    # round(0.3333333333..., 4) == 0.3333
    assert out["r_distribution"]["mean_r"] == 0.3333


@pytest.mark.asyncio
async def test_r_distribution_db_failure_never_raises():
    """Reporting sidecar contract: a transient Mongo hiccup must not
    bubble up and 500 the admin summary endpoint."""
    stats = {"counts": {"win": 1, "total_resolved": 1}, "running": {}}
    le = LearningEngine(_StubDB([], stats, trades_raise=True))
    out = await le.get_summary()
    assert out["r_distribution"] == {"mean_r": 0.0, "strong_r_frac": 0.0}
    # Core summary fields still populated — the partial failure
    # must be isolated to the r_distribution block.
    assert out["wins"] == 1
    assert out["total_resolved"] == 1
