"""Tests for `services.adversarial_logger`.

Pins down:

1. **Logging contract** — the persisted document carries every field
   a future analyser will read (timestamp, decision, scores, regime,
   phase) plus a server-issued ``decision_id`` the trade row uses.

2. **Outcome attribution** — ``derive_winner`` correctly credits Bull
   on winning longs and shorts that didn't materialise, Bear on
   losing longs and successful avoidances. Neutral on NO_TRADE
   decisions (we never measured the counterfactual).

3. **Failure swallowing** — Mongo errors must NEVER raise out of
   either logger function. They're observability data, never on
   the critical fill path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services.adversarial_logger import (
    DECISION_COLLECTION,
    derive_winner,
    log_adversarial_decision,
    update_decision_outcome,
)


# ── In-memory Mongo stub ──────────────────────────────────────────────────────


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def find_one(self, query, _proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def update_one(self, query, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))


class _FakeDB:
    def __init__(self):
        self._coll = _FakeColl()

    def __getitem__(self, key):
        assert key == DECISION_COLLECTION, f"unexpected collection: {key}"
        return self._coll


# ── log_adversarial_decision ──────────────────────────────────────────────────


def _decision_payload() -> dict:
    """Same shape as services.adversarial_core.run_adversarial_decision
    returns."""
    return {
        "timestamp": "2026-04-26T12:00:00+00:00",
        "symbol": "BTC",
        "regime": "trending",
        "phase": "shadow",
        "decision": "LONG",
        "edge_gap": 0.50,
        "risk_multiplier": 0.50,
        "bull_score": 1.00,
        "bear_score": 0.50,
        "bull_case": {
            "side": "LONG", "confidence": 0.8, "expected_r": 1.25,
            "thesis": "test", "invalidations": [],
        },
        "bear_case": {
            "side": "SHORT_OR_REJECT", "confidence": 0.5, "expected_r": 1.0,
            "thesis": "test", "invalidations": [],
        },
    }


@pytest.mark.asyncio
async def test_log_persists_full_payload_and_returns_id():
    db = _FakeDB()
    decision_id = await log_adversarial_decision(db, _decision_payload(), trade_id="t-1")
    assert decision_id is not None
    assert len(db._coll.docs) == 1
    doc = db._coll.docs[0]
    # Server-issued ID + caller's trade_id both present
    assert doc["decision_id"] == decision_id
    assert doc["trade_id"] == "t-1"
    # Decision content carried through
    assert doc["decision"] == "LONG"
    assert doc["bull_case"]["confidence"] == 0.8
    # Outcome columns initialised to None for later patching
    assert doc["final_result_r"] is None
    assert doc["winner"] is None
    # Mongo-native timestamp (not ISO string) so the index sorts properly
    assert isinstance(doc["timestamp"], datetime)


@pytest.mark.asyncio
async def test_log_returns_none_when_db_missing():
    out = await log_adversarial_decision(None, _decision_payload())
    assert out is None


@pytest.mark.asyncio
async def test_log_swallows_insert_failure():
    """A Mongo write failure must not raise out — it would crash the
    bot tick. We're observability data, not the fill path."""
    bad = _FakeDB()
    bad._coll.insert_one = AsyncMock(side_effect=RuntimeError("disk full"))
    out = await log_adversarial_decision(bad, _decision_payload())
    assert out is None


# ── derive_winner ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("decision,r,expected", [
    # Bull was right to go LONG → winning long: Bull wins.
    ("LONG", 1.0, ("bull", "bear")),
    ("LONG", 0.5, ("bull", "bear")),
    # Bull went LONG but lost → Bear was right to disagree.
    ("LONG", -0.5, ("bear", "bull")),
    ("LONG", 0.0, ("bear", "bull")),     # break-even is a loss for Bull
    # Bear vetoed; what would have happened if Bull had fired?
    # If price went UP, Bear was wrong (Bull should have fired).
    ("SHORT_OR_AVOID",  0.5, ("bull", "bear")),
    # If price went DOWN or flat, Bear was right to avoid the long.
    ("SHORT_OR_AVOID", -0.5, ("bear", "bull")),
    ("SHORT_OR_AVOID",  0.0, ("bear", "bull")),
    # NO_TRADE in shadow phase: trade fires anyway, so we
    # DO have a realised r and can attribute. Only logged
    # NO_TRADE rows in veto/full phase stay neutral (because
    # update_decision_outcome never runs on them).
    ("NO_TRADE",  0.5, ("bull", "bear")),
    ("NO_TRADE", -0.5, ("bear", "bull")),
    ("NO_TRADE",  0.0, ("bear", "bull")),
    # Empty / unknown decisions degrade to neutral.
    ("",         1.0, ("neutral", "neutral")),
])
def test_derive_winner(decision, r, expected):
    assert derive_winner(decision, r) == expected


# ── update_decision_outcome ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outcome_update_patches_existing_decision():
    db = _FakeDB()
    decision_id = await log_adversarial_decision(db, _decision_payload())
    assert decision_id is not None

    ok = await update_decision_outcome(db, decision_id, final_r=1.5)
    assert ok is True

    doc = db._coll.docs[0]
    assert doc["final_result_r"] == 1.5
    assert doc["winner"] == "bull"     # LONG + r > 0 → bull wins
    assert doc["loser"] == "bear"
    assert isinstance(doc["closed_at"], datetime)
    assert doc["closed_at"].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_outcome_update_returns_false_for_unknown_id():
    db = _FakeDB()
    ok = await update_decision_outcome(db, "no-such-id", final_r=1.0)
    assert ok is False


@pytest.mark.asyncio
async def test_outcome_update_returns_false_when_db_missing():
    ok = await update_decision_outcome(None, "any", 1.0)
    assert ok is False


@pytest.mark.asyncio
async def test_outcome_update_swallows_lookup_failure():
    bad = _FakeDB()
    bad._coll.find_one = AsyncMock(side_effect=RuntimeError("db down"))
    ok = await update_decision_outcome(bad, "any", 1.0)
    assert ok is False


@pytest.mark.asyncio
async def test_outcome_update_handles_short_or_avoid_decision():
    """Bear vetoed and price went DOWN → Bear was right; final_r ≤ 0
    on a SHORT_OR_AVOID means Bear wins."""
    db = _FakeDB()
    payload = {**_decision_payload(), "decision": "SHORT_OR_AVOID"}
    decision_id = await log_adversarial_decision(db, payload)

    await update_decision_outcome(db, decision_id, final_r=-0.5)
    doc = db._coll.docs[0]
    assert doc["winner"] == "bear"
    assert doc["loser"] == "bull"
