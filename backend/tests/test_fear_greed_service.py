"""
Regression test for the Fear & Greed dashboard widget bug
(May 2026): the gauge was stuck at ``value=50, source=default``
because the prior CNN-scrape implementation returned HTTP 418 to
our generic User-Agent and the `fear_greed_index` collection on
production was empty (cold-start gap).

The rewrite:
    1. Switched the live-fetch to alternative.me's free F&G API.
    2. Added lazy-seed-on-first-read (730 days of history).
    3. Added a daily APScheduler refresh for the trailing 30 days.

These tests pin the three invariants the rewrite must hold:
    • Live fetch: alternative.me JSON → typed ``current`` payload
    • Lazy seed: an empty store gets populated on first
      ``get_full_summary`` call.
    • Idempotent: a second ``ingest_history`` call overwrites
      existing rows in place — no dupes, no exceptions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from services import fear_greed_service as fgs


# ── In-memory Mongo-like stub (matches the surface fgs uses) ──────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def find_one(self, query, projection=None, sort=None):
        # Service only uses sort=[("date", -1)] with empty query.
        if not self._docs:
            return None
        order_desc = bool(sort and sort[0][1] == -1)
        rows = sorted(
            self._docs, key=lambda d: d["date"], reverse=order_desc
        )
        return {k: v for k, v in rows[0].items() if k != "_id"}

    def find(self, query, projection=None):
        rows = sorted(self._docs, key=lambda d: d["date"], reverse=True)
        return _FakeCursor(rows)

    async def update_one(self, filter, update, upsert=False):
        date = filter.get("date")
        new_data = update["$set"]
        for d in self._docs:
            if d.get("date") == date:
                d.update(new_data)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            self._docs.append(dict(new_data))
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def estimated_document_count(self):
        return len(self._docs)

    async def count_documents(self, query):  # noqa: ARG002
        return len(self._docs)


@pytest.fixture
def stub_collection():
    return _FakeCollection()


@pytest.fixture
def patched_service(stub_collection):
    """A FearGreedService with its `.col` swapped for the in-memory stub."""
    svc = fgs.FearGreedService.__new__(fgs.FearGreedService)
    svc.col = stub_collection
    # We never need a real Mongo client for these tests.
    svc.client = None
    svc.db = None
    return svc


# ── Test 1 — alternative.me JSON shape → typed payload ────────────


@pytest.mark.asyncio
async def test_get_current_parses_alternative_me_response(patched_service):
    fake_api = {
        "data": [
            {
                "value": "26",
                "value_classification": "Fear",
                "timestamp": "1777593600",
            }
        ]
    }
    with patch.object(
        patched_service,
        "_fetch_alternative_me",
        return_value=fake_api["data"],
    ):
        result = await patched_service.get_current()

    assert result["value"] == 26
    assert result["label"] == "Fear"
    assert result["source"] == "alternative_me_live"
    assert result["date"]  # non-empty ISO date

    # The live call upserts into Mongo as a side-effect (so the
    # next request can fall back to "historical_db" if the API blips).
    assert len(patched_service.col._docs) == 1
    assert patched_service.col._docs[0]["index"] == 26


# ── Test 2 — lazy seed populates an empty store ───────────────────


@pytest.mark.asyncio
async def test_full_summary_lazy_seeds_when_store_empty(patched_service):
    """First call against an empty store should populate it from
    alternative.me before computing averages — otherwise the gauge
    shows the "0 / EXTREME FEAR" bug observed in production."""
    history_payload = [
        {
            "value": str(50 + i % 25),
            "value_classification": "Neutral",
            "timestamp": str(1777593600 - i * 86400),
        }
        for i in range(60)
    ]
    # Live `get_current` call (limit=1) + ingest_history (limit=730).
    # We patch the underlying fetch so both calls return slices of
    # the same fake feed.
    async def fake_fetch(self, limit=1):
        return history_payload[:limit]

    with patch.object(fgs.FearGreedService, "_fetch_alternative_me", fake_fetch):
        summary = await patched_service.get_full_summary()

    # Store was empty; lazy seed wrote N rows.
    assert summary["total_records"] >= 60
    assert summary["current"]["source"] == "alternative_me_live"
    # 7-day and 30-day windows both compute non-zero averages.
    assert summary["avg_7d"] > 0
    assert summary["avg_30d"] > 0
    assert len(summary["history"]) > 0


# ── Test 3 — ingest_history is idempotent ─────────────────────────


@pytest.mark.asyncio
async def test_ingest_history_is_idempotent(patched_service):
    payload = [
        {
            "value": str(40 + i),
            "value_classification": "Fear",
            "timestamp": str(1777593600 - i * 86400),
        }
        for i in range(5)
    ]
    async def fake_fetch(self, limit=1):  # noqa: ARG001
        return payload

    with patch.object(fgs.FearGreedService, "_fetch_alternative_me", fake_fetch):
        first = await patched_service.ingest_history(days=5)
        second = await patched_service.ingest_history(days=5)

    assert first == 5
    assert second == 5  # same row count, in-place update
    assert len(patched_service.col._docs) == 5  # no dupes


# ── Test 4 — empty API + empty store falls back to default ────────


@pytest.mark.asyncio
async def test_get_current_default_when_api_and_store_empty(patched_service):
    with patch.object(
        patched_service, "_fetch_alternative_me", return_value=[]
    ):
        result = await patched_service.get_current()
    assert result == {"value": 50, "label": "Neutral", "date": "", "source": "default"}


# ── Test 5 — module-level scheduler entry point ───────────────────


@pytest.mark.asyncio
async def test_refresh_fear_greed_history_calls_30_day_window():
    """The cron-wired helper should request a 30-day window — wider
    than 1 day so backfills/corrections from alternative.me overwrite
    our stored values, narrower than the lazy-seed 730 so the
    daily refresh stays fast."""
    captured: dict[str, Any] = {}

    async def fake_ingest(self, days=730):
        captured["days"] = days
        return days

    with patch.object(fgs.FearGreedService, "ingest_history", fake_ingest):
        n = await fgs.refresh_fear_greed_history()

    assert captured["days"] == 30
    assert n == 30
