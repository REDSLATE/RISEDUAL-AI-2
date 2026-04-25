"""Tests for paper-trading progress + conviction clamp canary services.

Uses in-memory fakes (no real Mongo) because these services are pure
read-side aggregators — we only need to validate the counting logic
and the fail-closed behaviour.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import pytest

from services.conviction_clamp_canary import conviction_clamp_counter
from services.paper_trading_progress import (
    TIER3_MIN_LIVE_DAYS,
    compute_live_days,
    resolve_live_days,
    tier3_progress,
)


# ────────────────────────────────────────────────────────────────────────────────
# Tiny fake Mongo — just enough surface for these two services.
# ────────────────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = list(rows)

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length: int | None = None):
        out = self._rows if length is None else self._rows[:length]
        return list(out)

    def limit(self, _n):
        return self


class _FakeCollection:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def aggregate(self, pipeline: list[dict]) -> _FakeCursor:
        return _FakeCursor(self._run_pipeline(pipeline))

    def find(self, query: dict, projection: dict | None = None) -> _FakeCursor:  # noqa: ARG002
        return _FakeCursor(list(self._rows))

    async def count_documents(self, query: dict) -> int:  # noqa: ARG002
        return len(self._rows)

    async def find_one(
        self,
        query: dict,  # noqa: ARG002
        sort: list | None = None,
        projection: dict | None = None,  # noqa: ARG002
    ):
        if not self._rows:
            return None
        rows = list(self._rows)
        if sort:
            field, direction = sort[0]
            rows.sort(key=lambda r: r.get(field) or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=(direction == -1))
        return rows[0]

    def _run_pipeline(self, pipeline: list[dict]) -> list[dict]:
        """Hand-rolled mini-aggregator: supports the two pipelines we use."""
        rows = list(self._rows)
        for stage in pipeline:
            if "$match" in stage:
                # Only handles `opened_at: {$type: "date"}` and `$gte`
                clause = stage["$match"].get("opened_at", {})
                if "$gte" in clause:
                    since = clause["$gte"]
                    rows = [r for r in rows if r.get("opened_at") and r["opened_at"] >= since]
                else:
                    rows = [r for r in rows if isinstance(r.get("opened_at"), datetime)]
            elif "$group" in stage:
                stage["$group"]["_id"]
                # We only emit $dateToString-by-day groupings
                buckets: dict[str, int] = {}
                for r in rows:
                    d: datetime = r["opened_at"]
                    key = d.strftime("%Y-%m-%d")
                    buckets[key] = buckets.get(key, 0) + 1
                rows = [{"_id": k, "count": v} for k, v in buckets.items()]
            elif "$count" in stage:
                rows = [{stage["$count"]: len(rows)}]
            elif "$sort" in stage:
                key = next(iter(stage["$sort"]))
                rows.sort(key=lambda r: r[key])
        return rows


class _FakeDB:
    def __init__(self, paper_trades: list[dict] | None = None, predictions: list[dict] | None = None):
        self._collections = {
            "paper_trades": _FakeCollection(paper_trades or []),
            "predictions": _FakeCollection(predictions or []),
        }

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections[name]

    # Support `db.predictions.find(...)` attribute access too.
    @property
    def predictions(self) -> _FakeCollection:
        return self._collections["predictions"]


# ────────────────────────────────────────────────────────────────────────────────
# paper_trading_progress tests
# ────────────────────────────────────────────────────────────────────────────────

def _utc(y, m, d, h=12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_compute_live_days_zero_when_empty():
    db = _FakeDB(paper_trades=[])
    assert await compute_live_days(db) == 0


@pytest.mark.asyncio
async def test_compute_live_days_counts_distinct_utc_dates():
    rows = [
        {"opened_at": _utc(2026, 2, 1)},
        {"opened_at": _utc(2026, 2, 1, 15)},  # same day
        {"opened_at": _utc(2026, 2, 2)},
        {"opened_at": _utc(2026, 2, 3)},
    ]
    assert await compute_live_days(_FakeDB(paper_trades=rows)) == 3


@pytest.mark.asyncio
async def test_compute_live_days_ignores_missing_or_bad_dates():
    rows = [
        {"opened_at": _utc(2026, 2, 1)},
        {"opened_at": None},
        {"opened_at": "not-a-date"},
    ]
    assert await compute_live_days(_FakeDB(paper_trades=rows)) == 1


@pytest.mark.asyncio
async def test_compute_live_days_fails_closed():
    class _BoomDB:
        def __getitem__(self, _):
            raise RuntimeError("mongo down")
    assert await compute_live_days(_BoomDB()) == 0


@pytest.mark.asyncio
async def test_resolve_live_days_env_override_wins(monkeypatch):
    rows = [{"opened_at": _utc(2026, 2, 1)}]
    monkeypatch.setenv("RISEDUAL_LIVE_DAYS", "42")
    assert await resolve_live_days(_FakeDB(paper_trades=rows)) == 42


@pytest.mark.asyncio
async def test_resolve_live_days_ignores_blank_env(monkeypatch):
    rows = [{"opened_at": _utc(2026, 2, 1)}]
    monkeypatch.setenv("RISEDUAL_LIVE_DAYS", "")
    assert await resolve_live_days(_FakeDB(paper_trades=rows)) == 1


@pytest.mark.asyncio
async def test_resolve_live_days_ignores_garbage_env(monkeypatch):
    rows = [{"opened_at": _utc(2026, 2, 1)}]
    monkeypatch.setenv("RISEDUAL_LIVE_DAYS", "not-a-number")
    assert await resolve_live_days(_FakeDB(paper_trades=rows)) == 1


@pytest.mark.asyncio
async def test_tier3_progress_snapshot(monkeypatch):
    monkeypatch.delenv("RISEDUAL_LIVE_DAYS", raising=False)
    rows = [
        {"opened_at": _utc(2026, 1, 1)},
        {"opened_at": _utc(2026, 1, 2)},
        {"opened_at": _utc(2026, 1, 3)},
    ]
    snap = await tier3_progress(_FakeDB(paper_trades=rows))
    assert snap["days"] == 3
    assert snap["target_days"] == TIER3_MIN_LIVE_DAYS == 30
    assert snap["remaining_days"] == 27
    assert snap["progress_pct"] == 10
    assert snap["unlocked"] is False
    assert snap["total_trades"] == 3
    assert snap["window_days"] == 3


@pytest.mark.asyncio
async def test_tier3_progress_unlocked_when_over_threshold(monkeypatch):
    monkeypatch.delenv("RISEDUAL_LIVE_DAYS", raising=False)
    rows = [{"opened_at": _utc(2026, 1, d)} for d in range(1, 32)]  # 31 distinct days
    snap = await tier3_progress(_FakeDB(paper_trades=rows))
    assert snap["days"] == 31
    assert snap["unlocked"] is True
    assert snap["progress_pct"] == 100
    assert snap["remaining_days"] == 0


# ────────────────────────────────────────────────────────────────────────────────
# conviction_clamp_canary tests
# ────────────────────────────────────────────────────────────────────────────────

def _pred(grade: str, confidence: float, day: int = 15) -> dict:
    ts = _utc(2026, 2, day).isoformat()
    return {
        "verified_24h": {"grade": grade},
        "confidence": confidence,
        "timestamp": ts,
    }


@pytest.mark.asyncio
async def test_clamp_canary_reports_zero_in_healthy_state():
    """With stock GRADE_WEIGHTS (±2.0) and valid confidence ≤100,
    no row should ever touch the ±2.5 clamp."""
    rows = [
        _pred("STRONG_HIT", 100),
        _pred("STRONG_MISS", 100),
        _pred("WEAK_HIT", 80),
        _pred("NEUTRAL", 50),
    ]
    snap = await conviction_clamp_counter(_FakeDB(predictions=rows), days=30)
    assert snap["total_graded"] == 4
    assert snap["clamp_high"] == 0
    assert snap["clamp_low"] == 0
    assert snap["clamp_total"] == 0
    assert snap["status"] == "ok"


@pytest.mark.asyncio
async def test_clamp_canary_flags_runaway_weights(monkeypatch):
    """Simulate a misconfigured weight table that produces scores
    past the clamp — canary must flip to `warn`."""
    from services import conviction_service as cs
    monkeypatch.setitem(cs.GRADE_WEIGHTS, "RUNAWAY_WIN", +5.0)
    rows = [
        _pred("STRONG_HIT", 100),     # +2.0, within bounds
        _pred("RUNAWAY_WIN", 100),    # clamp HIGH
        _pred("RUNAWAY_WIN", 100),    # clamp HIGH
    ]
    snap = await conviction_clamp_counter(_FakeDB(predictions=rows), days=30)
    assert snap["total_graded"] == 3
    assert snap["clamp_high"] == 2
    assert snap["clamp_low"] == 0
    assert snap["clamp_total"] == 2
    assert snap["status"] == "warn"
    assert snap["clamp_rate_pct"] == pytest.approx(66.667, abs=0.01)


@pytest.mark.asyncio
async def test_clamp_canary_fails_closed_on_error():
    class _BoomDB:
        @property
        def predictions(self):
            raise RuntimeError("mongo down")
    snap = await conviction_clamp_counter(_BoomDB(), days=30)
    assert snap["total_graded"] == 0
    assert snap["status"] == "ok"  # never a false-positive warn


@pytest.mark.asyncio
async def test_clamp_canary_none_db_short_circuits():
    snap = await conviction_clamp_counter(None, days=30)
    assert snap["total_graded"] == 0
    assert snap["status"] == "ok"
