"""Alpha Core v2 — Milestone 1 failure corpus + accounting invariant.

Every situation that hurt Legacy must resolve to exactly one terminal
outcome (TRADED | BLOCKED | FAILED), and candidates_in must always equal
traded + blocked + failed.
"""
from __future__ import annotations

import pytest

from services.alpha_core_v2.config import Config
from services.alpha_core_v2.contracts import (
    AccountState, Candidate, OrderResult, Outcome, PositionState, Snapshot,
)
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.receipts import ReceiptStore


# ── fakes ───────────────────────────────────────────────────────────
class FakeBroker:
    def __init__(self, *, equity=1000.0, buying_power=1000.0, account_ok=True,
                 positions=None, positions_raise=False, submit_result=None,
                 order_result=None):
        self._equity = equity
        self._bp = buying_power
        self._account_ok = account_ok
        self._positions = positions or []
        self._positions_raise = positions_raise
        self._submit_result = submit_result
        self._order_result = order_result
        self.submitted = []

    def get_account(self):
        if not self._account_ok:
            return AccountState(0, 0, 0, ok=False, error="broker down")
        return AccountState(self._equity, self._bp, self._bp, ok=True)

    def get_positions(self):
        if self._positions_raise:
            raise RuntimeError("positions endpoint 503")
        return list(self._positions)

    def submit(self, symbol, qty, side="buy"):
        self.submitted.append((symbol, qty, side))
        if callable(self._submit_result):
            return self._submit_result(symbol, qty)
        return self._submit_result

    def get_order(self, order_id):
        return self._order_result


class FakeSource:
    def __init__(self, snaps):
        self._snaps = {s.symbol: s for s in snaps}

    async def snapshot(self, symbol):
        return self._snaps.get(symbol)


def _cfg(**over):
    base = dict(
        enabled=True, universe=["AAA"], desired_notional=350.0, alloc_pct=0.20,
        cash_reserve=5.0, min_trade=1.0, confidence_floor=0.55, max_positions=5,
        db_path=":memory:",
    )
    base.update(over)
    return Config(**base)


def _engine(broker, cfg=None, snaps=None):
    cfg = cfg or _cfg()
    src = FakeSource(snaps or [Snapshot("AAA", mark=250.0)])
    return CoreV2Engine(broker, ReceiptStore(":memory:"), cfg, source=src)


def _cand(symbol="AAA", mark=250.0, conf=0.90):
    return Candidate(symbol=symbol, mark=mark, score=0.9, pattern="p",
                     confidence=conf, reason="test")


def _filled(symbol, qty):
    return OrderResult(ok=True, order_id="OID-1", status="filled",
                       filled_qty=qty, fill_price=250.0, requested_qty=qty)


@pytest.mark.asyncio
async def test_phantom_local_open_broker_flat_proceeds_and_trades():
    b = FakeBroker(positions=[], submit_result=_filled)  # broker flat
    eng = _engine(b)
    # Seed a phantom "open" receipt so the store believes AAA is open.
    from services.alpha_core_v2.contracts import Receipt, Stage
    import time
    eng.store.save(Receipt("r0", "c0", "AAA", time.time_ns(), Outcome.TRADED,
                            Stage.RECONCILE, position_status="open", order_id="old"))
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.reconciled_phantom is True          # store phantom cleared
    assert "AAA" not in eng.store.open_position_symbols() or r.position_status == "open"
    assert b.submitted, "should have submitted after clearing phantom"


@pytest.mark.asyncio
async def test_genuine_broker_position_blocks_duplicate():
    b = FakeBroker(positions=[PositionState("AAA", 0.5, "long")])
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "duplicate_position"
    assert r.broker_held is True
    assert not b.submitted


@pytest.mark.asyncio
async def test_broker_position_lookup_unavailable_fails_closed():
    b = FakeBroker(positions_raise=True, submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.FAILED
    assert r.reason.startswith("broker_position_unknown")
    assert not b.submitted, "must NOT open blind when holdings unknown"


@pytest.mark.asyncio
async def test_bp_below_desired_resizes_then_trades():
    # equity 1000 -> 20% target = 200; bp only 173 -> spendable 168 binds.
    b = FakeBroker(equity=1000.0, buying_power=173.0, positions=[],
                   submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    s = r.sizing
    assert s["desired_notional"] == 350.0
    assert s["risk_capped_notional"] == 200.0
    assert s["affordable_notional"] == 168.0
    assert s["resized"] is True
    assert s["resize_reason"] == "buying_power"
    assert 0 < s["final_notional"] <= 168.0 + 250.0 / 10000.0


@pytest.mark.asyncio
async def test_bp_below_one_dollar_blocks_below_minimum():
    b = FakeBroker(equity=10.0, buying_power=5.50, positions=[], submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "below_minimum_trade_size"
    assert not b.submitted


@pytest.mark.asyncio
async def test_fractional_rounding_floors_not_over_affordable():
    b = FakeBroker(equity=1000.0, buying_power=1000.0, positions=[],
                   submit_result=_filled)
    # desired 350 -> risk cap 200 binds; mark 333 -> 200/333 = 0.6006 -> 0.6006
    eng = _engine(b, cfg=_cfg(desired_notional=350.0), snaps=[Snapshot("AAA", 333.0)])
    r = await eng._process("c1", _cand(mark=333.0), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.sizing["quantity"] * 333.0 <= 200.0 + 1e-6  # never exceeds affordable


@pytest.mark.asyncio
async def test_unusable_quote_blocks():
    b = FakeBroker(positions=[], submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(mark=0.0), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "below_minimum_trade_size"  # mark<=0 -> qty 0 -> blocked
    assert not b.submitted


@pytest.mark.asyncio
async def test_rejected_broker_order_fails():
    rej = OrderResult(ok=False, order_id="OID", status="rejected",
                      error="broker_rejected", requested_qty=0.1)
    b = FakeBroker(positions=[], submit_result=lambda s, q: rej)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.FAILED
    assert "rejected" in r.reason


@pytest.mark.asyncio
async def test_accepted_order_delayed_fill_then_reconciles():
    accepted = OrderResult(ok=True, order_id="OID-9", status="accepted",
                           filled_qty=0.0, fill_price=0.0, requested_qty=0.1)
    b = FakeBroker(positions=[], submit_result=lambda s, q: accepted)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED        # accepted = traded (pending fill)
    assert r.broker_confirmed is False
    assert r.position_status == "pending"
    # Restart-safe reconcile: broker later reports the fill.
    b._order_result = OrderResult(ok=True, order_id="OID-9", status="filled",
                                  filled_qty=0.1, fill_price=250.0)
    b._positions = [PositionState("AAA", 0.1, "long")]
    out = await eng.reconcile_outstanding()
    assert out["ok"] and out["finalized"] == 1


@pytest.mark.asyncio
async def test_partial_fill_is_traded_with_remaining_visible():
    partial = OrderResult(ok=True, order_id="OID-2", status="partially_filled",
                          filled_qty=0.05, fill_price=250.0, requested_qty=0.1)
    b = FakeBroker(positions=[], submit_result=lambda s, q: partial)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.filled_qty == 0.05
    assert r.requested_qty > r.filled_qty      # remaining is explicit


@pytest.mark.asyncio
async def test_flag_off_runs_full_pipeline_but_never_submits():
    b = FakeBroker(positions=[], submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=False)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "core_v2_disabled"
    assert r.sizing["quantity"] > 0           # sizing still computed + recorded
    assert not b.submitted


@pytest.mark.asyncio
async def test_accounting_invariant_mixed_cycle():
    # 3 candidates: one trades, one duplicate-blocks, one below-confidence.
    snaps = [
        Snapshot("AAA", 250.0, prev_close=245.0, pct_change=2.0, rvol=2.0),
        Snapshot("BBB", 100.0, prev_close=98.0, pct_change=2.0, rvol=2.0),
        Snapshot("CCC", 50.0, prev_close=49.9, pct_change=0.2, rvol=1.0),  # weak
    ]
    b = FakeBroker(equity=1000.0, buying_power=1000.0,
                   positions=[PositionState("BBB", 0.3, "long")],  # BBB dup
                   submit_result=_filled)
    cfg = _cfg(universe=["AAA", "BBB", "CCC"])
    eng = CoreV2Engine(b, ReceiptStore(":memory:"), cfg, source=FakeSource(snaps))
    res = await eng.run_cycle(live=True)
    assert res.candidates_in >= 2
    assert res.balanced, (res.candidates_in, res.traded, res.blocked, res.failed)
    assert res.candidates_in == res.traded + res.blocked + res.failed


@pytest.mark.asyncio
async def test_engine_exception_still_terminal_no_vanish():
    class Boom(FakeBroker):
        def get_positions(self):
            raise KeyboardInterrupt  # not caught by _process's except
    # Use a normal exception path instead: force account.ok True, positions ok,
    # but submit raises an unexpected error type handled by run_cycle wrapper.
    class WeirdBroker(FakeBroker):
        def submit(self, symbol, qty, side="buy"):
            raise ValueError("kaboom")
    b = WeirdBroker(positions=[], equity=1000.0, buying_power=1000.0)
    snaps = [Snapshot("AAA", 250.0, prev_close=245.0, pct_change=2.0, rvol=2.0)]
    eng = CoreV2Engine(b, ReceiptStore(":memory:"), _cfg(), source=FakeSource(snaps))
    res = await eng.run_cycle(live=True)
    assert res.balanced
    assert res.failed >= 1
