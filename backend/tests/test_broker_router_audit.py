"""Broker Router Audit — per-bot broker switch journal."""
from __future__ import annotations

import pytest


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self._sort_key = None
        self._sort_dir = 1
        self._limit = None

    def sort(self, key_or_pairs, direction=None):
        # motor: cur.sort("created_at", -1)  OR  cur.sort([("k", -1)])
        if direction is not None:
            self._sort_key, self._sort_dir = key_or_pairs, direction
        else:
            self._sort_key, self._sort_dir = key_or_pairs[0]
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def __aiter__(self):
        rows = list(self._rows)
        if self._sort_key:
            rows.sort(key=lambda r: r.get(self._sort_key) or "",
                      reverse=(self._sort_dir < 0))
        if self._limit is not None:
            rows = rows[: self._limit]
        return _AsyncIter(rows)


class _AsyncIter:
    def __init__(self, rows):
        self._rows = list(rows)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


class FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []
        self.raise_on_insert = False

    async def insert_one(self, doc):
        if self.raise_on_insert:
            raise RuntimeError("mongo down")
        # Simulate _id strip when read back.
        self.rows.append({k: v for k, v in doc.items() if k != "_id"})

    def find(self, query, _projection=None):
        rows = self.rows
        if query.get("bot_id"):
            rows = [r for r in rows if r.get("bot_id") == query["bot_id"]]
        return FakeCursor(rows)

    async def create_index(self, *_a, **_kw):
        return None


class FakeDB:
    def __init__(self):
        self._coll = FakeCollection()

    def __getitem__(self, name):
        assert name == "broker_router_audit"
        return self._coll


@pytest.mark.asyncio
async def test_record_switch_persists_row():
    from services.broker_router_audit import record_switch, recent
    db = FakeDB()
    await record_switch(
        db, bot_id="B1", user_id="U1", operator="ada@risedual.ai",
        from_broker="public", to_broker="moomoo",
    )
    rows = await recent(db, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["bot_id"] == "B1"
    assert row["from_broker"] == "public"
    assert row["to_broker"] == "moomoo"
    assert row["operator"] == "ada@risedual.ai"
    assert row["reason"] == "operator_switch"
    assert "created_at" in row


@pytest.mark.asyncio
async def test_record_switch_from_null():
    from services.broker_router_audit import record_switch, recent
    db = FakeDB()
    await record_switch(
        db, bot_id="B2", user_id="U1", operator="admin",
        from_broker=None, to_broker="public",
    )
    rows = await recent(db, limit=10)
    assert rows[0]["from_broker"] is None
    assert rows[0]["to_broker"] == "public"


@pytest.mark.asyncio
async def test_record_switch_normalizes_casing():
    from services.broker_router_audit import record_switch, recent
    db = FakeDB()
    await record_switch(
        db, bot_id="B3", user_id="U", operator="op",
        from_broker="PUBLIC", to_broker="MooMoo",
    )
    rows = await recent(db, limit=10)
    assert rows[0]["from_broker"] == "public"
    assert rows[0]["to_broker"] == "moomoo"


@pytest.mark.asyncio
async def test_record_switch_never_raises_on_mongo_failure():
    from services.broker_router_audit import record_switch
    db = FakeDB()
    db._coll.raise_on_insert = True
    # Must not raise even when Mongo is dead.
    await record_switch(
        db, bot_id="B4", user_id="U", operator="op",
        from_broker="public", to_broker="moomoo",
    )


@pytest.mark.asyncio
async def test_recent_filters_by_bot_id():
    from services.broker_router_audit import record_switch, recent
    db = FakeDB()
    await record_switch(db, bot_id="A", user_id="U", operator="op",
                         from_broker="public", to_broker="moomoo")
    await record_switch(db, bot_id="B", user_id="U", operator="op",
                         from_broker="moomoo", to_broker="public")
    a_rows = await recent(db, limit=10, bot_id="A")
    b_rows = await recent(db, limit=10, bot_id="B")
    assert len(a_rows) == 1 and a_rows[0]["bot_id"] == "A"
    assert len(b_rows) == 1 and b_rows[0]["bot_id"] == "B"


@pytest.mark.asyncio
async def test_recent_handles_none_db():
    from services.broker_router_audit import record_switch, recent
    # record_switch with None db is a silent no-op.
    await record_switch(None, bot_id="B", user_id="U", operator="op",
                         from_broker=None, to_broker="public")
    rows = await recent(None, limit=10)
    assert rows == []
