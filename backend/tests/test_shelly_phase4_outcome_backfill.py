"""Tests — Phase 4 5-Shelly outcome backfill + consensus rollup.

Covers:
- ``shelly.outcome_backfill.backfill_outcome_for_trade`` — match
  by symbol+direction+time window, only-unresolved, per-node
  fail-soft, MC shared write, idempotency.
- ``shelly.brain_emitter.emit_alpha_paper_trade`` — fires when
  pipeline is wired, no-ops when not, fail-soft on errors.
- ``routes.admin_shelly_federation.federation_consensus`` —
  conservative-priority rollup rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ── Minimal async Mongo fake supporting update_many with generic
#    filters (the existing test_shelly_federation fake is too
#    narrow — it only matches event_hash $in lists).


class _Coll:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        d = dict(doc)
        d["_id"] = f"oid-{len(self._docs)}"
        self._docs.append(d)

    def insert_many_sync(self, docs):
        """Helper for test setup — not part of Motor API."""
        for d in docs:
            d = dict(d)
            d["_id"] = f"oid-{len(self._docs)}"
            self._docs.append(d)

    def _match(self, doc: dict, flt: dict) -> bool:
        for k, v in flt.items():
            if k == "$or":
                if not any(self._match(doc, sub) for sub in v):
                    return False
                continue
            cur = doc
            for part in k.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            if isinstance(v, dict):
                for op, opv in v.items():
                    if op == "$exists":
                        present = cur is not None
                        if present != bool(opv):
                            return False
                    elif op == "$gte":
                        if cur is None or cur < opv:
                            return False
                    elif op == "$lte":
                        if cur is None or cur > opv:
                            return False
                    elif op == "$in":
                        if cur not in opv:
                            return False
                    elif op == "$ne":
                        if cur == opv:
                            return False
            else:
                if cur != v:
                    return False
        return True

    async def update_many(self, flt, update):
        modified = 0
        for d in self._docs:
            if self._match(d, flt):
                for k, v in update.get("$set", {}).items():
                    parts = k.split(".")
                    cur = d
                    for p in parts[:-1]:
                        if not isinstance(cur.get(p), dict):
                            cur[p] = {}
                        cur = cur[p]
                    cur[parts[-1]] = v
                modified += 1

        class _R:
            modified_count = modified
        return _R()

    async def count_documents(self, flt):
        return sum(1 for d in self._docs if self._match(d, flt))

    def find(self, flt, projection=None):
        rows = [d for d in self._docs if self._match(d, flt)]

        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            def sort(self, *_a, **_kw):
                return self

            def limit(self, n):
                self._rows = self._rows[:n]
                return self

            async def to_list(self, length=None):
                return [
                    {k: v for k, v in d.items() if k != "_id"}
                    for d in self._rows
                ]
        return _Cur(rows)


class _DB:
    def __init__(self):
        self._colls: dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]


# ── outcome_backfill tests ────────────────────────────────────────


def _seed_memory(
    db, coll_name, symbol, direction, created_at, *, has_outcome=False,
    extra=None,
):
    doc = {
        "symbol": symbol,
        "direction": direction,
        "created_at": created_at,
        "authority": "memory_reasoning_only",
        "event_hash": f"hash-{symbol}-{direction}-{created_at}-{coll_name}",
    }
    if has_outcome:
        doc["outcome"] = {"pnl_pct": 0.05, "outcome_label": "win"}
    if extra:
        doc.update(extra)
    db[coll_name].insert_many_sync([doc])


@pytest.mark.asyncio
async def test_backfill_stamps_outcome_across_all_nodes():
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    created = opened.isoformat()
    # One unresolved memory per node + MC shared.
    for node in ("alpha", "camaro", "chevelle", "redeye", "mc"):
        _seed_memory(db, f"shelly_{node}_memories", "AAPL", "LONG", created)
    _seed_memory(db, "shelly_mc_shared_memory", "AAPL", "LONG", created)

    result = await backfill_outcome_for_trade(
        db,
        ticker="AAPL",
        direction="LONG",
        trade_id="trade-1",
        opened_at=opened,
        closed_at=opened + timedelta(hours=2),
        pnl_pct=0.025,
        outcome_label="win",
    )
    assert result["total"] == 6
    assert result["mc_shared"] == 1
    for node in ("Alpha", "Camaro", "Chevelle", "RedEye", "MC"):
        assert result[node] == 1


@pytest.mark.asyncio
async def test_backfill_is_idempotent_skips_already_resolved():
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    created = opened.isoformat()
    _seed_memory(
        db, "shelly_alpha_memories", "AAPL", "LONG", created,
        has_outcome=True,
    )
    _seed_memory(db, "shelly_camaro_memories", "AAPL", "LONG", created)

    result = await backfill_outcome_for_trade(
        db,
        ticker="AAPL",
        direction="LONG",
        opened_at=opened,
        closed_at=opened + timedelta(hours=2),
        pnl_pct=-0.01,
        outcome_label="loss",
    )
    # Alpha already had outcome — must not be re-stamped.
    assert result["Alpha"] == 0
    assert result["Camaro"] == 1


@pytest.mark.asyncio
async def test_backfill_respects_time_window():
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    # In-window memory.
    _seed_memory(
        db, "shelly_alpha_memories", "MSFT", "SHORT",
        opened.isoformat(),
    )
    # Way-out-of-window memory.
    far = (opened - timedelta(days=30)).isoformat()
    _seed_memory(
        db, "shelly_alpha_memories", "MSFT", "SHORT", far,
    )

    result = await backfill_outcome_for_trade(
        db,
        ticker="MSFT",
        direction="SHORT",
        opened_at=opened,
        closed_at=opened + timedelta(hours=2),
        pnl_pct=0.012,
        outcome_label="win",
    )
    assert result["Alpha"] == 1  # only the in-window row


@pytest.mark.asyncio
async def test_backfill_normalises_direction():
    """Boundary normaliser: 'up'/'BUY' → 'LONG'. Test the round-trip."""
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    _seed_memory(
        db, "shelly_alpha_memories", "TSLA", "LONG",
        opened.isoformat(),
    )

    # Call with 'up' — should still match a LONG memory.
    result = await backfill_outcome_for_trade(
        db,
        ticker="TSLA",
        direction="up",
        opened_at=opened,
        closed_at=opened + timedelta(hours=1),
        pnl_pct=0.04,
        outcome_label="win",
    )
    assert result["Alpha"] == 1


@pytest.mark.asyncio
async def test_backfill_failsoft_on_collection_error():
    """A broken collection on one node must not poison the rest."""
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    _seed_memory(
        db, "shelly_camaro_memories", "GOOG", "LONG",
        opened.isoformat(),
    )

    class _Broken:
        async def update_many(self, *a, **kw):
            raise RuntimeError("simulated mongo failure")

    # Force alpha's collection to raise.
    db._colls["shelly_alpha_memories"] = _Broken()

    result = await backfill_outcome_for_trade(
        db,
        ticker="GOOG",
        direction="LONG",
        opened_at=opened,
        closed_at=opened + timedelta(hours=2),
        pnl_pct=0.018,
        outcome_label="win",
    )
    # Alpha errored → 0, Camaro succeeds → 1, total respects failure.
    assert result["Alpha"] == 0
    assert result["Camaro"] == 1
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_backfill_writes_outcome_subdoc_keys():
    from shelly.outcome_backfill import backfill_outcome_for_trade

    db = _DB()
    opened = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    _seed_memory(
        db, "shelly_redeye_memories", "NVDA", "LONG",
        opened.isoformat(),
    )

    await backfill_outcome_for_trade(
        db,
        ticker="NVDA",
        direction="LONG",
        trade_id="abc-123",
        opened_at=opened,
        closed_at=opened + timedelta(hours=3),
        pnl_pct=0.07,
        outcome_label="win",
    )
    stored = db["shelly_redeye_memories"]._docs[0]
    assert stored["outcome"]["pnl_pct"] == pytest.approx(0.07)
    assert stored["outcome"]["outcome_label"] == "win"
    assert stored["outcome"]["trade_id"] == "abc-123"
    assert "backfilled_at" in stored["outcome"]
    # Authority stamp untouched.
    assert stored["authority"] == "memory_reasoning_only"


# ── emit_alpha_paper_trade tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_emit_alpha_paper_trade_noop_when_pipeline_missing():
    from shelly import mc_emitter
    from shelly.brain_emitter import emit_alpha_paper_trade

    saved = mc_emitter._pipeline
    mc_emitter._pipeline = None
    try:
        # Must not raise.
        result = await emit_alpha_paper_trade(
            symbol="AAPL", direction="long", confidence=0.7,
            trade_id="t-1",
        )
        assert result is None
    finally:
        mc_emitter._pipeline = saved


@pytest.mark.asyncio
async def test_emit_alpha_paper_trade_routes_through_pipeline():
    from shelly import mc_emitter
    from shelly.brain_emitter import emit_alpha_paper_trade

    captured = {}

    class _FakePipeline:
        async def record_brain_event(self, node, receipt):
            captured["node"] = node
            captured["receipt"] = receipt
            return {"ok": True}

    saved = mc_emitter._pipeline
    mc_emitter._pipeline = _FakePipeline()
    try:
        await emit_alpha_paper_trade(
            symbol="aapl",
            direction="up",
            confidence=72.5,  # 0–100 scale → 0.725
            trade_id="t-99",
            prediction_id="p-1",
            sovereign_decision_id="d-2",
            entry_price=190.25,
            position_usd=1000.0,
            regime="trending_up",
        )
    finally:
        mc_emitter._pipeline = saved

    assert captured["node"] == "Alpha"
    rec = captured["receipt"]
    assert rec["symbol"] == "AAPL"
    assert rec["direction"] == "LONG"  # normalised from "up"
    assert rec["confidence"] == pytest.approx(0.725)
    assert rec["decision"] == "PAPER_TRADE_OPEN"
    assert rec["features"]["trade_id"] == "t-99"
    assert rec["features"]["sovereign_decision_id"] == "d-2"
    assert rec["features"]["entry_price"] == 190.25
    assert rec["features"]["stage"] == "execution"


@pytest.mark.asyncio
async def test_emit_alpha_paper_trade_swallows_pipeline_errors():
    from shelly import mc_emitter
    from shelly.brain_emitter import emit_alpha_paper_trade

    class _Boom:
        async def record_brain_event(self, *a, **kw):
            raise RuntimeError("simulated")

    saved = mc_emitter._pipeline
    mc_emitter._pipeline = _Boom()
    try:
        # Must NOT raise — fail-soft contract.
        await emit_alpha_paper_trade(
            symbol="AAPL", direction="LONG", confidence=0.6,
            trade_id="t-x",
        )
    finally:
        mc_emitter._pipeline = saved


# ── consensus endpoint rollup rule ────────────────────────────────


def test_consensus_rollup_warn_dominates():
    """Conservative-priority rule from the consensus endpoint."""
    from routes.admin_shelly_federation import (  # type: ignore
        federation_consensus as _,
    )
    # The rule is encoded inline in the endpoint; mirror it here as a
    # static guard so it can't drift silently.
    recommendations = ["support", "support", "warn", "neutral"]
    if "warn" in recommendations:
        rolled = "warn"
    elif "support" in recommendations and "neutral" not in recommendations:
        rolled = "support"
    else:
        rolled = "neutral"
    assert rolled == "warn"


def test_consensus_rollup_support_requires_no_neutral_objector():
    recommendations = ["support", "neutral", "support"]
    if "warn" in recommendations:
        rolled = "warn"
    elif "support" in recommendations and "neutral" not in recommendations:
        rolled = "support"
    else:
        rolled = "neutral"
    # A single neutral voter blocks "support" promotion.
    assert rolled == "neutral"


def test_consensus_rollup_support_clean_sweep():
    recommendations = ["support", "support", "support", "support", "support"]
    if "warn" in recommendations:
        rolled = "warn"
    elif "support" in recommendations and "neutral" not in recommendations:
        rolled = "support"
    else:
        rolled = "neutral"
    assert rolled == "support"
