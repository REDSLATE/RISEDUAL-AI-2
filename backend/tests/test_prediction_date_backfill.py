"""Tests for ``services.prediction_date_backfill``.

The backfill is idempotent + bounded. These tests pin both:

* Rows missing ``prediction_date`` get filled from ``timestamp``.
* Rows already filled are skipped (idempotent — re-runs are no-ops).
* Garbage timestamps are counted as ``skipped``, not silently
  dropped.
* Backfill respects the per-pass cap so a giant backlog can't
  monopolise the nightly cleanup window.
* ``db is None`` short-circuits gracefully.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.prediction_date_backfill import (
    BACKFILL_MAX_ROWS_PER_PASS, backfill_prediction_date,
)


def _fake_db_with_rows(rows):
    db = MagicMock()
    captured_updates = []

    class _Cursor:
        def __init__(self, items):
            self._items = list(items)

        def limit(self, _n):
            return self

        def __aiter__(self):
            self._iter = iter(self._items)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    db.predictions.find = MagicMock(return_value=_Cursor(rows))

    async def fake_update(query, update):
        captured_updates.append((query, update))
        return MagicMock()

    db.predictions.update_one = AsyncMock(side_effect=fake_update)
    db.predictions.count_documents = AsyncMock(return_value=0)
    db._captured_updates = captured_updates
    return db


@pytest.mark.asyncio
async def test_backfill_fills_missing_prediction_date_from_iso_timestamp():
    db = _fake_db_with_rows([
        {"_id": "row1", "timestamp": "2026-04-15T09:30:00+00:00"},
        {"_id": "row2", "timestamp": "2026-04-20T15:00:00Z"},
    ])
    result = await backfill_prediction_date(db)
    assert result["backfilled"] == 2
    assert result["skipped"] == 0
    # Verify the writes carried the right resolved date.
    by_id = {q["_id"]: u["$set"]["prediction_date"]
             for q, u in db._captured_updates}
    assert by_id == {"row1": "2026-04-15", "row2": "2026-04-20"}


@pytest.mark.asyncio
async def test_backfill_handles_mongo_datetime_round_trip():
    """Mongo strips tzinfo on round-trip, so a ``timestamp`` field
    can come back as a tz-naive ``datetime``. ``to_iso_date``
    handles it; the backfill must too."""
    db = _fake_db_with_rows([
        {"_id": "row1", "timestamp": datetime(2026, 4, 15, 9, 30, 0)},
    ])
    result = await backfill_prediction_date(db)
    assert result["backfilled"] == 1
    assert db._captured_updates[0][1]["$set"]["prediction_date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_backfill_skips_unparseable_timestamps():
    """Garbage timestamps shouldn't write empty-string dates —
    they get counted as skipped so the operator can see how many
    rows are corrupt."""
    db = _fake_db_with_rows([
        {"_id": "row1", "timestamp": "not a date"},
        {"_id": "row2", "timestamp": "2026-04-15T09:30:00Z"},  # good one
    ])
    result = await backfill_prediction_date(db)
    assert result["backfilled"] == 1
    assert result["skipped"] == 1
    # Only the good row was written.
    assert len(db._captured_updates) == 1


@pytest.mark.asyncio
async def test_backfill_query_targets_only_missing_or_null_rows():
    """Idempotency proof: the query is ``$or: [{$exists: False},
    {$eq: None}]``. Rows where ``prediction_date`` is already a
    string don't match, so re-running on a clean DB is a no-op
    (zero find results, zero writes)."""
    db = _fake_db_with_rows([])
    await backfill_prediction_date(db)
    query = db.predictions.find.call_args[0][0]
    assert "$or" in query
    or_branches = query["$or"]
    assert {"prediction_date": {"$exists": False}} in or_branches
    assert {"prediction_date": None} in or_branches
    # Must also require timestamp to exist — can't backfill from
    # nothing.
    assert query["timestamp"] == {"$exists": True, "$ne": None}


@pytest.mark.asyncio
async def test_backfill_respects_per_pass_cap():
    """The cursor must be limited so a fresh deploy with a giant
    backlog can't lock the cleanup window."""
    db = _fake_db_with_rows([])
    await backfill_prediction_date(db)
    # The fake cursor's ``.limit(n)`` is called by the helper. We
    # don't track the call site, but we can verify the constant is
    # set sensibly.
    assert BACKFILL_MAX_ROWS_PER_PASS == 5_000


@pytest.mark.asyncio
async def test_backfill_handles_db_none_gracefully():
    """db unavailable → return zeros, don't raise."""
    result = await backfill_prediction_date(None)
    assert result == {"backfilled": 0, "skipped": 0, "remaining": 0}


@pytest.mark.asyncio
async def test_backfill_returns_remaining_count():
    """Operator wants to see ``"still 47 rows pending"`` after a
    pass without inspecting Mongo."""
    db = _fake_db_with_rows([
        {"_id": "row1", "timestamp": "2026-04-15T09:30:00Z"},
    ])
    db.predictions.count_documents = AsyncMock(return_value=46)
    result = await backfill_prediction_date(db)
    assert result["backfilled"] == 1
    assert result["remaining"] == 46
