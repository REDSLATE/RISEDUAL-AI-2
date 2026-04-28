"""Unit tests for AI Core date-bucketed alerts.

Verifies:
  * First emit of (type, today) inserts.
  * Second same-day emit returns ``deduped: True`` (no duplicate row).
  * Different date_bucket creates a separate alert.
  * list_alerts returns most-recent first.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services import ai_core_alerts as mod


class _FakeAlertsCollection:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        if any(d["id"] == doc["id"] for d in self.docs):
            # Mimic pymongo's DuplicateKeyError shape so emit() catches it.
            raise Exception("E11000 duplicate key error collection: test ai_core_alerts")
        self.docs.append(doc)

    def find(self, query, projection=None):
        # Return self (we'll act as a chainable cursor)
        self._cur = list(self.docs)
        return self

    def sort(self, key, direction):
        if direction == -1:
            self._cur.sort(key=lambda d: d.get("created_at") or "", reverse=True)
        else:
            self._cur.sort(key=lambda d: d.get("created_at") or "")
        return self

    def limit(self, n):
        self._cur = self._cur[:n]
        return self

    async def to_list(self, length):
        return list(self._cur)[:length]


class _FakeDb:
    def __init__(self):
        self._coll = _FakeAlertsCollection()

    def __getitem__(self, name):
        assert name == "ai_core_alerts"
        return self._coll


@pytest.fixture(autouse=True)
def _reset_db_each_test():
    mod._db = None
    yield
    mod._db = None


@pytest.mark.asyncio
async def test_emit_first_call_inserts():
    db = _FakeDb()
    mod.set_db(db)
    res = await mod.emit("nightly_sweep", title="t", message="m")
    assert res["ok"] is True
    assert res["deduped"] is False
    assert res["id"].startswith("nightly_sweep:")
    assert len(db._coll.docs) == 1


@pytest.mark.asyncio
async def test_emit_same_day_dedupes():
    db = _FakeDb()
    mod.set_db(db)
    r1 = await mod.emit("nightly_sweep", title="t", message="m")
    r2 = await mod.emit("nightly_sweep", title="t", message="m")
    assert r1["deduped"] is False
    assert r2["deduped"] is True
    assert len(db._coll.docs) == 1, "duplicate must not insert"


@pytest.mark.asyncio
async def test_emit_different_date_creates_separate_row():
    db = _FakeDb()
    mod.set_db(db)
    await mod.emit("nightly_sweep", title="t", message="m1", date_bucket="2026-01-01")
    await mod.emit("nightly_sweep", title="t", message="m2", date_bucket="2026-01-02")
    assert len(db._coll.docs) == 2


@pytest.mark.asyncio
async def test_list_alerts_returns_recent_first():
    db = _FakeDb()
    mod.set_db(db)
    await mod.emit("nightly_sweep", title="t", message="early", date_bucket="2026-01-01")
    await mod.emit("nightly_sweep", title="t", message="late", date_bucket="2026-01-02")
    out = await mod.list_alerts(limit=5)
    assert len(out) == 2
    assert out[0]["message"] == "late"
    assert out[1]["message"] == "early"


@pytest.mark.asyncio
async def test_emit_handles_no_db_gracefully():
    res = await mod.emit("nightly_sweep", title="t", message="m")
    assert res["ok"] is False
    assert res["reason"] == "db_unavailable"
