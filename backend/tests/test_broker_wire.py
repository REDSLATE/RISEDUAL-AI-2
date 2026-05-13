"""Phase 5b broker wire — 4-gate defense in depth tests.

These tests are SAFETY-CRITICAL. The wire MUST NOT fire when any one
of the 4 gates is closed. Defaults: ALL 4 closed -> never fires.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

import pytest

from services.ml.broker_wire import (
    BrokerWireResult,
    _broker_live_order_flag,
    _enforce_flag_for,
    _legacy_live_execution_flag,
    run_broker_wire,
)
from services.ml.contracts import MLVerdict, Verdict


# ── Helpers ──────────────────────────────────────────────────────


@dataclass
class _StubPipelineDecision:
    symbol: str = "AAPL"
    lane: str = "equity"
    blocked_at: Optional[str] = None
    final: MLVerdict = field(default_factory=lambda: MLVerdict(
        layer="executor", decision="BUY", confidence=0.7,
        reason="EXECUTOR_APPROVE", can_approve=True,
    ))
    trail: list = field(default_factory=list)


@dataclass
class _StubRGVerdict:
    decision: str = "PASS"
    gate: Optional[str] = None
    reason: Optional[str] = None
    lane: str = "equity"


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)


class _Coll:
    def __init__(self, db, name):
        self.db = db
        self.name = name
    def aggregate(self, _pipeline):
        return _Cursor([])
    def find(self, *_a, **_kw):
        return _Cursor([])
    async def find_one(self, *_a, **_kw):
        return None
    async def count_documents(self, *_a, **_kw):
        return 0
    async def insert_one(self, doc):
        self.db._intents.append(doc)


class _StubDB:
    def __init__(self):
        self._intents: list = []
    def __getitem__(self, name):
        return _Coll(self, name)


@contextmanager
def _env(**vars_):
    """Set env vars for the duration of the block; restore on exit."""
    saved = {}
    for k, v in vars_.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── Default state — all gates closed ─────────────────────────────


@pytest.mark.asyncio
async def test_defaults_never_fire():
    """All 4 gates default closed. The wire must NEVER fire."""
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    res = await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
    )
    assert res is not None
    assert res.fired is False
    assert res.order_id is None
    assert res.classification != "FIRED"
    # Persisted exactly one intent row
    assert len(db._intents) == 1


@pytest.mark.asyncio
async def test_pipeline_blocked_classified_shadow_only():
    """When the upstream pipeline already blocked, the wire records
    SHADOW_ONLY (no possible firing path)."""
    db = _StubDB()
    pipeline = _StubPipelineDecision(
        blocked_at="perception",
        final=MLVerdict(layer="perception", decision="NO_TRADE",
                        confidence=0.0, reason="LIQUIDITY_THIN",
                        can_approve=True),
    )
    rg = None  # RG not called when pipeline blocked
    res = await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
    )
    assert res.classification == "SHADOW_ONLY"
    assert res.fired is False


@pytest.mark.asyncio
async def test_rg_blocked_classified_shadow_only():
    """When RG blocked the trade, classification is SHADOW_ONLY."""
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict(decision="BLOCK", gate="G02", reason="DAILY_LOSS_CAP_HIT")
    res = await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
    )
    assert res.classification == "SHADOW_ONLY"
    assert res.fired is False


@pytest.mark.asyncio
async def test_upstream_clear_but_gate1_closed_classified_gate_block():
    """Pipeline + RG approve, but Gate 1 (per-lane enforce) is closed."""
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    # All flags default false → expect GATE_BLOCK (not SHADOW_ONLY since
    # upstream is clear).
    res = await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
    )
    assert res.classification == "GATE_BLOCK"
    assert res.fired is False
    assert res.gates["gate1_enforce_flag"] is False


# ── Each gate independently blocks firing ────────────────────────


@pytest.mark.asyncio
async def test_three_gates_open_but_kanban_blocks():
    """Gates 1+2+3 open but Gate 4 (Kanban) closed → no fire.

    The Kanban is in "Shadow / Blocked" by default (no receipts) so
    this is the realistic safety-net that catches operator
    misconfiguration."""
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    with _env(
        EQUITY_EXECUTOR_ENFORCE_ENABLED="true",
        BROKER_LIVE_ORDER_ENABLED="true",
        RISEDUAL_LIVE_EXECUTION="1",
    ):
        res = await run_broker_wire(
            db, lane="equity", symbol="AAPL", side="BUY",
            notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
        )
    # Real Kanban call against the stub returns Blocked → fail-safe.
    assert res.classification == "GATE_BLOCK"
    assert res.fired is False
    assert res.gates["gate1_enforce_flag"] is True
    assert res.gates["gate2_broker_live_order_enabled"] is True
    assert res.gates["gate3_legacy_live_execution"] is True
    assert res.gates["gate4_kanban_eligible"] is False


@pytest.mark.asyncio
async def test_only_gate1_open():
    """DOCTRINE V3: gate 2 (BROKER_LIVE_ORDER_ENABLED) is permanently open.

    Historically this test asserted gate 2 stays CLOSED when the env
    var is unset; after the V3 doctrine shift, gate 2 always reports
    open. The Kanban (gate 4) is now the meaningful brake.
    """
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    with _env(EQUITY_EXECUTOR_ENFORCE_ENABLED="true"):
        res = await run_broker_wire(
            db, lane="equity", symbol="AAPL", side="BUY",
            notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
        )
    assert res.fired is False  # Kanban still in Shadow/Blocked
    assert res.gates["gate1_enforce_flag"] is True
    assert res.gates["gate2_broker_live_order_enabled"] is True


@pytest.mark.asyncio
async def test_only_gate2_open():
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    with _env(BROKER_LIVE_ORDER_ENABLED="true"):
        res = await run_broker_wire(
            db, lane="equity", symbol="AAPL", side="BUY",
            notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
        )
    assert res.classification == "GATE_BLOCK"
    assert res.fired is False


@pytest.mark.asyncio
async def test_lane_gates_independent():
    """Equity enforce ON does NOT enable crypto firing path."""
    db = _StubDB()
    pipeline = _StubPipelineDecision(lane="crypto")
    rg = _StubRGVerdict(lane="crypto")
    with _env(
        EQUITY_EXECUTOR_ENFORCE_ENABLED="true",
        BROKER_LIVE_ORDER_ENABLED="true",
        RISEDUAL_LIVE_EXECUTION="1",
    ):
        res = await run_broker_wire(
            db, lane="crypto", symbol="BTC-USD", side="BUY",
            notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
        )
    # Crypto enforce flag is unset → gate1 closed for this call.
    assert res.gates["gate1_enforce_flag"] is False
    assert res.classification == "GATE_BLOCK"
    assert res.fired is False


# ── Gate-flag readers ────────────────────────────────────────────


def test_enforce_flag_for_equity():
    with _env(EQUITY_EXECUTOR_ENFORCE_ENABLED="true"):
        assert _enforce_flag_for("equity") is True
    assert _enforce_flag_for("equity") is False


def test_enforce_flag_for_crypto():
    with _env(CRYPTO_EXECUTOR_ENFORCE_ENABLED="true"):
        assert _enforce_flag_for("crypto") is True
    assert _enforce_flag_for("crypto") is False


def test_enforce_flag_for_unknown_lane():
    assert _enforce_flag_for("options") is False


def test_broker_live_order_flag():
    """DOCTRINE V3: ``_broker_live_order_flag`` is permanently True.

    Env var ``BROKER_LIVE_ORDER_ENABLED`` is no longer consulted —
    RISEDUAL is a headless brain. Mission Control's Executor seat
    owns broker authorization.
    """
    assert _broker_live_order_flag() is True
    with _env(BROKER_LIVE_ORDER_ENABLED="false"):
        assert _broker_live_order_flag() is True


def test_legacy_live_execution_flag():
    with _env(RISEDUAL_LIVE_EXECUTION="1"):
        assert _legacy_live_execution_flag() is True
    assert _legacy_live_execution_flag() is False


# ── Persistence shape ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intent_doc_shape():
    db = _StubDB()
    pipeline = _StubPipelineDecision()
    rg = _StubRGVerdict()
    await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=pipeline, rg_verdict=rg,
    )
    assert len(db._intents) == 1
    doc = db._intents[0]
    for key in (
        "lane", "symbol", "side", "requested_notional_usd",
        "classification", "fired", "order_id", "gates", "diagnostics",
        "created_at", "schema_version",
    ):
        assert key in doc, f"missing key: {key}"
    assert doc["fired"] is False
    assert isinstance(doc["gates"], dict)
    # All 4 gate booleans present
    for gk in (
        "gate1_enforce_flag", "gate2_broker_live_order_enabled",
        "gate3_legacy_live_execution", "gate4_kanban_eligible",
    ):
        assert gk in doc["gates"]


# ── NEVER raises ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_broker_wire_never_raises_on_bad_inputs():
    """A None pipeline_decision must degrade to None, not crash."""
    db = _StubDB()
    res = await run_broker_wire(
        db, lane="equity", symbol="AAPL", side="BUY",
        notional_usd=100.0, pipeline_decision=None, rg_verdict=None,
    )
    # Degraded -> returned None and the executor flow continues
    assert res is None
