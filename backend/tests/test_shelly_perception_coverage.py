"""Doctrine v2 — full perception coverage tests.

Pins the remaining ingest paths now flow through perceive():
  1. ``agent_activity_service.log_event`` tees through
     ``perceive(source="agent_activity")`` after the existing Mongo
     write. Title + detail are composed as scribe text; severity,
     event_id, agent_event_type, and symbol land in metadata.
  2. ``news_shock_feeder._persist_catalyst_events`` (Benzinga) tees
     each persisted article through
     ``perceive(source="news.benzinga")`` with event_id, event_date,
     symbol, url, and headline in metadata.
  3. ``av_sentiment_feeder._persist_av_catalyst_events`` (Alpha
     Vantage) tees each persisted article through
     ``perceive(source="news.alpha_vantage")`` with event_id,
     event_date, symbol, url, sentiment_score, headline in
     metadata.
  4. All three are fire-and-forget: a Shelly outage NEVER breaks
     the primary write path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from services import agent_activity_service as aas
from services import av_sentiment_feeder as avf
from services import news_shock_feeder as nsf
from services import shelly_memory as sm


# ── Compact in-memory Mongo stub ───────────────────────────────────


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

    async def count_documents(self, q):
        return sum(
            1 for r in self._docs
            if all(
                r.get(k) == v for k, v in q.items()
                if not isinstance(v, dict)
            )
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

    def __getattr__(self, name):
        return self[name]


@pytest.fixture(autouse=True)
def _disable_chroma(monkeypatch):
    monkeypatch.setattr(sm, "_get_chroma_collection", lambda: None)
    yield


# ════════════════════════════════════════════════════════════════════
# 1. agent_activity_service
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_activity_log_event_tees_through_perceive():
    db = _DB()
    aas.set_db(db)

    event_id = await aas.log_event(
        type="paper_trade_open",
        title="Opened NVDA long",
        detail="Confidence 87%, regime risk-on",
        severity="success",
        symbol="NVDA",
        metadata={"position_usd": 5000.0},
    )
    assert event_id

    # Primary write to agent_activity.
    assert len(db["agent_activity"]._docs) == 1
    assert db["agent_activity"]._docs[0]["title"] == "Opened NVDA long"

    # Canonical Shelly record.
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 1
    md = shelly_rows[0]["metadata"]
    assert md["source"] == "agent_activity"
    assert md["agent_event_id"] == event_id
    assert md["agent_event_type"] == "paper_trade_open"
    assert md["severity"] == "success"
    assert md["symbol"] == "NVDA"
    # Scribe text is composed from title + detail.
    text = shelly_rows[0]["text"]
    assert "Opened NVDA long" in text
    assert "Confidence 87%" in text


@pytest.mark.asyncio
async def test_agent_activity_log_event_no_detail_uses_title_only():
    db = _DB()
    aas.set_db(db)
    await aas.log_event(
        type="info",
        title="Just a heartbeat",
        severity="info",
    )
    text = db[sm.MEMORY_COLLECTION]._docs[0]["text"]
    assert text == "Just a heartbeat"


@pytest.mark.asyncio
async def test_agent_activity_resilient_to_perceive_failure(monkeypatch):
    db = _DB()
    aas.set_db(db)

    async def boom(*_a, **_kw):
        raise RuntimeError("shelly down")

    monkeypatch.setattr(sm, "perceive", boom)
    event_id = await aas.log_event(
        type="info", title="hello", severity="info",
    )
    # Primary activity write still landed.
    assert event_id
    assert len(db["agent_activity"]._docs) == 1
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 0


# ════════════════════════════════════════════════════════════════════
# 2. news_shock_feeder (Benzinga)
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_benzinga_persist_catalyst_tees_through_perceive():
    db = _DB()
    articles = [
        {
            "id": 12345,
            "created": "2024-03-15T14:30:00Z",
            "title": "AAPL beats Q1 earnings",
            "url": "https://benzinga.com/a/12345",
        },
        {
            "id": 12346,
            "created": "2024-03-15T15:00:00Z",
            "title": "AAPL announces dividend hike",
            "url": "https://benzinga.com/a/12346",
        },
    ]
    await nsf._persist_catalyst_events(db, "AAPL", articles)

    # Primary upserts to catalyst_events.
    assert len(db["catalyst_events"]._docs) == 2

    # Canonical Shelly perception records — one per article.
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 2
    sources = {r["metadata"]["source"] for r in shelly_rows}
    assert sources == {"news.benzinga"}
    event_ids = {r["metadata"]["event_id"] for r in shelly_rows}
    assert event_ids == {"benzinga:12345", "benzinga:12346"}
    # Event date collapsed to UTC YYYY-MM-DD via Shelly normalizer.
    assert {r["metadata"]["event_date"] for r in shelly_rows} == {"2024-03-15"}
    # Headline preserved as scribe text + metadata.
    headlines = {r["text"] for r in shelly_rows}
    assert "AAPL beats Q1 earnings" in headlines


@pytest.mark.asyncio
async def test_benzinga_resilient_to_perceive_failure(monkeypatch):
    db = _DB()

    async def boom(*_a, **_kw):
        raise RuntimeError("shelly down")

    monkeypatch.setattr(sm, "perceive", boom)
    articles = [{
        "id": 999,
        "created": "2024-03-15T14:30:00Z",
        "title": "Headline",
        "url": "http://x",
    }]
    # Must not raise.
    await nsf._persist_catalyst_events(db, "AAPL", articles)
    # Primary catalyst_events write still succeeded.
    assert len(db["catalyst_events"]._docs) == 1


# ════════════════════════════════════════════════════════════════════
# 3. av_sentiment_feeder (Alpha Vantage)
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_av_persist_catalyst_tees_through_perceive():
    db = _DB()
    feed = [
        {
            "url": "https://av.com/article/1",
            "time_published": "20240315T143000",
            "title": "TSLA sentiment story",
            "overall_sentiment_score": 0.65,
        },
        {
            "url": "https://av.com/article/2",
            "time_published": "20240315T150000",
            "title": "TSLA bearish piece",
            "overall_sentiment_score": -0.85,
        },
    ]
    await avf._persist_av_catalyst_events(db, "TSLA", feed)

    # Primary catalyst_events upserts.
    assert len(db["catalyst_events"]._docs) == 2

    # Canonical Shelly perception.
    shelly_rows = db[sm.MEMORY_COLLECTION]._docs
    assert len(shelly_rows) == 2
    sources = {r["metadata"]["source"] for r in shelly_rows}
    assert sources == {"news.alpha_vantage"}
    # Sentiment carried in metadata (signed, clamped to [-1, 1]).
    scores = sorted(r["metadata"]["sentiment_score"] for r in shelly_rows)
    assert scores == [-0.85, 0.65]
    # event_id stable across AV runs.
    event_ids = {r["metadata"]["event_id"] for r in shelly_rows}
    assert event_ids == {"av:https://av.com/article/1", "av:https://av.com/article/2"}


@pytest.mark.asyncio
async def test_av_persist_skips_articles_with_garbage_score():
    """Defense: a non-numeric sentiment_score must NOT produce a
    perception record (the article was skipped at the primary write,
    so the tee shouldn't fire either)."""
    db = _DB()
    feed = [
        {
            "url": "https://av.com/good",
            "time_published": "20240315T143000",
            "title": "Good article",
            "overall_sentiment_score": 0.5,
        },
        {
            "url": "https://av.com/bad",
            "time_published": "20240315T143000",
            "title": "Bad article",
            "overall_sentiment_score": "not-a-number",
        },
    ]
    await avf._persist_av_catalyst_events(db, "AMD", feed)
    assert len(db["catalyst_events"]._docs) == 1
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 1
    assert db[sm.MEMORY_COLLECTION]._docs[0]["text"] == "Good article"


@pytest.mark.asyncio
async def test_av_resilient_to_perceive_failure(monkeypatch):
    db = _DB()

    async def boom(*_a, **_kw):
        raise RuntimeError("shelly down")

    monkeypatch.setattr(sm, "perceive", boom)
    feed = [{
        "url": "https://av.com/x",
        "time_published": "20240315T143000",
        "title": "X",
        "overall_sentiment_score": 0.3,
    }]
    await avf._persist_av_catalyst_events(db, "X", feed)
    # Primary catalyst_events write still succeeded.
    assert len(db["catalyst_events"]._docs) == 1
