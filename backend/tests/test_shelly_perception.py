"""Shelly Perception (Doctrine v2) — perception + malformed quarantine.

Operator directive (2026-05-12):
  "Shelly is the scribe and MongoDB is the source of truth.
   Perception is also Shelly. Any information sourced must be labeled
   according to MongoDB standards. If malformed it still must be
   labeled legacy, date, time and ID. Place malformed in a file of
   its own, numbered by the number of documents in file."

Pin every invariant of the doctrine:
  1. ``perceive()`` never raises — last-resort quarantine catches
     anything that doesn't pass the stamper.
  2. Successful perceptions land in MEMORY_COLLECTION with the full
     6-stamp doctrine + ``source`` label in metadata.
  3. Malformed perceptions land in MALFORMED_COLLECTION with
     ``legacy_id`` / ``legacy_date`` / ``legacy_time`` / ``created_at``
     / ``embedding_version`` / ``source`` / ``error`` /
     ``doc_number`` / ``raw_payload``.
  4. ``doc_number`` is sequential within MALFORMED_COLLECTION,
     atomic across concurrent writes (counter-collection upsert).
  5. ``raw_payload`` preserves the original input verbatim.
  6. Empty / unscribable payloads quarantine even when no stamper
     error fires.
  7. ``count_by_regime`` exposes the malformed bucket count.
  8. ``list_malformed`` returns rows in arrival order.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from services import shelly_memory as sm


# ── In-memory Mongo stub ────────────────────────────────────────────


class _Cursor:
    def __init__(self, items):
        self._items = list(items)

    def sort(self, field, direction=1):
        reverse = direction < 0
        self._items.sort(key=lambda r: r.get(field, 0), reverse=reverse)
        return self

    def limit(self, n):
        self._items = self._items[: int(n)]
        return self

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration  # noqa: B904


class _Coll:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        doc["_id"] = f"oid-{len(self._docs)}"
        self._docs.append(dict(doc))

    def find(self, q=None, _proj=None):
        rows = [dict(r) for r in self._docs if _matches(r, q or {})]
        for r in rows:
            r.pop("_id", None)
        return _Cursor(rows)

    async def count_documents(self, q):
        return sum(1 for r in self._docs if _matches(r, q))

    async def find_one_and_update(
        self, query, update, upsert=False, return_document=False, projection=None,
    ):
        # Find matching doc (only `_id` queries supported here).
        target = None
        for r in self._docs:
            if all(r.get(k) == v for k, v in query.items()):
                target = r
                break
        if target is None:
            if not upsert:
                return None
            target = dict(query)
            self._docs.append(target)
        # Apply $inc.
        for op, payload in update.items():
            if op == "$inc":
                for field, delta in payload.items():
                    target[field] = target.get(field, 0) + delta
        out = dict(target)
        if projection:
            keep = {k for k, v in projection.items() if v == 1}
            drop = {k for k, v in projection.items() if v == 0}
            if keep:
                out = {k: v for k, v in out.items() if k in keep}
            for k in drop:
                out.pop(k, None)
        return out


def _matches(doc: dict, q: dict) -> bool:
    if not q:
        return True
    for k, v in q.items():
        if k.startswith("metadata."):
            field = k.split(".", 1)[1]
            actual = (doc.get("metadata") or {}).get(field)
        else:
            actual = doc.get(k)
        if isinstance(v, dict):
            for op, opv in v.items():
                if op == "$gte" and not (actual is not None and actual >= opv):
                    return False
                if op == "$lte" and not (actual is not None and actual <= opv):
                    return False
        else:
            if actual != v:
                return False
    return True


class _DB:
    def __init__(self):
        self._cols: dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Coll()
        return self._cols[name]


@pytest.fixture(autouse=True)
def _disable_chroma(monkeypatch):
    monkeypatch.setattr(sm, "_get_chroma_collection", lambda: None)
    yield


# ── 1. Happy path: perception → memory lane ────────────────────────


@pytest.mark.asyncio
async def test_perceive_happy_path_lands_in_memory_collection():
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"text": "AAPL split announcement", "event_date": "2024-03-15"},
        source="chat",
    )
    assert out["ok"] is True
    assert out["lane"] == "memory"
    # Memory has the 6 doctrine stamps + source.
    doc = out["doc"]
    assert doc["text"] == "AAPL split announcement"
    md = doc["metadata"]
    assert md["event_date"] == "2024-03-15"
    assert md["regime_status"] == "legacy"
    assert md["source"] == "chat"
    # Persisted to MEMORY_COLLECTION.
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 1
    assert len(db[sm.MALFORMED_COLLECTION]._docs) == 0


@pytest.mark.asyncio
async def test_perceive_string_payload_treated_as_text():
    db = _DB()
    out = await sm.perceive(db, payload="raw chat message", source="chat")
    assert out["lane"] == "memory"
    assert out["doc"]["text"] == "raw chat message"
    assert out["doc"]["metadata"]["source"] == "chat"


@pytest.mark.asyncio
async def test_perceive_dict_without_text_serializes_to_json():
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"symbol": "TSLA", "event_date": "2024-04-01"},
        source="market_feed",
    )
    assert out["lane"] == "memory"
    # Some scribable text was derived (JSON form).
    assert out["doc"]["text"]
    assert "TSLA" in out["doc"]["text"]
    # Inherited fields from payload landed in metadata.
    assert out["doc"]["metadata"]["symbol"] == "TSLA"
    assert out["doc"]["metadata"]["event_date"] == "2024-04-01"


@pytest.mark.asyncio
async def test_perceive_explicit_metadata_wins_over_payload_inherit():
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"text": "x", "event_date": "2024-03-15"},
        source="chat",
        metadata={"event_date": "2024-04-01", "lane": "research"},
    )
    assert out["doc"]["metadata"]["event_date"] == "2024-04-01"
    assert out["doc"]["metadata"]["lane"] == "research"


# ── 2. Malformed lane ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_perceive_bad_event_date_quarantines():
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"text": "broken", "event_date": "not-a-date"},
        source="chat",
    )
    assert out["ok"] is True
    assert out["lane"] == "malformed"
    doc = out["doc"]
    # Doctrine: legacy/date/time/id ALL present.
    assert doc["legacy_id"]
    assert len(doc["legacy_date"]) == 10  # YYYY-MM-DD
    assert "T" in doc["legacy_time"]  # full ISO
    assert doc["created_at"]
    assert doc["embedding_version"] == sm.EMBEDDING_VERSION
    assert doc["source"] == "chat"
    assert "stamp_error" in doc["error"]
    assert doc["doc_number"] == 1
    # Original payload preserved verbatim.
    assert doc["raw_payload"]["text"] == "broken"
    assert doc["raw_payload"]["event_date"] == "not-a-date"
    # Memory bin untouched.
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 0
    assert len(db[sm.MALFORMED_COLLECTION]._docs) == 1


@pytest.mark.asyncio
async def test_perceive_empty_payload_quarantines():
    db = _DB()
    out = await sm.perceive(db, payload="", source="chat")
    assert out["lane"] == "malformed"
    assert out["doc"]["error"] == "empty_or_unscribable_payload"
    # Even empty string is preserved verbatim under raw_payload.
    assert out["doc"]["raw_payload"] == ""


@pytest.mark.asyncio
async def test_perceive_none_payload_quarantines():
    db = _DB()
    out = await sm.perceive(db, payload=None, source="agent")
    assert out["lane"] == "malformed"
    assert out["doc"]["raw_payload"] is None
    assert out["doc"]["source"] == "agent"


@pytest.mark.asyncio
async def test_perceive_non_serializable_payload_preserved_as_repr():
    db = _DB()

    class Weird:
        def __repr__(self):
            return "<Weird obj>"

    out = await sm.perceive(db, payload=Weird(), source="agent")
    # No text, no usable form → quarantine.
    assert out["lane"] == "malformed"
    assert out["doc"]["raw_payload"] == "<Weird obj>"


# ── 3. Sequential doc_number ───────────────────────────────────────


@pytest.mark.asyncio
async def test_doc_number_is_sequential_across_quarantines():
    db = _DB()
    for _ in range(5):
        await sm.perceive(
            db,
            payload={"event_date": "not-a-date"},
            source="chat",
        )
    rows = sorted(
        db[sm.MALFORMED_COLLECTION]._docs,
        key=lambda r: r["doc_number"],
    )
    nums = [r["doc_number"] for r in rows]
    assert nums == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_doc_number_atomic_under_concurrent_writes():
    """Concurrent quarantines must produce a contiguous sequence —
    no two docs may share the same doc_number."""
    db = _DB()
    # Fire 10 concurrent malformed perceptions.
    coros = [
        sm.perceive(db, payload={"event_date": "bad"}, source="chat")
        for _ in range(10)
    ]
    await asyncio.gather(*coros)
    nums = sorted(r["doc_number"] for r in db[sm.MALFORMED_COLLECTION]._docs)
    assert nums == list(range(1, 11))


# ── 4. Date salvage in malformed docs ──────────────────────────────


@pytest.mark.asyncio
async def test_malformed_legacy_date_salvages_from_payload():
    """If a recognisable date hides in the payload under a different
    field, the malformed doc still labels with that date."""
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"text": "x", "event_date": "not-a-date", "timestamp": "2023-06-01"},
        source="chat",
    )
    # event_date was unparseable → triggered quarantine.
    # timestamp is parseable → used as legacy_date.
    assert out["lane"] == "malformed"
    assert out["doc"]["legacy_date"] == "2023-06-01"


@pytest.mark.asyncio
async def test_malformed_legacy_date_falls_back_to_today():
    db = _DB()
    out = await sm.perceive(
        db,
        payload={"event_date": "garbage"},
        source="chat",
    )
    today = datetime.now(timezone.utc).date().isoformat()
    assert out["doc"]["legacy_date"] == today


# ── 5. count_by_regime exposes malformed bucket ────────────────────


@pytest.mark.asyncio
async def test_count_by_regime_includes_malformed():
    db = _DB()
    # 1 happy.
    await sm.perceive(
        db,
        payload={"text": "ok", "event_date": "2024-01-01"},
        source="chat",
    )
    # 2 malformed.
    await sm.perceive(db, payload={"event_date": "bad"}, source="chat")
    await sm.perceive(db, payload="", source="chat")

    counts = await sm.count_by_regime(db)
    assert counts["malformed"] == 2
    assert counts["total"] == 1  # only memory-lane rows count toward total
    assert counts["legacy"] == 1


# ── 6. list_malformed sorted in arrival order ──────────────────────


@pytest.mark.asyncio
async def test_list_malformed_returns_arrival_order():
    db = _DB()
    for i in range(3):
        await sm.perceive(
            db,
            payload={"marker": i, "event_date": "bad"},
            source="chat",
        )
    rows = await sm.list_malformed(db, limit=10)
    assert [r["doc_number"] for r in rows] == [1, 2, 3]
    assert [r["raw_payload"]["marker"] for r in rows] == [0, 1, 2]


@pytest.mark.asyncio
async def test_list_malformed_min_doc_number_filter():
    db = _DB()
    for _ in range(5):
        await sm.perceive(db, payload={"event_date": "bad"}, source="chat")
    rows = await sm.list_malformed(db, min_doc_number=3)
    assert [r["doc_number"] for r in rows] == [3, 4, 5]


# ── 7. perceive() never raises ─────────────────────────────────────


@pytest.mark.asyncio
async def test_perceive_never_raises_on_mongo_failure(monkeypatch):
    """Even if the durable Mongo write itself fails, perceive routes
    to quarantine instead of propagating the exception."""
    from services import shelly_perception as sp

    db = _DB()

    async def boom(*_a, **_kw):
        raise RuntimeError("mongo on fire")

    # Patch the symbol inside shelly_perception (where perceive
    # resolves it via `from .shelly_memory import remember`).
    monkeypatch.setattr(sp, "remember", boom)
    out = await sm.perceive(
        db,
        payload={"text": "msg", "event_date": "2024-01-01"},
        source="chat",
    )
    assert out["ok"] is True
    assert out["lane"] == "malformed"
    assert "perception_failure" in out["doc"]["error"]
    assert "RuntimeError" in out["doc"]["error"]


# ── 8. Direct quarantine_malformed contract ────────────────────────


@pytest.mark.asyncio
async def test_quarantine_malformed_stamps_all_required_fields():
    db = _DB()
    doc = await sm.quarantine_malformed(
        db,
        raw={"foo": "bar"},
        source="testlane",
        error="boom",
    )
    for field in (
        "legacy_id",
        "legacy_date",
        "legacy_time",
        "created_at",
        "embedding_version",
        "source",
        "error",
        "doc_number",
        "raw_payload",
    ):
        assert field in doc, f"missing required field: {field}"
    assert doc["source"] == "testlane"
    assert doc["error"] == "boom"
    assert doc["doc_number"] == 1
    assert doc["raw_payload"] == {"foo": "bar"}
