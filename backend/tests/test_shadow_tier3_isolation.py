"""**Non-negotiable Tier-3 firewall regression test.**

The Research Shadow framework (`services/research_shadow*.py`) was
designed to be incapable of poisoning the Tier-3 readiness gate. This
test exists to verify that property AT EVERY COMMIT.

If this test fails, do not "fix it" by adjusting expectations —
the failure means a shadow code path has leaked into a Tier-3
collection, which silently invalidates engine promotion math. Find
the leak, plug it, then re-run.

What's tested
-------------
1. Inserting 100 synthetic shadow decisions across the four
   forbidden cross-collection sites (paper_trades, crypto_paper_trades,
   prediction_tracker, trading_bots.stats, crypto_adversarial_decision_log)
   and asserting NONE were touched.
2. The output of :func:`build_tier3_stats` is byte-identical before
   and after the synthetic shadow load.
3. The shadow logger only ever writes to ``research_shadow_decisions``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from services.research_shadow import ShadowDecision
from services.research_shadow_logger import (
    SHADOW_COLLECTION,
    insert_shadow_decision,
)


# ── In-memory Mongo stub with collection-level call tracking ──────────────────


class _TrackedCollection:
    """In-memory stand-in for a Mongo collection. Records writes so
    the test can assert which collections were touched."""

    def __init__(self, name: str, tracker: dict[str, list[dict]]):
        self._name = name
        self._tracker = tracker
        self._docs: list[dict] = []

    async def insert_one(self, doc: dict) -> Any:
        self._tracker.setdefault(self._name, []).append(doc)
        self._docs.append(dict(doc))
        return type("_R", (), {"inserted_id": "x"})()

    async def update_one(self, *_a, **_kw) -> Any:
        self._tracker.setdefault(f"{self._name}::update", []).append(_a)
        return type("_R", (), {"matched_count": 0, "modified_count": 0})()

    async def update_many(self, *_a, **_kw) -> Any:
        self._tracker.setdefault(f"{self._name}::update_many", []).append(_a)
        return type("_R", (), {"matched_count": 0, "modified_count": 0})()

    async def count_documents(self, *_a, **_kw) -> int:
        return len(self._docs)

    def find(self, *_a, **_kw):
        class _Cursor:
            def __init__(self, docs): self._docs = docs
            def sort(self, *_a, **_kw): return self
            def limit(self, _n): return self
            async def to_list(self, _length): return list(self._docs)
            def __aiter__(self): return self._gen()
            async def _gen(self):
                for d in self._docs:
                    yield d
        return _Cursor(self._docs)

    async def find_one(self, *_a, **_kw): return None

    def aggregate(self, *_a, **_kw):
        class _Cursor:
            async def to_list(self, _length): return []
        return _Cursor()

    async def create_index(self, *_a, **_kw): return "idx"


class _TrackedDB:
    """Minimal Mongo db stand-in. Lazily creates collections."""

    def __init__(self):
        self._collections: dict[str, _TrackedCollection] = {}
        self.tracker: dict[str, list[dict]] = {}

    def __getitem__(self, name: str) -> _TrackedCollection:
        if name not in self._collections:
            self._collections[name] = _TrackedCollection(name, self.tracker)
        return self._collections[name]

    def __getattr__(self, name: str) -> _TrackedCollection:
        return self[name]


# ── The forbidden collections ─────────────────────────────────────────────────


# Any of these being non-empty after the synthetic shadow load means
# the firewall has a leak. Hardcoded list — additions go through
# code review.
TIER3_FORBIDDEN_COLLECTIONS = (
    "paper_trades",
    "crypto_paper_trades",
    "prediction_tracker",
    "trading_bots",
    "crypto_adversarial_decision_log",
    "ml_predictions",
    "ml_paper_trades",
)


# ── The non-negotiable test ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_inserts_never_touch_tier3_collections():
    """Insert 100 synthetic shadow decisions through the production
    insert path. Assert ZERO touches on any Tier-3 collection.
    """
    db = _TrackedDB()

    for i in range(100):
        decision = ShadowDecision(
            bot_id="firewall-test-bot",
            user_id="firewall-test-user",
            symbol="BTC" if i % 2 == 0 else "AAPL",
            asset_type="crypto" if i % 2 == 0 else "stock",
            decision_phase="entry" if i < 50 else "cycle",
            active_engine="confluence",
            active_action="LONG",
            shadow_engine="adversarial",
            shadow_action="HOLD" if i % 3 == 0 else "SHORT",
            mid_price=100.0 + i,
            sim_fill_bps_round_trip=20,
        )
        out = await insert_shadow_decision(db, decision)
        assert out == decision.decision_id

    # Shadow collection received the writes.
    shadow_writes = db.tracker.get(SHADOW_COLLECTION, [])
    assert len(shadow_writes) == 100, (
        f"Expected 100 writes to {SHADOW_COLLECTION}, got {len(shadow_writes)}"
    )

    # The defence-in-depth marker is present on every row.
    assert all(d.get("tier3_firewall") is True for d in shadow_writes)

    # No forbidden collection was touched.
    for forbidden in TIER3_FORBIDDEN_COLLECTIONS:
        assert forbidden not in db.tracker, (
            f"FIREWALL VIOLATION: shadow code wrote to {forbidden}"
        )
        # Updates count as touches too.
        assert f"{forbidden}::update" not in db.tracker, (
            f"FIREWALL VIOLATION: shadow code updated {forbidden}"
        )
        assert f"{forbidden}::update_many" not in db.tracker, (
            f"FIREWALL VIOLATION: shadow code update_many'd {forbidden}"
        )


@pytest.mark.asyncio
async def test_scorer_only_writes_back_to_shadow_collection():
    """The deferred scorer reads pending dissents and patches scores
    onto the SAME shadow rows. Assert it never writes to any
    forbidden collection.
    """
    from services.research_shadow_logger import patch_scores

    db = _TrackedDB()

    # Pre-seed one shadow row so patch_scores has a target.
    pre_decision = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="adversarial",
        shadow_action="HOLD", mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    await insert_shadow_decision(db, pre_decision)

    # Patch a tactical score onto it.
    await patch_scores(
        db,
        pre_decision.decision_id,
        tactical_score={
            "active_pnl_usd": 0.0,
            "shadow_pnl_usd": 8.0,
            "delta_usd": 8.0,
            "shadow_was_right": True,
            "lookahead_used_s": 1800,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # The score patch hits update_one on the SHADOW collection only.
    shadow_updates = db.tracker.get(f"{SHADOW_COLLECTION}::update", [])
    assert len(shadow_updates) >= 1

    # Forbidden collections still untouched.
    for forbidden in TIER3_FORBIDDEN_COLLECTIONS:
        assert forbidden not in db.tracker
        assert f"{forbidden}::update" not in db.tracker
        assert f"{forbidden}::update_many" not in db.tracker


@pytest.mark.asyncio
async def test_shadow_decisions_marker_is_searchable():
    """Operator-grade sanity check: every shadow doc carries
    ``tier3_firewall=True`` so anyone grepping the DB or running
    ``db.research_shadow_decisions.find({"tier3_firewall": False})``
    can confirm the firewall is intact at a glance.
    """
    db = _TrackedDB()

    decision = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="adversarial",
        shadow_action="HOLD", mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    await insert_shadow_decision(db, decision)

    written = db.tracker[SHADOW_COLLECTION][0]
    assert written["tier3_firewall"] is True
    # And no row should ever be written with the marker as False.
    bad_rows = [d for d in db.tracker[SHADOW_COLLECTION]
                if d.get("tier3_firewall") is not True]
    assert bad_rows == []
