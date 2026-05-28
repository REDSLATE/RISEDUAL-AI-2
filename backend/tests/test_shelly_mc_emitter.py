"""Tests — Phase 1 Shelly-MC wiring (2026-02-26).

Covers:
  * mc_emitter helper — singleton, fail-soft, doctrine stamp.
  * Integration: sovereign promotion gate emits a Shelly-MC event
    on every verdict.
"""
from __future__ import annotations

import pytest

from shelly.mc_emitter import emit_mc_event, get_pipeline, set_pipeline


@pytest.fixture(autouse=True)
def reset_singleton():
    """Don't leak the pipeline between tests."""
    set_pipeline(None)
    yield
    set_pipeline(None)


# ── Singleton + fail-soft ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_returns_skipped_when_no_pipeline():
    r = await emit_mc_event(
        verdict_type="x", symbol="EQUITY", direction="HOLD_SHADOW",
    )
    assert r["ok"] is False
    assert r["skipped"] == "pipeline_uninitialised"


@pytest.mark.asyncio
async def test_emit_routes_through_pipeline_with_mc_brain_tag():
    captured = {}

    class _FakePipeline:
        async def record_brain_event(self, brain, receipt):
            captured["brain"] = brain
            captured["receipt"] = receipt
            return {"ok": True, "node": brain, "is_mc_node": brain == "MC"}

    set_pipeline(_FakePipeline())

    r = await emit_mc_event(
        verdict_type="sovereign_promotion_gate",
        symbol="EQUITY",
        direction="HOLD_SHADOW",
        decision="PROMOTE_GATE_BLOCK",
        features={"rows_resolved": 0, "rows_to_go": 500},
    )
    assert r["ok"] is True
    assert captured["brain"] == "MC"
    # verdict_type stamped onto features
    assert captured["receipt"]["features"]["verdict_type"] == "sovereign_promotion_gate"
    assert captured["receipt"]["features"]["rows_resolved"] == 0
    assert r["result"]["is_mc_node"] is True


@pytest.mark.asyncio
async def test_emit_swallows_pipeline_exceptions():
    class _BadPipeline:
        async def record_brain_event(self, brain, receipt):
            raise RuntimeError("mongo down")

    set_pipeline(_BadPipeline())
    r = await emit_mc_event(
        verdict_type="x", symbol="EQUITY", direction="HOLD_SHADOW",
    )
    # Caller must never have to wrap this in their own try/except.
    assert r["ok"] is False
    assert r["skipped"] == "emit_exception"
    assert "mongo down" in r["error"]


# ── Integration: promotion gate → Shelly-MC ──────────────────────


@pytest.mark.asyncio
async def test_promotion_gate_emits_mc_shelly_event(monkeypatch):
    """The full Phase 1 wiring: computing the gate emits a verdict
    receipt through the MC emitter."""
    from shelly import ShellyPipeline

    captured = []

    class _Coll:
        async def count_documents(self, flt):
            return 0  # No data → blocked gate

        def aggregate(self, pipe):
            class _Cur:
                async def to_list(self, length=None):
                    return []
            return _Cur()

    class _FakeDB:
        def __init__(self):
            self._coll = _Coll()

        def __getitem__(self, name):
            return self._coll

    pipeline = ShellyPipeline(_FakeDB())

    # Wrap the pipeline's record_brain_event so we can observe the
    # MC verdict without actually writing to mongo.
    original = pipeline.record_brain_event

    async def _spy(brain, receipt):
        captured.append({"brain": brain, "receipt": receipt})
        return await original(brain, receipt)

    pipeline.record_brain_event = _spy
    set_pipeline(pipeline)

    from services.sovereign_promotion_gate import (
        compute_sovereign_promotion_status,
    )
    fake_db = _FakeDB()
    status = await compute_sovereign_promotion_status(fake_db, "equity")

    # The gate completed and its verdict has been pushed through MC
    # Shelly. Even on a totally empty DB we expect the BLOCK path.
    assert status["promoted"] is False
    assert len(captured) == 1
    emit = captured[0]
    assert emit["brain"] == "MC"
    feats = emit["receipt"]["features"]
    assert feats["verdict_type"] == "sovereign_promotion_gate"
    assert emit["receipt"]["decision"] == "PROMOTE_GATE_BLOCK"
    assert emit["receipt"]["direction"] == "HOLD_SHADOW"
    assert emit["receipt"]["symbol"] == "EQUITY"
