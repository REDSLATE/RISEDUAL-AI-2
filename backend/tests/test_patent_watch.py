"""Tests for the Patent Watch service.

Covers:
- Query payload composition (single / multi-condition OR)
- Row normalisation tolerates legacy + camelCase field names
- Refresh upserts dedupe on (query_id, patent_number)
- Network failures absorb into a clean error string
- list_results respects limit + query_id filter
"""
from __future__ import annotations

import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from services import patent_watch_service as pws


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        # tests don't care about ordering; just preserve insertion order
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.deletes = []
        self.updates = []

    async def insert_one(self, doc):
        self.docs.append({**doc})

    def find(self, flt=None, projection=None):
        flt = flt or {}
        matched = [
            d for d in self.docs
            if all(d.get(k) == v for k, v in flt.items())
        ]
        return FakeCursor(matched)

    async def find_one(self, flt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return {**d}
        return None

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in flt.items()):
                self.docs.pop(i)
                self.deletes.append(flt)

                class R: deleted_count = 1
                return R()

        class R: deleted_count = 0
        return R()

    async def delete_many(self, flt):
        before = len(self.docs)
        self.docs = [
            d for d in self.docs
            if not all(d.get(k) == v for k, v in flt.items())
        ]

        class R: deleted_count = before - len(self.docs)
        return R()

    async def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                self.updates.append((flt, update))

                class R: matched_count = 1
                return R()
        if upsert:
            new_doc = {**flt, **update.get("$set", {})}
            self.docs.append(new_doc)

        class R: matched_count = 0
        return R()

    async def create_index(self, *_a, **_kw):
        return None


class FakeDB:
    def __init__(self):
        self.patent_watch_queries = FakeCollection()
        self.patent_watch_results = FakeCollection()


@pytest.fixture
def fake_db():
    db = FakeDB()
    pws.set_db(db)
    yield db
    pws.set_db(None)


# ── _build_query_payload ──────────────────────────────────────────


def test_build_query_payload_empty():
    assert pws._build_query_payload({}) == ""


def test_build_query_payload_single_assignee():
    # Single-token term passes through bare (no quoting needed).
    assert pws._build_query_payload({"assignee": "OpenAI"}) == "OpenAI"


def test_build_query_payload_phrase_quotes_multiword():
    # Multi-word terms must be phrase-quoted so Lucene treats them
    # as a single phrase instead of OR-ing each token.
    assert pws._build_query_payload(
        {"assignee": "Acme Industries"},
    ) == '"Acme Industries"'


def test_build_query_payload_multi_or():
    out = pws._build_query_payload({
        "assignee": "OpenAI",
        "inventor_last": "Sutskever",
        "keyword": "transformer",
    })
    assert " OR " in out
    assert "OpenAI" in out
    assert "Sutskever" in out
    assert "transformer" in out


# ── _normalise_row ────────────────────────────────────────────────


def test_normalise_row_real_uspto_schema():
    """Matches the live response shape from api.uspto.gov as of
    Feb 2026 — the row schema this service was rebuilt against."""
    row = {
        "applicationNumberText": "19406512",
        "applicationMetaData": {
            "inventionTitle": "SYSTEMS AND METHODS FOR IMAGE GENERATION",
            "filingDate": "2025-12-02",
            "firstApplicantName": "OpenAI OpCo, LLC",
            "firstInventorName": "Aditya RAMESH",
            "earliestPublicationNumber": "US20260012345A1",
            "publicationDateBag": ["2026-03-26"],
        },
    }
    out = pws._normalise_row(row)
    assert out["patent_number"] == "US20260012345A1"
    assert out["application_number"] == "19406512"
    assert out["publication_number"] == "US20260012345A1"
    assert out["title"] == "SYSTEMS AND METHODS FOR IMAGE GENERATION"
    assert out["assignee"] == "OpenAI OpCo, LLC"
    assert out["inventor"] == "Aditya RAMESH"
    assert out["filing_date"] == "2025-12-02"
    assert out["patent_date"] == "2026-03-26"  # publication date wins
    assert "patents.google.com/patent/US20260012345A1" in out["url"]


def test_normalise_row_falls_back_to_application_number():
    """Application without a pre-grant pub yet still cacheable."""
    row = {
        "applicationNumberText": "19400000",
        "applicationMetaData": {
            "inventionTitle": "Pre-pub filing",
            "filingDate": "2025-11-01",
            "firstApplicantName": "Anthropic, PBC",
        },
    }
    out = pws._normalise_row(row)
    assert out["patent_number"] == "19400000"
    assert out["publication_number"] is None
    assert out["patent_date"] == "2025-11-01"  # filing date used as fallback
    assert "19400000" in out["url"]  # search-style URL


def test_normalise_row_legacy_snake_case_compat():
    """Older payload shape — still parseable so cache reads from
    pre-migration data don't error out."""
    row = {
        "patent_number": "11000001",
        "patent_title": "Legacy patent",
        "patent_date": "2026-01-15",
        "assignee_organization": "OpenAI",
        "inventor_name_first": "Ilya",
        "inventor_name_last": "Sutskever",
    }
    out = pws._normalise_row(row)
    assert out["patent_number"] == "11000001"
    assert out["title"] == "Legacy patent"
    assert out["assignee"] == "OpenAI"
    assert out["inventor"] == "Ilya Sutskever"


def test_normalise_row_missing_number_returns_none():
    assert pws._normalise_row({"applicationMetaData": {"inventionTitle": "no num"}}) is None
    assert pws._normalise_row({"patent_title": "no num"}) is None


# ── Query CRUD ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_query(fake_db):
    rec = await pws.create_query(label="Test", assignee="OpenAI")
    assert rec["label"] == "Test"
    assert rec["assignee"] == "OpenAI"
    assert rec["last_fetch_at"] is None
    assert "_id" not in rec
    queries = await pws.list_queries()
    assert len(queries) == 1
    assert queries[0]["id"] == rec["id"]


@pytest.mark.asyncio
async def test_delete_query_removes_results(fake_db):
    rec = await pws.create_query(label="Del", keyword="ai")
    fake_db.patent_watch_results.docs.append({
        "query_id": rec["id"], "patent_number": "1",
    })
    fake_db.patent_watch_results.docs.append({
        "query_id": "other", "patent_number": "2",
    })
    ok = await pws.delete_query(rec["id"])
    assert ok is True
    assert len(fake_db.patent_watch_queries.docs) == 0
    # Only the matching results were dropped.
    assert {d["query_id"] for d in fake_db.patent_watch_results.docs} == {"other"}


@pytest.mark.asyncio
async def test_delete_unknown_query(fake_db):
    assert await pws.delete_query("nope") is False


# ── refresh_query ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_query_unknown(fake_db):
    out = await pws.refresh_query("nope")
    assert out == {"fetched": 0, "error": "query_not_found"}


@pytest.mark.asyncio
async def test_refresh_query_happy_path(fake_db):
    rec = await pws.create_query(label="A", assignee="OpenAI")
    rows = [
        {
            "applicationNumberText": "19400001",
            "applicationMetaData": {
                "inventionTitle": "First",
                "filingDate": "2025-12-01",
                "earliestPublicationNumber": "US20260000100A1",
            },
        },
        {
            "applicationNumberText": "19400002",
            "applicationMetaData": {
                "inventionTitle": "Second",
                "filingDate": "2025-12-02",
                "earliestPublicationNumber": "US20260000200A1",
            },
        },
    ]
    with patch.object(
        pws, "_fetch_from_uspto",
        new=AsyncMock(return_value={"rows": rows, "error": None}),
    ):
        out = await pws.refresh_query(rec["id"])
    assert out == {"fetched": 2, "error": None}
    cached = await pws.list_results(query_id=rec["id"])
    assert {r["patent_number"] for r in cached} == {
        "US20260000100A1", "US20260000200A1",
    }
    refreshed = await fake_db.patent_watch_queries.find_one({"id": rec["id"]})
    assert refreshed["last_fetch_count"] == 2
    assert refreshed["last_error"] is None


@pytest.mark.asyncio
async def test_refresh_query_dedupes_on_repeat(fake_db):
    rec = await pws.create_query(label="A", assignee="OpenAI")
    rows = [{
        "applicationNumberText": "19400001",
        "applicationMetaData": {
            "inventionTitle": "Same",
            "earliestPublicationNumber": "US20260000999A1",
        },
    }]
    with patch.object(
        pws, "_fetch_from_uspto",
        new=AsyncMock(return_value={"rows": rows, "error": None}),
    ):
        await pws.refresh_query(rec["id"])
        await pws.refresh_query(rec["id"])
    cached = await pws.list_results(query_id=rec["id"])
    assert len(cached) == 1


@pytest.mark.asyncio
async def test_refresh_query_propagates_error_string(fake_db):
    rec = await pws.create_query(label="A", assignee="OpenAI")
    with patch.object(
        pws, "_fetch_from_uspto",
        new=AsyncMock(return_value={"rows": [], "error": "timeout"}),
    ):
        out = await pws.refresh_query(rec["id"])
    assert out == {"fetched": 0, "error": "timeout"}
    refreshed = await fake_db.patent_watch_queries.find_one({"id": rec["id"]})
    assert refreshed["last_error"] == "timeout"


# ── _fetch_from_uspto network failure modes ───────────────────────


@pytest.mark.asyncio
async def test_fetch_from_uspto_empty_query_short_circuits():
    out = await pws._fetch_from_uspto({})
    assert out == {"rows": [], "error": "empty_query"}


@pytest.mark.asyncio
async def test_fetch_from_uspto_404_means_zero_results_not_error(monkeypatch):
    """USPTO ODP returns HTTP 404 on zero-match queries (instead of
    200 + empty array). This must not surface as an error, or the
    UI would flash a red banner on a perfectly legitimate empty
    search."""
    import types

    class FakeResp:
        status_code = 404

        def json(self):
            return {"code": "404", "message": "Not Found"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *_a, **_kw): return FakeResp()

    monkeypatch.setattr(pws, "_USPTO_API_KEY", "sk_test")
    monkeypatch.setattr(
        pws.httpx, "AsyncClient",
        lambda *a, **kw: FakeClient(),
    )
    out = await pws._fetch_from_uspto({"assignee": "NoSuchOrg12345"})
    assert out == {"rows": [], "error": None}


@pytest.mark.asyncio
async def test_fetch_from_uspto_missing_key_short_circuits(monkeypatch):
    monkeypatch.setattr(pws, "_USPTO_API_KEY", "")
    out = await pws._fetch_from_uspto({"assignee": "OpenAI"})
    assert out == {"rows": [], "error": "missing_api_key"}


def test_get_config_status_no_key(monkeypatch):
    monkeypatch.setattr(pws, "_USPTO_API_KEY", "")
    cfg = pws.get_config_status()
    assert cfg["api_key_configured"] is False
    assert "uspto" in cfg["api_base_url"].lower()


def test_get_config_status_with_key(monkeypatch):
    monkeypatch.setattr(pws, "_USPTO_API_KEY", "sk_test")
    assert pws.get_config_status()["api_key_configured"] is True


# ── refresh_all_queries no-op ─────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_all_queries_empty(fake_db):
    out = await pws.refresh_all_queries()
    assert out == {"queries": 0, "fetched": 0, "errors": 0}
