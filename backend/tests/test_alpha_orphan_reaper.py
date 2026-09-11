"""Orphaned-intent reaper — every intent must terminate as
BROKER_SUBMITTED or EXECUTION_BLOCKED(reason). The reaper backfills
orphans with a synthetic ``orphaned:no_terminal_event`` reason and
records the event in the why-not-trade stream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import alpha_orphan_reaper


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def limit(self, n):
        self._rows = self._rows[:n]
        return self
    async def to_list(self, length):
        return self._rows


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.updates = []
        self.inserts = []
    def find(self, query):
        # Filter mirrors the reaper's actual query shape (submitted != True + null reject).
        cutoff = query.get("created_at", {}).get("$lt")
        out = [
            r for r in self.rows
            if (cutoff is None or (r.get("created_at") and r["created_at"] < cutoff))
            and r.get("submitted") is not True
            and not r.get("reject_reason")
        ]
        return _FakeCursor(out)
    async def update_one(self, filter_q, update):
        self.updates.append((filter_q, update))
    async def insert_one(self, doc):
        self.inserts.append(doc)


class _FakeDB:
    def __init__(self, outcomes=None):
        self.alpha_intent_outcomes = _FakeCollection(outcomes or [])
        self.alpha_observations = _FakeCollection()


@pytest.mark.asyncio
async def test_reaper_returns_no_db_shape_when_db_missing():
    result = await alpha_orphan_reaper.sweep_orphaned_intents(None)
    assert result == {"reaped": 0, "reason": "no_db"}


@pytest.mark.asyncio
async def test_reaper_backfills_orphans_older_than_window(monkeypatch):
    monkeypatch.setenv("ALPHA_ORPHAN_REAPER_SECONDS", "60")
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    new = datetime.now(timezone.utc)
    db = _FakeDB(outcomes=[
        {"_id": 1, "intent_id": "orphan-a", "symbol": "AAPL", "setup_id": "s1",
         "created_at": old, "submitted": False},
        {"_id": 2, "intent_id": "orphan-b", "symbol": "MSFT", "setup_id": "s2",
         "created_at": old, "submitted": False, "reject_reason": ""},
        # Fresh intent — should NOT be reaped
        {"_id": 3, "intent_id": "fresh", "symbol": "NVDA", "setup_id": "s3",
         "created_at": new, "submitted": False},
        # Already submitted — should NOT be reaped
        {"_id": 4, "intent_id": "done", "symbol": "TSLA", "setup_id": "s4",
         "created_at": old, "submitted": True},
        # Already has a reject reason — should NOT be reaped
        {"_id": 5, "intent_id": "already-blocked", "symbol": "SPY", "setup_id": "s5",
         "created_at": old, "submitted": False, "reject_reason": "chasing_filter"},
    ])
    result = await alpha_orphan_reaper.sweep_orphaned_intents(db)
    assert result["reaped"] == 2
    assert result["window_seconds"] == 60
    # Exactly two backfill updates, both with the orphan reason.
    assert len(db.alpha_intent_outcomes.updates) == 2
    for _flt, upd in db.alpha_intent_outcomes.updates:
        assert upd["$set"]["reject_reason"] == "orphaned:no_terminal_event"
        assert "reaped_at" in upd["$set"]
    # Exactly two observation rows written (execution_blocked stage).
    assert len(db.alpha_observations.inserts) == 2
    for obs in db.alpha_observations.inserts:
        assert obs["event"] == "execution_blocked"
        assert obs["payload"]["stage"] == "orphan_reaper"
        assert obs["payload"]["reject_reason"] == "orphaned:no_terminal_event"


@pytest.mark.asyncio
async def test_reaper_never_touches_terminal_intents():
    """Regression: reaper must NEVER rewrite an intent that already
    has a reject_reason or is submitted."""
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    db = _FakeDB(outcomes=[
        {"_id": 1, "intent_id": "submitted", "symbol": "AAPL", "created_at": old,
         "submitted": True, "reject_reason": None},
        {"_id": 2, "intent_id": "blocked", "symbol": "MSFT", "created_at": old,
         "submitted": False, "reject_reason": "chasing_filter"},
    ])
    result = await alpha_orphan_reaper.sweep_orphaned_intents(db)
    assert result["reaped"] == 0
    assert db.alpha_intent_outcomes.updates == []
    assert db.alpha_observations.inserts == []


@pytest.mark.asyncio
async def test_reaper_window_floor_prevents_race_reaping(monkeypatch):
    """The reaper must never sweep sub-minute intents — false
    positives from race conditions with the tick pipeline."""
    monkeypatch.setenv("ALPHA_ORPHAN_REAPER_SECONDS", "5")  # nonsense value
    fresh = datetime.now(timezone.utc) - timedelta(seconds=30)  # < 60s floor
    db = _FakeDB(outcomes=[
        {"_id": 1, "intent_id": "very-fresh", "symbol": "AAPL",
         "created_at": fresh, "submitted": False},
    ])
    result = await alpha_orphan_reaper.sweep_orphaned_intents(db)
    # Floor is 60s so the 30s-old intent stays safe.
    assert result["reaped"] == 0
    assert result["window_seconds"] == 60  # clamped up to the floor


@pytest.mark.asyncio
async def test_reaper_handles_scan_exception_gracefully():
    class _BrokenDB:
        class _Broken:
            def find(self, *a, **kw):
                raise RuntimeError("mongo down")
        alpha_intent_outcomes = _Broken()
        alpha_observations = _FakeCollection()
    result = await alpha_orphan_reaper.sweep_orphaned_intents(_BrokenDB())
    assert result["reaped"] == 0
    assert result["reason"].startswith("scan_error")


@pytest.mark.asyncio
async def test_reaper_returns_sample_of_reaped_intents():
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    db = _FakeDB(outcomes=[
        {"_id": i, "intent_id": f"orphan-{i}", "symbol": "AAPL",
         "setup_id": f"s{i}", "created_at": old, "submitted": False}
        for i in range(10)
    ])
    result = await alpha_orphan_reaper.sweep_orphaned_intents(db)
    # Sample capped at 5 for admin panel rendering.
    assert result["reaped"] == 10
    assert len(result["sample"]) == 5
    assert all("intent_id" in s for s in result["sample"])
