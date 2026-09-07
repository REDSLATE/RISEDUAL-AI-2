"""Tests for the Compact Authority Receipt assembler.

The receipt distills a setup's SQLite lifecycle stream into ONE
compact document — the chain of authority gates plus the resolved
outcome. These tests lock down:

* Every stage in the ordered pipeline is reported (passed /
  blocked / not_recorded — never fabricated).
* The FIRST blocking event correctly sets ``failure_stage`` and
  short-circuits ``authority_verified`` to False.
* A fully-clean trade (setup → trigger → intent → quote →
  submit) surfaces as ``authority_verified=True`` +
  ``final_status="filled"`` when the outcome doc says filled.
* Missing lifecycle events return ``None`` (invalid setup_id).
* Persistence is idempotent (upsert by setup_id).
* The outcome join gracefully handles missing MFE / MAE / R.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _isolated_hot_store(tmp_path, monkeypatch):
    db_path = tmp_path / "hot_store.db"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db_path))
    from services import alpha_hot_store
    alpha_hot_store._DB_PATH = None
    alpha_hot_store.init()
    yield


def _seed(setup_id: str, symbol: str, event: str, payload: dict, ts_ns: int) -> None:
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:
        con.execute(
            "INSERT INTO lifecycle_events(setup_id, symbol, event, stage, payload, ts_ns) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (setup_id, symbol, event, payload.get("stage") or event,
             json.dumps(payload), ts_ns),
        )


class _FakeCollection:
    def __init__(self, docs: list[dict]):
        self._docs = docs
        self._writes: list[tuple[dict, dict, bool]] = []

    async def find_one(self, query: dict):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    async def update_one(self, query, update, upsert=False):  # noqa: ARG002
        self._writes.append((query, update, upsert))


class _FakeDB:
    def __init__(self, outcomes: list[dict], receipts: list[dict] | None = None):
        self.alpha_outcomes = _FakeCollection(outcomes)
        self.alpha_authority_receipts = _FakeCollection(receipts or [])


# ─────────────────────────────────────────────
#  Build receipt paths
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_receipt_clean_trade_authority_verified():
    """A full pass — every gate fired a pass event, no rejections.
    ``authority_verified`` must be True and ``failure_stage`` None."""
    sid = "setup-clean"
    _seed(sid, "WMT", "setup_detected",
          {"setup_type": "breakout", "stage": "detected"}, 1_000_000_000)
    _seed(sid, "WMT", "triggered",
          {"stage": "triggered"}, 1_000_030_000_000)
    _seed(sid, "WMT", "intent_created",
          {"stage": "intent"}, 1_000_030_100_000)
    _seed(sid, "WMT", "execution_quote_confirmed",
          {"stage": "execution_quote"}, 1_000_030_200_000)
    _seed(sid, "WMT", "broker_submitted",
          {"stage": "broker", "broker_order_id": "abc"}, 1_000_030_500_000)

    db = _FakeDB(outcomes=[{
        "setup_id": sid, "order_submitted": True, "filled": True,
        "realized_r": 1.2, "mfe": 1.5, "mae": -0.2,
    }])
    from services.alpha_authority_receipt import build_receipt
    r = await build_receipt(db, setup_id=sid)

    assert r is not None
    assert r["authority_verified"] is True
    assert r["failure_stage"] is None
    assert r["final_status"] == "filled"
    stages = {s["stage"]: s for s in r["chain"]}
    assert stages["brain_vote"]["status"] == "passed"
    assert stages["trigger"]["status"] == "passed"
    assert stages["intent"]["status"] == "passed"
    assert stages["broker_capability"]["status"] == "passed"
    assert stages["broker_submit"]["status"] == "passed"
    # Un-fired stages should surface as not_recorded, NOT fabricated as pass
    assert stages["wave_intelligence"]["status"] == "not_recorded"
    assert stages["seat_authority"]["status"] == "not_recorded"
    assert stages["roadguard"]["status"] == "not_recorded"
    assert r["outcome"] == {
        "submitted": True, "filled": True, "reject_reason": None,
        "realized_r": 1.2, "mfe": 1.5, "mae": -0.2,
    }


@pytest.mark.asyncio
async def test_build_receipt_wave_blocked_sets_failure_stage():
    """Wave DANGER_PAUSE veto blocks upstream; failure_stage must
    surface it and every downstream stage stays not_recorded."""
    sid = "setup-wave"
    _seed(sid, "TSLA", "setup_detected",
          {"setup_type": "breakout", "stage": "detected"}, 1_000_000_000)
    _seed(sid, "TSLA", "wave_danger_pause",
          {"reason": "wave_danger_pause", "stage": "pre_pattern",
           "danger_score": 0.85}, 1_000_000_500_000)

    from services.alpha_authority_receipt import build_receipt
    r = await build_receipt(_FakeDB([]), setup_id=sid)

    assert r["failure_stage"] == "wave_intelligence"
    assert r["authority_verified"] is False
    assert r["final_status"] == "blocked_at_wave_intelligence"
    stages = {s["stage"]: s for s in r["chain"]}
    assert stages["wave_intelligence"]["status"] == "blocked"
    assert stages["wave_intelligence"]["reason"] == "wave_danger_pause"
    # Downstream stages must be not_recorded — NEVER fabricated as pass.
    for downstream in ("intent", "broker_capability", "broker_submit"):
        assert stages[downstream]["status"] == "not_recorded"


@pytest.mark.asyncio
async def test_build_receipt_chasing_filter_blocks_at_roadguard():
    sid = "setup-chase"
    _seed(sid, "ANET", "setup_detected",
          {"setup_type": "inverse_head_and_shoulders"}, 1_000_000_000)
    _seed(sid, "ANET", "triggered", {}, 1_000_030_000_000)
    _seed(sid, "ANET", "intent_created", {}, 1_000_030_100_000)
    _seed(sid, "ANET", "executor_rejected",
          {"reject_reason": "chasing_filter",
           "stage": "chasing_filter"}, 1_000_030_500_000)

    from services.alpha_authority_receipt import build_receipt
    r = await build_receipt(_FakeDB([]), setup_id=sid)

    assert r["failure_stage"] == "roadguard"
    assert r["authority_verified"] is False
    stages = {s["stage"]: s for s in r["chain"]}
    assert stages["roadguard"]["status"] == "blocked"
    assert stages["roadguard"]["reason"] == "chasing_filter"


@pytest.mark.asyncio
async def test_build_receipt_no_events_returns_none():
    from services.alpha_authority_receipt import build_receipt
    assert await build_receipt(_FakeDB([]), setup_id="setup-nonexistent") is None
    assert await build_receipt(_FakeDB([]), setup_id="") is None


@pytest.mark.asyncio
async def test_build_receipt_survives_missing_outcome():
    sid = "setup-noout"
    _seed(sid, "MSFT", "setup_detected", {"setup_type": "breakout"}, 1_000_000_000)
    _seed(sid, "MSFT", "triggered", {}, 1_000_030_000_000)
    from services.alpha_authority_receipt import build_receipt
    r = await build_receipt(_FakeDB([]), setup_id=sid)  # no outcome doc
    assert r is not None
    assert r["outcome"] == {}
    assert r["final_status"] == "unresolved"


# ─────────────────────────────────────────────
#  Persist + idempotency
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_receipt_upserts_by_setup_id():
    from services.alpha_authority_receipt import persist_receipt
    db = _FakeDB([])
    receipt = {"setup_id": "s-1", "symbol": "AAPL", "chain": []}
    await persist_receipt(db, receipt)
    await persist_receipt(db, receipt)  # second call = upsert, not duplicate
    writes = db.alpha_authority_receipts._writes
    assert len(writes) == 2
    for query, update, upsert in writes:
        assert query == {"setup_id": "s-1"}
        assert upsert is True
        assert update["$set"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_persist_receipt_swallows_mongo_errors():
    from services.alpha_authority_receipt import persist_receipt

    class _BoomCollection:
        async def update_one(self, *a, **kw):
            raise RuntimeError("mongo down")

    class _BoomDB:
        alpha_authority_receipts = _BoomCollection()

    # Must not raise — this runs on the trade path.
    await persist_receipt(_BoomDB(), {"setup_id": "s-boom"})


@pytest.mark.asyncio
async def test_build_and_persist_end_to_end():
    sid = "setup-e2e"
    _seed(sid, "NFLX", "setup_detected", {"setup_type": "breakout"}, 1_000_000_000)
    _seed(sid, "NFLX", "triggered", {}, 1_000_030_000_000)
    _seed(sid, "NFLX", "intent_created", {}, 1_000_030_100_000)
    _seed(sid, "NFLX", "execution_quote_confirmed", {}, 1_000_030_200_000)
    _seed(sid, "NFLX", "broker_submitted",
          {"broker_order_id": "ord-99"}, 1_000_030_500_000)

    db = _FakeDB(outcomes=[
        {"setup_id": sid, "order_submitted": True, "filled": False}
    ])
    from services.alpha_authority_receipt import build_and_persist
    r = await build_and_persist(db, setup_id=sid)
    assert r is not None
    assert r["final_status"] == "submitted"
    assert db.alpha_authority_receipts._writes
    query, update, upsert = db.alpha_authority_receipts._writes[0]
    assert query == {"setup_id": sid}
    assert upsert is True
    assert update["$set"]["symbol"] == "NFLX"


@pytest.mark.asyncio
async def test_first_blocking_event_wins_failure_stage():
    """When multiple gates block (rare but possible in retry loops),
    the FIRST-in-time block sets failure_stage."""
    sid = "setup-multi-block"
    _seed(sid, "AMD", "setup_detected", {"setup_type": "breakout"}, 1_000_000_000)
    _seed(sid, "AMD", "triggered", {}, 1_000_030_000_000)
    _seed(sid, "AMD", "intent_deduplicated",
          {"reason": "economic_fingerprint"}, 1_000_030_100_000)
    # A later exec_lock_conflict would ALSO block, but the receipt
    # must attribute the failure to the first stage.
    _seed(sid, "AMD", "exec_lock_conflict", {}, 1_000_030_500_000)

    from services.alpha_authority_receipt import build_receipt
    r = await build_receipt(_FakeDB([]), setup_id=sid)
    assert r["failure_stage"] == "intent"
