"""MC2 Phase A — outcome bridge mirror test (2026-06-09).

Doctrine pin: when ``RISEDUAL_STANDALONE_MODE=1``,
``sovereign_outcome_bridge.enqueue_outcome`` MUST mirror the
outcome into ``mc2_outcomes`` in ADDITION to the legacy
``sovereign_outcomes_inbox`` write. Mirror, not replace —
flipping the env var off must not lose outcomes either way.

This is the permanent fix for the ``total_resolved=0`` Scorecard
pain. Once MC2 is on, every paper/live close lands locally and
the Scorecard becomes self-sufficient.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.mc2 import set_db as mc2_set_db
from services.sovereign_outcome_bridge import enqueue_outcome


class _FakeColl:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake")

    async def find_one(self, _q, _proj=None):
        return None  # never deduplicate in this test

    async def count_documents(self, _q):
        return len(self.inserted)


class _FakeDB:
    def __init__(self):
        self.sovereign_outcomes_inbox = _FakeColl()
        self.mc2_outcomes = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.mark.asyncio
async def test_outcome_bridge_mirrors_to_mc2_when_standalone(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    db = _FakeDB()
    mc2_set_db(db)

    out = await enqueue_outcome(
        db,
        brain="alpha", trade_id="T-1", symbol="BTC",
        direction="LONG", confidence=0.65, outcome_label="win",
        notional=25.0,
        extras={"lane": "crypto", "receipt_type": "paper"},
        sovereign_decision_id="dec-1",
        prediction_id="pred-1",
        source_signal="alpha:consensus:v1",
    )
    assert out["ok"] is True
    assert out["deduped"] is False

    # Legacy inbox written.
    assert len(db.sovereign_outcomes_inbox.inserted) == 1
    legacy_doc = db.sovereign_outcomes_inbox.inserted[0]
    assert legacy_doc["brain"] == "alpha"
    assert legacy_doc["trade_id"] == "T-1"

    # MC2 mirror also written — same provenance carried over.
    assert len(db.mc2_outcomes.inserted) == 1
    mirror_doc = db.mc2_outcomes.inserted[0]
    assert mirror_doc["brain"] == "alpha"
    assert mirror_doc["trade_id"] == "T-1"
    assert mirror_doc["symbol"] == "BTC"
    assert mirror_doc["outcome_label"] == "win"
    assert mirror_doc["sovereign_decision_id"] == "dec-1"
    assert mirror_doc["prediction_id"] == "pred-1"
    assert mirror_doc["source_signal"] == "alpha:consensus:v1"


@pytest.mark.asyncio
async def test_outcome_bridge_does_not_mirror_when_not_standalone(monkeypatch):
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    db = _FakeDB()
    mc2_set_db(db)

    out = await enqueue_outcome(
        db,
        brain="alpha", trade_id="T-2", symbol="ETH",
        direction="LONG", confidence=0.5, outcome_label="loss",
    )
    assert out["ok"] is True
    # Legacy inbox written.
    assert len(db.sovereign_outcomes_inbox.inserted) == 1
    # MC2 NOT written.
    assert len(db.mc2_outcomes.inserted) == 0
