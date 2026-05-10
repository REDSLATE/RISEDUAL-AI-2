"""Doctrine v2 integration tests — chat & market memory tee through perceive().

Operator directive (2026-05-12):
  *"Wire chat_memory_service and market_memory_service ingest paths
   to flow through perceive() so all sourced information is
   doctrine-labeled."*

Pin the behavior:
  1. ``chat_memory_service.save_memory`` writes BOTH to
     ``chat_memories`` (UI projection, doctrine-stamped) AND to
     ``shelly_memories`` (canonical Shelly record via
     ``perceive(source="chat")``).
  2. ``market_memory_service.save_regime`` migrates its date
     pipeline onto ``_normalize_event_date`` (Shelly's boundary
     normalizer) AND tees through ``perceive(source="market_feed")``
     so every regime save leaves a canonical doctrine-labeled
     record.
  3. The ``market_memory_log`` collection now carries
     ``apply_doctrine_stamps`` labels (id / event_date /
     regime_status / created_at / embedding_version).
  4. Bad input dates in market regimes:
     - ChromaDB upsert still succeeds with a today-fallback
       (live-feed contract preserved).
     - Shelly perception tee receives the malformed payload and
       quarantines it — operator gets the trail.
  5. A Shelly outage on the perception tee NEVER breaks the
     primary write path (chat or market).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from services import chat_memory_service as cms
from services import market_memory_service as mms
from services import shelly_memory as sm
from services import shelly_perception as sp


# ── In-memory Mongo stub ────────────────────────────────────────────


class _Cursor:
    def __init__(self, items):
        self._items = list(items)

    def sort(self, *a, **k):
        if a and isinstance(a[0], str):
            field = a[0]
            direction = a[1] if len(a) > 1 else 1
            self._items.sort(key=lambda r: r.get(field, 0), reverse=(direction < 0))
        return self

    def limit(self, n):
        self._items = self._items[: int(n)]
        return self

    async def to_list(self, length=None):
        return self._items[: int(length or len(self._items))]

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
        return MagicMock(inserted_id=doc["_id"])

    async def update_one(self, q, u, upsert=False):
        target = None
        for r in self._docs:
            if all(r.get(k) == v for k, v in q.items()):
                target = r
                break
        if target is None:
            if not upsert:
                return MagicMock(modified_count=0, upserted_id=None)
            target = dict(q)
            self._docs.append(target)
        if "$set" in u:
            target.update(u["$set"])
        return MagicMock(modified_count=1, upserted_id=target.get("_id"))

    async def delete_one(self, q):
        for i, r in enumerate(self._docs):
            if all(r.get(k) == v for k, v in q.items()):
                self._docs.pop(i)
                return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)

    async def delete_many(self, q):
        before = len(self._docs)
        self._docs = [
            r for r in self._docs
            if not all(r.get(k) == v for k, v in q.items())
        ]
        return MagicMock(deleted_count=before - len(self._docs))

    def find(self, q=None, _proj=None):
        rows = [dict(r) for r in self._docs if _matches(r, q or {})]
        for r in rows:
            r.pop("_id", None)
        return _Cursor(rows)

    async def count_documents(self, q):
        return sum(1 for r in self._docs if _matches(r, q))

    async def find_one(self, q=None, proj=None):
        for r in self._docs:
            if _matches(r, q or {}):
                out = dict(r)
                out.pop("_id", None)
                return out
        return None

    async def find_one_and_update(
        self, query, update, upsert=False, return_document=False, projection=None,
    ):
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
        for op, payload in update.items():
            if op == "$inc":
                for f, d in payload.items():
                    target[f] = target.get(f, 0) + d
        out = dict(target)
        if projection:
            keep = {k for k, v in projection.items() if v == 1}
            if keep:
                out = {k: v for k, v in out.items() if k in keep}
        return out


def _matches(doc, q):
    if not q:
        return True
    for k, v in q.items():
        if k.startswith("metadata."):
            actual = (doc.get("metadata") or {}).get(k.split(".", 1)[1])
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

    def __getattr__(self, name):
        return self[name]


@pytest.fixture(autouse=True)
def _disable_chroma(monkeypatch):
    monkeypatch.setattr(sm, "_get_chroma_collection", lambda: None)
    yield


# ════════════════════════════════════════════════════════════════════
# 1. chat_memory_service.save_memory
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_chat_save_memory_writes_to_both_collections():
    """save_memory must produce TWO records: chat-projection +
    canonical Shelly perception."""
    db = _DB()
    cms.set_db(db)

    mid = await cms.save_memory(
        user_id="user-42",
        content="Bullish on NVDA for Q2 earnings",
        category="auto",
        source_session="sess-1",
    )
    assert mid.startswith("mem_")

    # Chat projection.
    chat_rows = db["chat_memories"]._docs
    assert len(chat_rows) == 1
    assert chat_rows[0]["content"] == "Bullish on NVDA for Q2 earnings"
    assert chat_rows[0]["user_id"] == "user-42"
    # Doctrine stamps applied.
    assert "id" in chat_rows[0]
    assert chat_rows[0]["embedding_version"] == sm.EMBEDDING_VERSION
    assert chat_rows[0]["metadata"]["event_date"]
    assert chat_rows[0]["metadata"]["regime_status"] in ("active", "legacy")

    # Canonical Shelly record.
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 1
    assert shelly_rows[0]["text"] == "Bullish on NVDA for Q2 earnings"
    assert shelly_rows[0]["metadata"]["source"] == "chat"
    assert shelly_rows[0]["metadata"]["user_id"] == "user-42"
    assert shelly_rows[0]["metadata"]["chat_memory_id"] == mid


@pytest.mark.asyncio
async def test_chat_save_memory_resilient_to_perceive_failure(monkeypatch):
    """If perceive() blows up at the boundary, the chat write must
    still complete — the user-facing UI cannot break on a Shelly
    hiccup."""
    db = _DB()
    cms.set_db(db)

    async def boom(*_a, **_kw):
        raise RuntimeError("shelly is unavailable")

    monkeypatch.setattr(sm, "perceive", boom)
    # Chat write still succeeds.
    mid = await cms.save_memory("user-77", "remember this", "general", "")
    assert mid
    assert len(db["chat_memories"]._docs) == 1
    # Canonical record is absent — but the chat UI is intact.


@pytest.mark.asyncio
async def test_chat_save_memory_canonical_record_has_doctrine_stamps():
    db = _DB()
    cms.set_db(db)
    await cms.save_memory("u-1", "x", "general", "")
    canonical = db[sm.MEMORY_COLLECTION]._docs[0]
    md = canonical["metadata"]
    # Full Shelly doctrine — 4 mandatory metadata fields.
    assert md["event_date"]
    assert "event_date_ordinal" in md
    assert md["regime_status"] in ("active", "legacy")
    assert md["regime_label"] == md["event_date"]


# ════════════════════════════════════════════════════════════════════
# 2. market_memory_service.save_regime
# ════════════════════════════════════════════════════════════════════


class _FakeChromaColl:
    """Minimal ChromaDB stub — collects upsert calls so tests can
    assert without needing a real Chroma persistent client."""
    def __init__(self):
        self.upserts: list[dict] = []
    def upsert(self, ids, documents, metadatas):
        self.upserts.append({"ids": ids, "documents": documents, "metadatas": metadatas})
    def count(self):
        return len(self.upserts)


@pytest.fixture
def _market_setup(monkeypatch):
    """Wire market_memory_service to a fake Chroma collection +
    fresh in-memory Mongo. Restores module globals on teardown."""
    db = _DB()
    fake_chroma = _FakeChromaColl()
    monkeypatch.setattr(mms, "_collection", fake_chroma)
    monkeypatch.setattr(mms, "_db", db)
    # Stub normalize_confidence (avoid pulling prediction_tracker stack).
    import services.prediction_tracker as pt
    monkeypatch.setattr(pt, "normalize_confidence", lambda v: float(v or 0))
    return db, fake_chroma


@pytest.mark.asyncio
async def test_market_save_regime_uses_shelly_date_normalizer(_market_setup):
    """Date pipeline migration — full ISO timestamps now collapse
    via _normalize_event_date instead of the legacy to_iso_date
    helper. Verify a tz-aware ISO input round-trips to UTC date."""
    db, fake_chroma = _market_setup
    await mms.save_regime({
        "symbol": "NVDA",
        "price": 925.50,
        "date": "2024-03-15T23:30:00-05:00",  # 04:30 UTC the next day
        "outcome": "pending",
        "confidence": 75,
    })
    # ChromaDB upsert got the UTC-coerced date.
    chroma_meta = fake_chroma.upserts[0]["metadatas"][0]
    assert chroma_meta["date"] == "2024-03-16"
    # Same date used in market_memory_log doctrine stamps.
    log_doc = db["market_memory_log"]._docs[0]
    assert log_doc["metadata"]["event_date"] == "2024-03-16"
    assert log_doc["metadata"]["regime_status"] == "legacy"


@pytest.mark.asyncio
async def test_market_save_regime_log_carries_doctrine_stamps(_market_setup):
    """market_memory_log rows must now carry the full Shelly
    doctrine stamps so the collection speaks the same vocabulary
    as shelly_memories."""
    db, _ = _market_setup
    await mms.save_regime({
        "symbol": "AAPL",
        "price": 180.0,
        "date": "2024-03-15",
        "outcome": "hit",
        "confidence": 85,
    })
    log_doc = db["market_memory_log"]._docs[0]
    # apply_doctrine_stamps adds id + embedding_version + created_at + metadata block.
    assert "id" in log_doc and len(log_doc["id"]) > 0
    assert log_doc["embedding_version"] == sm.EMBEDDING_VERSION
    assert log_doc["created_at"]
    md = log_doc["metadata"]
    assert md["event_date"] == "2024-03-15"
    assert md["event_date_ordinal"] > 0
    assert md["regime_status"] == "legacy"
    assert md["source"] == "market_feed"
    assert md["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_market_save_regime_tees_through_perceive(_market_setup):
    """Every save_regime call should also produce a canonical
    perception record in shelly_memories."""
    db, _ = _market_setup
    await mms.save_regime({
        "symbol": "TSLA",
        "price": 250.0,
        "date": "2024-04-01",
        "outcome": "miss",
        "confidence": 88,
        "prediction_id": "pred-xyz-1",
    })
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 1
    md = shelly_rows[0]["metadata"]
    assert md["source"] == "market_feed"
    assert md["symbol"] == "TSLA"
    assert md["event_date"] == "2024-04-01"
    assert md["regime_doc_id"]  # SHA256 hash from _make_id
    assert md["prediction_id"] == "pred-xyz-1"


@pytest.mark.asyncio
async def test_market_save_regime_handles_garbage_date(_market_setup):
    """Live-feed contract: a malformed `date` field must NOT block
    the ChromaDB write — falls back to today, logs a warning. The
    canonical perception tee still records the event."""
    db, fake_chroma = _market_setup
    await mms.save_regime({
        "symbol": "MSFT",
        "price": 380.0,
        "date": "not-a-date",
        "outcome": "pending",
        "confidence": 60,
    })
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chroma_meta = fake_chroma.upserts[0]["metadatas"][0]
    assert chroma_meta["date"] == today
    # Shelly tee STILL recorded the event with today's stamp.
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 1
    assert shelly_rows[0]["metadata"]["event_date"] == today


@pytest.mark.asyncio
async def test_market_save_regime_resilient_to_perceive_failure(monkeypatch, _market_setup):
    """If the Shelly perception tee blows up, the ChromaDB upsert
    and market_memory_log write must still complete."""
    db, fake_chroma = _market_setup

    async def boom(*_a, **_kw):
        raise RuntimeError("shelly down")

    # Patch at the binding the service module sees (imported lazily
    # inside save_regime).
    monkeypatch.setattr(sm, "perceive", boom)
    doc_id = await mms.save_regime({
        "symbol": "GOOG",
        "price": 145.0,
        "date": "2024-03-15",
        "outcome": "hit",
        "confidence": 70,
    })
    assert doc_id  # primary write succeeded.
    assert len(fake_chroma.upserts) == 1
    assert len(db["market_memory_log"]._docs) == 1
    # Shelly-side write is empty.
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 0


@pytest.mark.asyncio
async def test_market_save_regime_naive_datetime_normalized(_market_setup):
    """Shelly normalizer assumes naive datetimes are UTC — verify
    that contract holds end-to-end."""
    db, fake_chroma = _market_setup
    naive = datetime(2024, 3, 15, 14, 30, 0)  # no tzinfo
    await mms.save_regime({
        "symbol": "AMD",
        "price": 200.0,
        "date": naive,
        "outcome": "pending",
        "confidence": 50,
    })
    assert fake_chroma.upserts[0]["metadatas"][0]["date"] == "2024-03-15"
    assert db[sm.MEMORY_COLLECTION]._docs[0]["metadata"]["event_date"] == "2024-03-15"
