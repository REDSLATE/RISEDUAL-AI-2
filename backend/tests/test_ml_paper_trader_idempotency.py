"""Idempotency guard for ml_paper_trader paper_trades inserts.

Forensic context: 2026-04-16 produced two AAPL/down twin rows
12 seconds apart with identical fields. The fix is a Mongo
unique partial index on
    (ticker, direction, prediction_id, time_bucket)
where time_bucket = floor(opened_at_unix / 60). These tests cover:

- _time_bucket_for produces stable minute floors.
- ensure_indexes is a no-op when db is None (safe for sync init).
- ensure_indexes builds the right index spec.
- Re-running ensure_indexes is idempotent (best-effort warn-and-swallow).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services import ml_paper_trader as mpt


# ── _time_bucket_for ──────────────────────────────────────────────


def test_time_bucket_floors_to_60s():
    t = datetime(2026, 4, 16, 19, 18, 20, tzinfo=timezone.utc)
    bucket = mpt._time_bucket_for(t)
    # 60-second floor — both 19:18:20 and 19:18:32 must land in
    # the same bucket so the AAPL twin scenario is blocked.
    t2 = datetime(2026, 4, 16, 19, 18, 32, tzinfo=timezone.utc)
    assert mpt._time_bucket_for(t2) == bucket


def test_time_bucket_changes_at_minute_boundary():
    t1 = datetime(2026, 4, 16, 19, 18, 59, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 16, 19, 19, 0, tzinfo=timezone.utc)
    assert mpt._time_bucket_for(t1) != mpt._time_bucket_for(t2)
    assert mpt._time_bucket_for(t2) - mpt._time_bucket_for(t1) == 1


def test_time_bucket_is_int():
    t = datetime.now(timezone.utc)
    assert isinstance(mpt._time_bucket_for(t), int)


# ── ensure_indexes ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_noop_when_db_missing():
    # Should not raise — sync-init path may pass None.
    await mpt.ensure_indexes(None)


@pytest.mark.asyncio
async def test_ensure_indexes_builds_correct_spec():
    create_index = AsyncMock(return_value="paper_trades_idempotency")

    class FakeColl:
        def __init__(self):
            self.create_index = create_index

    class FakeDB:
        def __init__(self):
            self._coll = FakeColl()

        def __getitem__(self, key):
            assert key == "paper_trades"
            return self._coll

    await mpt.ensure_indexes(FakeDB())

    # Inspect the call: keys, unique flag, and partial filter.
    args, kwargs = create_index.call_args
    keys = args[0]
    assert keys == [
        ("ticker", 1),
        ("direction", 1),
        ("prediction_id", 1),
        ("time_bucket", 1),
    ]
    assert kwargs["unique"] is True
    assert kwargs["name"] == "paper_trades_idempotency"
    # Partial filter must require prediction_id to be a real string —
    # legacy / external inserts without one bypass the guard.
    pf = kwargs["partialFilterExpression"]
    assert pf == {"prediction_id": {"$exists": True, "$type": "string"}}


@pytest.mark.asyncio
async def test_ensure_indexes_swallows_errors():
    """Index creation must never crash startup. A flaky Mongo or
    a stale conflicting index should warn-and-continue."""

    class BoomColl:
        async def create_index(self, *a, **kw):
            raise RuntimeError("simulated mongo hiccup")

    class FakeDB:
        def __getitem__(self, _):
            return BoomColl()

    # Should not raise.
    await mpt.ensure_indexes(FakeDB())
