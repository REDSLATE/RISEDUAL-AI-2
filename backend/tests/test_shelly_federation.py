"""Tests — 5-Shelly Federation (2026-02-26).

Covers contracts (hashing, stamps, doctrine string), LocalShelly,
MCShelly, ShellyPipeline, plus the route. Uses ``mongomock_motor``
where possible; falls back to a hand-rolled tiny fake when not.
"""
from __future__ import annotations

import pytest

from shelly.config import BRAIN_NAMES, MC_NODE_NAME, NODE_NAMES, MEMORY_REASONING_ONLY
from shelly.contracts import (
    ShellyMemoryEvent,
    ShellyReasoningReceipt,
    stable_hash,
    utc_now,
)


# ── Fake async mongo ──────────────────────────────────────────────


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def update_one(self, flt, update, upsert=False):
        for d in self._docs:
            if all(d.get(k) == v for k, v in flt.items()):
                if "$setOnInsert" not in update and "$set" in update:
                    d.update(update["$set"])

                class _R:
                    upserted_id = None
                    modified_count = 1
                    matched_count = 1
                return _R()
        if upsert:
            new = dict()
            if "$setOnInsert" in update:
                new.update(update["$setOnInsert"])
            if "$set" in update:
                new.update(update["$set"])
            new.update(flt)
            new["_id"] = f"oid-{len(self._docs)}"
            self._docs.append(new)

            class _R:
                upserted_id = new["_id"]
                modified_count = 0
                matched_count = 0
            return _R()

        class _R:
            upserted_id = None
            modified_count = 0
            matched_count = 0
        return _R()

    async def update_many(self, flt, update):
        modified = 0
        in_set = flt.get("event_hash", {}).get("$in", []) if isinstance(flt.get("event_hash"), dict) else None
        for d in self._docs:
            if in_set is not None and d.get("event_hash") not in in_set:
                continue
            d.update(update.get("$set", {}))
            modified += 1

        class _R:
            modified_count = modified
        return _R()

    async def insert_one(self, doc):
        d = dict(doc)
        d["_id"] = f"oid-{len(self._docs)}"
        self._docs.append(d)

        class _R:
            inserted_id = d["_id"]
        return _R()

    async def count_documents(self, flt):
        return sum(1 for d in self._docs if self._match(d, flt))

    def find(self, flt, projection=None):
        items = [d for d in self._docs if self._match(d, flt)]
        if projection and projection.get("_id") == 0:
            items = [{k: v for k, v in d.items() if k != "_id"} for d in items]
        return _FakeCursor(items)

    @staticmethod
    def _match(doc, flt):
        for k, v in flt.items():
            if isinstance(v, dict):
                if "$exists" in v:
                    parts = k.split(".")
                    cur = doc
                    exists = True
                    for p in parts:
                        if not isinstance(cur, dict) or p not in cur:
                            exists = False
                            break
                        cur = cur[p]
                    if exists != v["$exists"]:
                        return False
                if "$ne" in v and doc.get(k) == v["$ne"]:
                    return False
                if "$in" in v and doc.get(k) not in v["$in"]:
                    return False
            else:
                if "." in k:
                    parts = k.split(".")
                    cur = doc
                    for p in parts:
                        cur = cur.get(p) if isinstance(cur, dict) else None
                    if cur != v:
                        return False
                else:
                    if doc.get(k) != v:
                        return False
        return True


class _FakeCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, field, direction):
        reverse = direction == -1
        self._items = sorted(
            self._items, key=lambda d: d.get(field) or "", reverse=reverse,
        )
        return self

    def limit(self, n):
        self._items = self._items[:n]
        return self

    async def to_list(self, length=None):
        return list(self._items[: length or len(self._items)])


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeCollection()
        return self._colls[name]


# ── Contracts ─────────────────────────────────────────────────────


def test_stable_hash_is_order_insensitive():
    a = {"x": 1, "y": [1, 2], "z": "k"}
    b = {"z": "k", "y": [1, 2], "x": 1}
    assert stable_hash(a) == stable_hash(b)


def test_utc_now_isoformat_with_tz():
    s = utc_now()
    assert "+00:00" in s or "Z" in s


def test_memory_event_stamps_doctrine_authority():
    ev = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.7, decision="LONG", features={"rsi": 65},
        mc_status="verified", roadguard_status="green",
    )
    doc = ev.to_doc()
    assert doc["authority"] == MEMORY_REASONING_ONLY
    assert doc["authority"] == "memory_reasoning_only"
    assert isinstance(doc["event_hash"], str) and len(doc["event_hash"]) == 64
    assert doc["created_at"]


def test_reasoning_receipt_excludes_timestamp_from_hash():
    r = ShellyReasoningReceipt(
        brain="Alpha", symbol="AAPL", recommendation="warn",
        confidence_delta=-0.1, reasons=["foo"], evidence_hashes=["h1"],
    )
    d1 = r.to_doc()
    d2 = r.to_doc()
    # Hashes match even though created_at differs.
    assert d1["receipt_hash"] == d2["receipt_hash"]
    assert d1["authority"] == "memory_reasoning_only"


# ── LocalShelly ───────────────────────────────────────────────────


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.mark.asyncio
async def test_local_shelly_remember_is_idempotent(fake_db):
    from shelly.local_shelly import LocalShelly
    ls = LocalShelly("Alpha", fake_db)
    # Idempotency is per-(content+timestamp). Pin created_at so two
    # writes of the same logical receipt produce the same event_hash.
    pinned_ts = "2026-05-28T00:00:00+00:00"
    ev = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.6, decision="LONG", features={},
        mc_status="ok", roadguard_status="green",
        created_at=pinned_ts,
    )
    d1 = await ls.remember(ev)
    d2 = await ls.remember(ev)
    assert d1["event_hash"] == d2["event_hash"]
    coll = fake_db["shelly_alpha_memories"]
    matching = [d for d in coll._docs if d["event_hash"] == d1["event_hash"]]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_local_shelly_reason_warns_on_loss_streak(fake_db):
    from shelly.local_shelly import LocalShelly
    ls = LocalShelly("Alpha", fake_db)
    # Seed 6 losses + 1 win = 6/7 loss rate → warn
    for i in range(6):
        ev = ShellyMemoryEvent(
            brain="Alpha", symbol="AAPL", direction="LONG",
            confidence=0.7, decision="LONG", features={"i": i},
            mc_status="ok", roadguard_status="green",
            outcome={"pnl_pct": -0.02},
        )
        await ls.remember(ev)
    ev_win = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.7, decision="LONG", features={"i": 99},
        mc_status="ok", roadguard_status="green",
        outcome={"pnl_pct": 0.03},
    )
    await ls.remember(ev_win)

    receipt = await ls.reason({"symbol": "AAPL", "direction": "LONG"})
    assert receipt["recommendation"] == "warn"
    assert receipt["confidence_delta"] < 0
    assert receipt["authority"] == "memory_reasoning_only"
    assert receipt["reasons"]
    # _id must NEVER leak from a Shelly write.
    assert "_id" not in receipt


@pytest.mark.asyncio
async def test_local_shelly_reason_neutral_when_no_history(fake_db):
    from shelly.local_shelly import LocalShelly
    ls = LocalShelly("Camaro", fake_db)
    receipt = await ls.reason({"symbol": "NVDA", "direction": "SHORT"})
    assert receipt["recommendation"] == "neutral"
    assert receipt["confidence_delta"] == 0.0


@pytest.mark.asyncio
async def test_rollup_marks_memories_rolled(fake_db):
    from shelly.local_shelly import LocalShelly
    ls = LocalShelly("Alpha", fake_db)
    for i in range(3):
        await ls.remember(ShellyMemoryEvent(
            brain="Alpha", symbol=f"S{i}", direction="LONG",
            confidence=0.5, decision="LONG", features={},
            mc_status="ok", roadguard_status="green",
        ))
    pending = await ls.rollup_for_mc()
    assert len(pending) == 3
    hashes = [m["event_hash"] for m in pending]
    n = await ls.mark_rolled_to_mc(hashes)
    assert n == 3
    pending2 = await ls.rollup_for_mc()
    assert pending2 == []


# ── MCShelly ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mc_ingest_dedup(fake_db):
    from shelly.mc_shelly import MCShelly
    mc = MCShelly(fake_db)
    mem = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.5, decision="LONG", features={},
        mc_status="ok", roadguard_status="green",
    ).to_doc()
    r1 = await mc.ingest_rollup("Alpha", [mem])
    r2 = await mc.ingest_rollup("Alpha", [mem])
    assert r1["inserted"] == 1
    assert r2["duplicates"] == 1


@pytest.mark.asyncio
async def test_mc_reason_support_when_pattern_favourable(fake_db):
    from shelly.mc_shelly import MCShelly
    mc = MCShelly(fake_db)
    # 11 wins across two brains → support
    rollup = []
    for i in range(8):
        rollup.append(ShellyMemoryEvent(
            brain="Alpha", symbol="AAPL", direction="LONG",
            confidence=0.6, decision="LONG", features={"i": i},
            mc_status="ok", roadguard_status="green",
            outcome={"pnl_pct": 0.02},
        ).to_doc())
    for i in range(3):
        rollup.append(ShellyMemoryEvent(
            brain="Camaro", symbol="AAPL", direction="LONG",
            confidence=0.6, decision="LONG", features={"i": i},
            mc_status="ok", roadguard_status="green",
            outcome={"pnl_pct": 0.04},
        ).to_doc())
    await mc.ingest_rollup("Alpha", rollup[:8])
    await mc.ingest_rollup("Camaro", rollup[8:])

    r = await mc.reason_across_shellys({"symbol": "AAPL", "direction": "LONG"})
    assert r["recommendation"] == "support"
    assert r["confidence_delta"] > 0
    assert r["authority"] == "memory_reasoning_only"
    assert "Alpha" in r["by_brain"] and "Camaro" in r["by_brain"]


@pytest.mark.asyncio
async def test_mc_reason_warns_when_loss_pattern_dominant(fake_db):
    from shelly.mc_shelly import MCShelly
    mc = MCShelly(fake_db)
    rollup = []
    for i in range(12):
        rollup.append(ShellyMemoryEvent(
            brain="Alpha", symbol="TSLA", direction="LONG",
            confidence=0.6, decision="LONG", features={"i": i},
            mc_status="ok", roadguard_status="green",
            outcome={"pnl_pct": -0.05},
        ).to_doc())
    await mc.ingest_rollup("Alpha", rollup)
    r = await mc.reason_across_shellys({"symbol": "TSLA", "direction": "LONG"})
    assert r["recommendation"] == "warn"
    assert r["confidence_delta"] < 0


@pytest.mark.asyncio
async def test_mc_reason_neutral_below_sample_floor(fake_db):
    from shelly.mc_shelly import MCShelly
    mc = MCShelly(fake_db)
    rollup = []
    for i in range(3):  # < MC_MIN_SAMPLES
        rollup.append(ShellyMemoryEvent(
            brain="Alpha", symbol="META", direction="LONG",
            confidence=0.6, decision="LONG", features={"i": i},
            mc_status="ok", roadguard_status="green",
            outcome={"pnl_pct": -0.01},
        ).to_doc())
    await mc.ingest_rollup("Alpha", rollup)
    r = await mc.reason_across_shellys({"symbol": "META", "direction": "LONG"})
    assert r["recommendation"] == "neutral"


# ── ShellyPipeline (end-to-end smoke) ────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_instantiates_all_five_nodes(fake_db):
    """Federation has 5 LocalShellys — 4 brains plus MC.

    Doctrine: MC is NOT a brain (it's the verifier/notary) but it
    IS a federation node and owns its own Shelly so MC receipts
    get the same memory + reasoning treatment as brain receipts.
    """
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    assert set(p.locals.keys()) == set(NODE_NAMES)
    assert len(p.locals) == 5
    # MC is present alongside the 4 brains
    assert MC_NODE_NAME in p.locals
    assert MC_NODE_NAME not in BRAIN_NAMES  # MC is not a brain
    for brain in BRAIN_NAMES:
        assert brain in p.locals


@pytest.mark.asyncio
async def test_pipeline_records_mc_node_receipt(fake_db):
    """MC owns its own Shelly and produces receipts that flow through
    the same pipeline as brain receipts. Rejection of MC = doctrine bug."""
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    result = await p.record_brain_event("MC", {
        "symbol": "AAPL", "direction": "VERIFY", "confidence": 1.0,
        "decision": "VERIFY_OK", "features": {"verifier": True},
        "mc_status": "self", "roadguard_status": "green",
    })
    assert result["ok"] is True
    assert result["node"] == "MC"
    assert result["is_mc_node"] is True
    # MC's local memory was persisted into shelly_mc_memories
    assert await fake_db["shelly_mc_memories"].count_documents({}) == 1


@pytest.mark.asyncio
async def test_pipeline_record_brain_event_writes_to_all_three_layers(fake_db):
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    result = await p.record_brain_event("Alpha", {
        "symbol": "AAPL", "direction": "LONG", "confidence": 0.7,
        "decision": "LONG", "features": {"rsi": 55},
        "mc_status": "ok", "roadguard_status": "green",
    })
    assert result["ok"] is True
    assert result["authority"] == "memory_reasoning_only"
    assert result["local_memory"]["event_hash"]
    assert result["local_reasoning"]["authority"] == "memory_reasoning_only"
    assert result["mc_reasoning"]["authority"] == "memory_reasoning_only"
    # Memory persisted to LocalShelly
    assert await fake_db["shelly_alpha_memories"].count_documents({}) == 1


@pytest.mark.asyncio
async def test_pipeline_unknown_node_rejected_cleanly(fake_db):
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    result = await p.record_brain_event("GTO", {
        "symbol": "AAPL", "direction": "LONG",
    })
    assert result["ok"] is False
    assert result["reason"] == "UNKNOWN_NODE"
    assert "MC" in result["valid_nodes"]
    assert "Alpha" in result["valid_nodes"]


@pytest.mark.asyncio
async def test_pipeline_rollup_drains_all_five_nodes(fake_db):
    """All 5 federation nodes — 4 brains + MC — drain into shared."""
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    for node in NODE_NAMES:
        await p.record_brain_event(node, {
            "symbol": "BTC", "direction": "LONG", "confidence": 0.6,
            "decision": "LONG", "features": {"node": node},
            "mc_status": "ok", "roadguard_status": "green",
        })
    r = await p.rollup_all_to_mc()
    assert r["ok"] is True
    assert r["authority"] == "memory_reasoning_only"
    assert set(r["results"].keys()) == set(NODE_NAMES)
    assert sum(v["inserted"] for v in r["results"].values()) == 5
    # All five local-Shelly memory pools fully drained.
    for node in NODE_NAMES:
        pending = await p.locals[node].rollup_for_mc()
        assert pending == []


# ── Doctrine invariant ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_persisted_doc_carries_doctrine_authority(fake_db):
    """Doctrine check: NO Shelly-written doc may omit
    ``authority: memory_reasoning_only``. A grep-friendly contract
    that catches anyone who adds a new write path without the stamp."""
    from shelly.pipeline import ShellyPipeline
    p = ShellyPipeline(fake_db)
    await p.record_brain_event("Alpha", {
        "symbol": "AAPL", "direction": "LONG", "confidence": 0.6,
        "decision": "LONG", "features": {}, "mc_status": "ok",
        "roadguard_status": "green",
    })
    await p.rollup_all_to_mc()
    # Every doc in every shelly_* collection in the fake DB:
    for name, coll in fake_db._colls.items():
        if not name.startswith("shelly_"):
            continue
        for d in coll._docs:
            assert d.get("authority") == "memory_reasoning_only", (
                f"Doctrine violation in {name}: missing authority stamp"
            )
