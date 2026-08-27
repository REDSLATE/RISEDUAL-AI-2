"""Tests for the pattern-research audit log (2026-02).

Guardrails:
* Every assessment (blocked/forming/confirmed/invalidated) is persisted
  — we need the negatives too for later ML training
* Writes are fire-and-forget — a Mongo hiccup must never crash a tick
* Reads filter by symbol/pattern/state and return newest first
* Empty input / None db returns 0 without raising
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.alpha_pattern_research import (
    record_assessments,
    recent,
    counts_by_state,
    COLLECTION,
)
from services.alpha_classical_patterns import PatternAssessment


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self._limit: int | None = None

    def sort(self, _spec, *_a):
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    async def to_list(self, length=None):
        rows = list(self._rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class _FakeAggCursor(_FakeCursor):
    pass


class _FakeCollection:
    def __init__(self):
        self._rows: list[dict] = []
        self.raise_on_write = False

    async def insert_many(self, docs, ordered=True):
        if self.raise_on_write:
            raise RuntimeError("mongo down")
        self._rows.extend(docs)

    def find(self, q=None, projection=None):
        q = q or {}
        rows = self._rows
        if "symbol" in q:
            rows = [r for r in rows if r.get("symbol") == q["symbol"]]
        if "pattern" in q:
            rows = [r for r in rows if r.get("pattern") == q["pattern"]]
        if "state" in q:
            rows = [r for r in rows if r.get("state") == q["state"]]
        # newest first
        rows = sorted(rows, key=lambda r: r.get("created_at"), reverse=True)
        return _FakeCursor(rows)

    def aggregate(self, pipe):
        rows = list(self._rows)
        # very small pipeline emulator: [{$match}, {$group}]
        for stage in pipe:
            if "$match" in stage:
                q = stage["$match"]
                if "symbol" in q:
                    rows = [r for r in rows if r.get("symbol") == q["symbol"]]
            elif "$group" in stage:
                buckets: dict[tuple, int] = {}
                for r in rows:
                    key = (r.get("pattern"), r.get("state"))
                    buckets[key] = buckets.get(key, 0) + 1
                rows = [
                    {"_id": {"pattern": p, "state": s}, "count": c}
                    for (p, s), c in buckets.items()
                ]
        return _FakeAggCursor(rows)

    async def create_index(self, *_a, **_k):
        pass


class _FakeDB:
    def __init__(self):
        self._coll = _FakeCollection()

    def __getitem__(self, name):
        assert name == COLLECTION
        return self._coll


def _assess(pattern="double_bottom", state="confirmed", conf=0.78):
    return PatternAssessment(
        pattern=pattern, state=state, confidence=conf,
        latest_close=100.0, detail="test", neckline_or_support=99.0,
        invalidation_level=95.0,
        criteria=[{"label": "x", "met": True, "detail": "y"}],
    )


# ─── write path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_records_all_assessments_including_blocked_and_forming():
    """Every state gets persisted — the negatives are the whole point."""
    db = _FakeDB()
    assessments = [
        _assess("double_bottom", "confirmed", 0.78),
        _assess("inverse_head_and_shoulders", "forming", 0.56),
        _assess("falling_wedge", "blocked", 0.16),
    ]
    written = await record_assessments(
        db, symbol="GOOGL", assessments=assessments,
    )
    assert written == 3
    rows = await recent(db, limit=10)
    assert len(rows) == 3
    assert {r["state"] for r in rows} == {"confirmed", "forming", "blocked"}


@pytest.mark.asyncio
async def test_none_db_returns_zero():
    n = await record_assessments(
        None, symbol="X", assessments=[_assess()],
    )
    assert n == 0


@pytest.mark.asyncio
async def test_empty_symbol_returns_zero():
    db = _FakeDB()
    n = await record_assessments(db, symbol="", assessments=[_assess()])
    assert n == 0


@pytest.mark.asyncio
async def test_write_failure_is_swallowed_and_returns_zero():
    db = _FakeDB()
    db._coll.raise_on_write = True
    n = await record_assessments(
        db, symbol="X", assessments=[_assess()],
    )
    assert n == 0


@pytest.mark.asyncio
async def test_symbol_is_normalised_to_upper():
    db = _FakeDB()
    await record_assessments(db, symbol="googl", assessments=[_assess()])
    rows = await recent(db, symbol="GOOGL", limit=10)
    assert len(rows) == 1


# ─── read filters ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_by_pattern_and_state():
    db = _FakeDB()
    await record_assessments(db, symbol="X", assessments=[
        _assess("double_bottom", "confirmed"),
        _assess("double_bottom", "forming"),
        _assess("head_and_shoulders", "confirmed"),
    ])
    rows = await recent(db, pattern="double_bottom", limit=10)
    assert {r["pattern"] for r in rows} == {"double_bottom"}
    rows = await recent(db, state="confirmed", limit=10)
    assert {r["state"] for r in rows} == {"confirmed"}


@pytest.mark.asyncio
async def test_counts_by_state_rollup():
    db = _FakeDB()
    await record_assessments(db, symbol="X", assessments=[
        _assess("double_bottom", "confirmed"),
        _assess("double_bottom", "confirmed"),
        _assess("double_bottom", "forming"),
    ])
    counts = await counts_by_state(db, symbol="X")
    assert counts["double_bottom"]["confirmed"] == 2
    assert counts["double_bottom"]["forming"] == 1


@pytest.mark.asyncio
async def test_limit_is_bounded():
    db = _FakeDB()
    await record_assessments(db, symbol="X", assessments=[
        _assess() for _ in range(5)
    ])
    rows = await recent(db, limit=3)
    assert len(rows) == 3
