"""Alpha Core v2 × existing-Sovereign-shadow gate.

Proves: the shadow gate is ADVISORY (a HOLD does NOT stop a V2 candidate); it
becomes blocking ONLY when sovereign_enforce AND the cycle is live; it reads the
existing shadow weights READ-ONLY (never writes/clobbers state.json); and the
verdict maps run_adaptive_core's action to a proposal correctly.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.alpha_core_v2.config import Config
from services.alpha_core_v2.contracts import (
    AccountState, Candidate, ExecutionQuote, OrderResult, Outcome, Stage,
)
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.receipts import ReceiptStore
from services.alpha_core_v2 import sovereign_shadow as ss


# ── fakes ───────────────────────────────────────────────────────────
class FakeBroker:
    def __init__(self):
        self.submitted = []

    def get_account(self):
        return AccountState(1000.0, 1000.0, 1000.0, ok=True)

    def get_positions(self):
        return []

    def get_execution_quote(self, symbol):
        return ExecutionQuote(symbol=symbol, price=Decimal("250"),
                              timestamp=datetime.now(timezone.utc), source="fake")

    def submit(self, symbol, qty, side="buy"):
        self.submitted.append((symbol, qty, side))
        return OrderResult(ok=True, order_id="OID", status="filled",
                           filled_qty=qty, fill_price=250.0, requested_qty=qty)

    def get_order(self, oid):
        return OrderResult(ok=True, order_id=oid, status="filled", filled_qty=1)


class HoldGate:
    async def __call__(self, cand):
        return ss.ShadowProposal(action="HOLD", vetoes=["shadow_hold"],
                                 support_score=0.1)


def _cfg(**over):
    base = dict(enabled=True, universe=["AAA"], desired_notional=350.0,
                alloc_pct=0.20, cash_reserve=5.0, min_trade=1.0,
                confidence_floor=0.55, quote_max_age_s=15.0, db_path=":memory:")
    base.update(over)
    return Config(**base)


def _cand():
    return Candidate(symbol="AAA", mark=250.0, score=0.9, pattern="p",
                     confidence=0.90, reason="test")


def _engine(gate=None, enforce=False):
    b = FakeBroker()
    return CoreV2Engine(b, ReceiptStore(":memory:"), _cfg(),
                        sovereign_gate=gate, sovereign_enforce=enforce), b


# ── advisory vs enforcement ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_advisory_hold_does_not_stop_candidate():
    eng, b = _engine(gate=HoldGate(), enforce=False)
    r = await eng._process("c1", _cand(), b.get_account(), live=False)
    # Advisory: sovereign did NOT terminate it — it flowed past DECIDE and only
    # stopped at ORDER because this is a dry cycle.
    assert not r.reason.startswith("sovereign_hold")
    assert r.stage_reached is Stage.ORDER
    assert r.reason == "core_v2_disabled"


@pytest.mark.asyncio
async def test_enforced_hold_blocks_only_when_live():
    eng, b = _engine(gate=HoldGate(), enforce=True)
    # enforce=True but dry (live=False) → still advisory, candidate flows.
    r_dry = await eng._process("c1", _cand(), b.get_account(), live=False)
    assert not r_dry.reason.startswith("sovereign_hold")
    # enforce=True AND live=True → blocked at DECIDE.
    r_live = await eng._process("c2", _cand(), b.get_account(), live=True)
    assert r_live.outcome is Outcome.BLOCKED
    assert r_live.stage_reached is Stage.DECIDE
    assert r_live.reason.startswith("sovereign_hold")
    assert not b.submitted


# ── gate verdict mapping ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_shadow_gate_maps_bullish_to_buy(monkeypatch):
    async def _top(cand):
        # strong uptrend: price well above sma20, positive macd, healthy rsi
        return {"symbol": cand.symbol, "price": 110.0,
                "technicals": {"sma20": 100.0, "macd": 1.0, "rsi14": 60.0}}
    monkeypatch.setattr(ss, "_build_topofbook", _top)
    gate = ss.ShadowGate(weights={"trend": 0.85, "macd": 0.65, "rsi": -0.25})
    p = await gate(_cand())
    assert p.action == "BUY" and not p.vetoes


@pytest.mark.asyncio
async def test_shadow_gate_maps_flat_to_hold(monkeypatch):
    async def _top(cand):
        return {"symbol": cand.symbol, "price": 100.0, "technicals": {}}
    monkeypatch.setattr(ss, "_build_topofbook", _top)
    gate = ss.ShadowGate(weights={"trend": 0.85, "macd": 0.65, "rsi": -0.25})
    p = await gate(_cand())
    assert p.action == "HOLD" and p.vetoes


# ── existing shadow is read-only ─────────────────────────────────────
def test_load_weights_reads_existing_shadow_readonly(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    payload = {"brain": "alpha", "mode": "DTD", "learning_rate": 0.06,
               "weights": {"trend": 1.5, "macd": 0.4, "rsi": -0.2},
               "decisions": [], "outcomes": [], "notes": "",
               "updated_at": "2026-06-01T00:00:00+00:00"}
    state.write_text(json.dumps(payload))
    before = state.read_text()
    before_mtime = state.stat().st_mtime
    monkeypatch.setenv("SOVEREIGN_STATE_PATH", str(state))
    w = ss._load_weights()
    assert w == {"trend": 1.5, "macd": 0.4, "rsi": -0.2}
    # File must be untouched — the gate never writes the shared shadow state.
    assert state.read_text() == before
    assert state.stat().st_mtime == before_mtime
