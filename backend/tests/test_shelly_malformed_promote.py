"""Promote-malformed endpoint logic.

The HTTP layer is owner-gated, but the doctrine-relevant logic
(re-perceive with corrected payload + audit-stamp on success) is
the unit pinned here. We exercise the underlying ``perceive()`` +
malformed-collection contracts directly so the test stays fast and
doesn't need a FastAPI client / auth fixture.

Pins:
  1. Corrected payload → memory lane → malformed row stamped with
     ``promoted_to_memory_id`` + ``promoted_at``.
  2. ``use_raw=True`` → re-perceives the original raw_payload.
  3. Still-malformed re-perception → no stamp on the original row;
     a new malformed doc is created with its own doc_number
     (append-only audit trail).
  4. Source label inherits from the malformed row if not overridden.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from services import shelly_memory as sm


# ── Compact Mongo stub (same shape as other doctrine tests) ───────


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
                return MagicMock(modified_count=0)
            target = dict(q)
            self._docs.append(target)
        if "$set" in u:
            target.update(u["$set"])
        return MagicMock(modified_count=1)

    async def find_one(self, q=None, proj=None):
        for r in self._docs:
            if all(r.get(k) == v for k, v in (q or {}).items()):
                out = dict(r)
                out.pop("_id", None)
                return out
        return None

    async def count_documents(self, q):
        return sum(
            1 for r in self._docs
            if all(r.get(k) == v for k, v in q.items() if not isinstance(v, dict))
        )

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


# Inline the promotion logic so we test it without the FastAPI layer.
# This is the exact body of ``promote_malformed_endpoint`` minus the
# auth + HTTP boilerplate.


async def _promote(
    db, *, doc_number, corrected_payload=None, use_raw=False, source=None,
):
    row = await db[sm.MALFORMED_COLLECTION].find_one({"doc_number": int(doc_number)})
    if row is None:
        return {"error": "not_found"}
    payload = row.get("raw_payload") if (use_raw or corrected_payload is None) else corrected_payload
    src = source or row.get("source") or "unknown"
    result = await sm.perceive(db, payload=payload, source=src)
    if result.get("lane") == "memory":
        memory_id = (result.get("doc") or {}).get("id")
        await db[sm.MALFORMED_COLLECTION].update_one(
            {"doc_number": int(doc_number)},
            {"$set": {
                "promoted_to_memory_id": memory_id,
                "promoted_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    return {"promoted_from_doc_number": int(doc_number), **result}


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promote_corrected_payload_stamps_malformed_row():
    db = _DB()
    # Plant a malformed row.
    await sm.perceive(db, payload={"event_date": "not-a-date", "text": "AAPL split"}, source="chat")
    bad = db[sm.MALFORMED_COLLECTION]._docs[0]
    assert bad["doc_number"] == 1

    # Operator corrects the date.
    out = await _promote(
        db,
        doc_number=1,
        corrected_payload={"event_date": "2024-03-15", "text": "AAPL split"},
    )
    assert out["lane"] == "memory"
    new_mem_id = out["doc"]["id"]

    # Malformed row stamped with the link.
    stamped = db[sm.MALFORMED_COLLECTION]._docs[0]
    assert stamped["promoted_to_memory_id"] == new_mem_id
    assert "promoted_at" in stamped


@pytest.mark.asyncio
async def test_promote_inherits_source_when_not_overridden():
    db = _DB()
    await sm.perceive(db, payload={"event_date": "bad", "text": "msg"}, source="news.benzinga")
    out = await _promote(
        db,
        doc_number=1,
        corrected_payload={"event_date": "2024-03-15", "text": "msg"},
    )
    assert out["lane"] == "memory"
    # The canonical memory record carries the inherited source.
    assert db[sm.MEMORY_COLLECTION]._docs[0]["metadata"]["source"] == "news.benzinga"


@pytest.mark.asyncio
async def test_promote_use_raw_attempts_original_payload():
    db = _DB()
    await sm.perceive(db, payload={"text": "valid msg"}, source="chat")
    # Quarantine for a non-date-related reason — empty payload variant.
    # Actually create a "bad date" quarantine then retry with use_raw=False;
    # since the raw is still bad, expect a NEW malformed doc.
    await sm.perceive(db, payload={"event_date": "bad", "text": "x"}, source="chat")
    out = await _promote(db, doc_number=1, use_raw=True)
    # Re-perceiving the same garbage produces yet another malformed
    # doc; the original is NOT stamped.
    assert out["lane"] == "malformed"
    malformed_rows = db[sm.MALFORMED_COLLECTION]._docs
    assert len(malformed_rows) == 2
    # Original row untouched.
    assert "promoted_to_memory_id" not in malformed_rows[0]
    # New doc has its own doc_number = 2 (append-only).
    assert malformed_rows[1]["doc_number"] == 2


@pytest.mark.asyncio
async def test_promote_returns_error_for_unknown_doc_number():
    db = _DB()
    out = await _promote(db, doc_number=999, corrected_payload={"text": "x"})
    assert out["error"] == "not_found"
