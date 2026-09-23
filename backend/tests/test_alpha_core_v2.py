"""Alpha Core v2 — Milestone 1 failure corpus + accounting invariant.

Every situation that hurt Legacy must resolve to exactly one terminal
outcome (TRADED | BLOCKED | FAILED), and candidates_in must always equal
traded + blocked + failed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.alpha_core_v2.config import Config
from services.alpha_core_v2.contracts import (
    AccountState, Candidate, ExecutionQuote, OrderResult, Outcome,
    PositionState, Snapshot,
)
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.receipts import ReceiptStore


# ── fakes ───────────────────────────────────────────────────────────
class FakeBroker:
    def __init__(self, *, equity=1000.0, buying_power=1000.0, account_ok=True,
                 positions=None, positions_raise=False, submit_result=None,
                 order_result=None, exec_price=250.0, quote_age_s=0.0,
                 quote_none=False):
        self._equity = equity
        self._bp = buying_power
        self._account_ok = account_ok
        self._positions = positions or []
        self._positions_raise = positions_raise
        self._submit_result = submit_result
        self._order_result = order_result
        self._exec_price = exec_price
        self._quote_age_s = quote_age_s
        self._quote_none = quote_none
        self.submitted = []

    def get_account(self):
        if not self._account_ok:
            return AccountState(0, 0, 0, ok=False, error="broker down")
        return AccountState(self._equity, self._bp, self._bp, ok=True)

    def get_positions(self):
        if self._positions_raise:
            raise RuntimeError("positions endpoint 503")
        return list(self._positions)

    def get_execution_quote(self, symbol):
        if self._quote_none:
            return None
        ts = datetime.now(timezone.utc) - timedelta(seconds=self._quote_age_s)
        return ExecutionQuote(symbol=symbol, price=Decimal(str(self._exec_price)),
                              timestamp=ts, source="fake")

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
        cash_reserve=5.0, min_trade=1.0, confidence_floor=0.55,
        quote_max_age_s=15.0, db_path=":memory:",
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
    from services.alpha_core_v2.contracts import Receipt, Stage
    import time
    eng.store.save(Receipt("r0", "c0", "AAA", time.time_ns(), Outcome.TRADED,
                            Stage.CONFIRM, position_status="open", order_id="old"))
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.reconciled_phantom is True
    assert b.submitted


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
    assert not b.submitted


@pytest.mark.asyncio
async def test_execution_quote_unavailable_fails():
    b = FakeBroker(positions=[], quote_none=True, submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.FAILED
    assert r.reason == "execution_quote_unavailable"
    assert not b.submitted


@pytest.mark.asyncio
async def test_stale_execution_quote_blocks():
    b = FakeBroker(positions=[], quote_age_s=3600.0, submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason.startswith("stale_execution_quote")
    assert not b.submitted


@pytest.mark.asyncio
async def test_allocation_is_pct_of_available_buying_power():
    # Account-percentage rule: allocate alloc_pct(20%) of AVAILABLE buying
    # power (173), NOT of equity(1000) and NOT a fixed target. 173*0.20=34.6.
    b = FakeBroker(equity=1000.0, buying_power=173.0, positions=[],
                   exec_price=250.0, submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    s = r.sizing
    assert s["spendable_balance"] == 173.0
    assert s["allocation_pct"] == 0.20
    assert s["allocation_notional"] == 34.6           # 3%-style base * buying power
    assert s["risk_cap_notional"] == 350.0            # existing per-trade cap preserved
    assert s["final_notional"] == 34.6                # allocation binds, cap does not
    assert s["quantity"] == 0.1384                    # 34.6 / 250, floored to 4dp
    assert s["remaining_buying_power"] == 138.4       # 173 - 34.6
    assert s["resized"] is False                      # nothing bound below the allocation
    assert r.order_acknowledged is True
    assert r.position_reconciled is False             # fact #2 deferred
    assert r.execution_price == 250.0
    assert r.execution_quote_source == "fake"


@pytest.mark.asyncio
async def test_owning_positions_does_not_block_new_entry():
    # 6 unrelated positions held, plenty of buying power → a fresh symbol must
    # still trade. Existing positions constrain ONLY via buying power now.
    held = [PositionState(f"H{i}", 0.1, "long") for i in range(6)]
    b = FakeBroker(equity=5000.0, buying_power=5000.0, positions=held,
                   exec_price=250.0, submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand("AAA"), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert b.submitted


@pytest.mark.asyncio
async def test_bp_below_one_dollar_blocks_below_minimum():
    b = FakeBroker(equity=10.0, buying_power=5.50, positions=[], submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "below_minimum_trade_size"
    assert not b.submitted


@pytest.mark.asyncio
async def test_size_recalculated_from_execution_mark_floors_affordable():
    # Discovery mark 250, but the FRESH execution quote is 333 → sizing must
    # use 333 (risk cap 200 binds) and floor so cost never exceeds affordable.
    b = FakeBroker(equity=1000.0, buying_power=1000.0, positions=[],
                   exec_price=333.0, submit_result=_filled)
    eng = _engine(b, snaps=[Snapshot("AAA", 250.0)])
    r = await eng._process("c1", _cand(mark=250.0), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.execution_price == 333.0            # execution mark, not discovery
    assert r.sizing["quantity"] * 333.0 <= 200.0 + 1e-6


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
    assert r.outcome is Outcome.TRADED
    assert r.order_acknowledged is True          # fact #1: order ACK'd
    assert r.position_reconciled is False        # fact #2: not yet verified
    assert r.position_status == "pending"
    # Restart-safe reconcile: broker later reports the fill AND the position.
    b._order_result = OrderResult(ok=True, order_id="OID-9", status="filled",
                                  filled_qty=0.1, fill_price=250.0)
    b._positions = [PositionState("AAA", 0.1, "long")]
    out = await eng.reconcile_outstanding()
    assert out["ok"] and out["finalized"] == 1
    saved = eng.store.recent(1)[0]
    assert saved["position_reconciled"] is True
    assert saved["reconciled_position_qty"] == 0.1


@pytest.mark.asyncio
async def test_partial_fill_is_traded_with_remaining_visible():
    partial = OrderResult(ok=True, order_id="OID-2", status="partially_filled",
                          filled_qty=0.05, fill_price=250.0, requested_qty=0.1)
    b = FakeBroker(positions=[], submit_result=lambda s, q: partial)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.TRADED
    assert r.broker_reported_fill_qty == 0.05
    assert r.requested_qty > r.broker_reported_fill_qty


@pytest.mark.asyncio
async def test_flag_off_runs_full_pipeline_but_never_submits():
    b = FakeBroker(positions=[], submit_result=_filled)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=False)
    assert r.outcome is Outcome.BLOCKED
    assert r.reason == "core_v2_disabled"
    assert r.sizing["quantity"] > 0
    assert r.execution_price == 250.0            # quote still fetched + recorded
    assert not b.submitted


@pytest.mark.asyncio
async def test_accounting_invariant_mixed_cycle():
    snaps = [
        Snapshot("AAA", 250.0, prev_close=245.0, pct_change=2.0, rvol=2.0),
        Snapshot("BBB", 100.0, prev_close=98.0, pct_change=2.0, rvol=2.0),
        Snapshot("CCC", 50.0, prev_close=49.9, pct_change=0.2, rvol=1.0),  # weak
    ]
    b = FakeBroker(equity=1000.0, buying_power=1000.0,
                   positions=[PositionState("BBB", 0.3, "long")],
                   submit_result=_filled)
    cfg = _cfg(universe=["AAA", "BBB", "CCC"])
    eng = CoreV2Engine(b, ReceiptStore(":memory:"), cfg, source=FakeSource(snaps))
    res = await eng.run_cycle(live=True)
    assert res.candidates_in == 3
    assert res.balanced
    assert res.traded == 1 and res.blocked == 2 and res.failed == 0


@pytest.mark.asyncio
async def test_cycle_running_buying_power_never_overcommits():
    # With no position-count cap, a cycle must not fire more orders than the
    # account can pay for. Each accepted order decrements the running buying
    # power, so successive allocations taper and the aggregate stays within
    # available capital (had we frozen the snapshot, 3 x 50% = 150 > 100).
    snaps = [
        Snapshot("AAA", 10.0, prev_close=9.8, pct_change=2.0, rvol=2.0),
        Snapshot("DDD", 10.0, prev_close=9.8, pct_change=2.0, rvol=2.0),
        Snapshot("EEE", 10.0, prev_close=9.8, pct_change=2.0, rvol=2.0),
    ]
    b = FakeBroker(equity=100.0, buying_power=100.0, positions=[],
                   exec_price=10.0, submit_result=_filled)
    cfg = _cfg(universe=["AAA", "DDD", "EEE"], alloc_pct=0.5, cash_reserve=0.0)
    eng = CoreV2Engine(b, ReceiptStore(":memory:"), cfg, source=FakeSource(snaps))
    res = await eng.run_cycle(live=True)
    assert res.balanced
    traded = [r for r in res.receipts if r.outcome is Outcome.TRADED]
    assert len(traded) >= 2
    notionals = [float(r.sizing["final_notional"]) for r in traded]
    # Core guarantee: aggregate committed capital never exceeds what was available.
    assert sum(notionals) <= 100.0 + 1e-6
    # Tapering proves the decrement (a frozen snapshot would size every trade equal).
    assert notionals[0] > notionals[1]


@pytest.mark.asyncio
async def test_engine_exception_still_terminal_no_vanish():
    class WeirdBroker(FakeBroker):
        def submit(self, symbol, qty, side="buy"):
            raise ValueError("kaboom")
    b = WeirdBroker(positions=[], equity=1000.0, buying_power=1000.0)
    snaps = [Snapshot("AAA", 250.0, prev_close=245.0, pct_change=2.0, rvol=2.0)]
    eng = CoreV2Engine(b, ReceiptStore(":memory:"), _cfg(), source=FakeSource(snaps))
    res = await eng.run_cycle(live=True)
    assert res.balanced
    assert res.failed >= 1


@pytest.mark.asyncio
async def test_two_simultaneous_same_symbol_single_submission():
    """RACE GATE: two identical candidates arriving nearly simultaneously must
    yield AT MOST ONE broker submission — even if the broker never reports the
    position (worst case: both read held=False)."""
    submissions = []

    def submit_fn(sym, qty):
        submissions.append((sym, qty))
        return OrderResult(ok=True, order_id=f"OID-{len(submissions)}",
                           status="accepted", filled_qty=0.0, requested_qty=qty)

    b = FakeBroker(equity=1000.0, buying_power=1000.0, positions=[],
                   submit_result=submit_fn)
    eng = _engine(b)
    acct = b.get_account()
    r1, r2 = await asyncio.gather(
        eng._process("cyc", _cand("AAA"), acct, live=True),
        eng._process("cyc", _cand("AAA"), acct, live=True),
    )
    assert len(submissions) == 1
    outcomes = {r1.outcome, r2.outcome}
    assert Outcome.TRADED in outcomes and Outcome.BLOCKED in outcomes
    blocked = r1 if r1.outcome is Outcome.BLOCKED else r2
    assert blocked.reason == "concurrent_duplicate"


@pytest.mark.asyncio
async def test_many_simultaneous_same_symbol_single_submission():
    submissions = []

    def submit_fn(sym, qty):
        submissions.append((sym, qty))
        return OrderResult(ok=True, order_id=f"OID-{len(submissions)}",
                           status="filled", filled_qty=qty, fill_price=250.0,
                           requested_qty=qty)

    b = FakeBroker(equity=1000.0, buying_power=1000.0, positions=[],
                   submit_result=submit_fn)
    eng = _engine(b)
    acct = b.get_account()
    results = await asyncio.gather(*[
        eng._process("cyc", _cand("AAA"), acct, live=True) for _ in range(8)
    ])
    assert len(submissions) == 1
    assert sum(1 for r in results if r.outcome is Outcome.TRADED) == 1
    assert sum(1 for r in results if r.outcome is Outcome.BLOCKED) == 7


def test_interlock_v2_armed_disables_legacy(monkeypatch):
    """Safety interlock: Legacy's live-exec gate must be OFF whenever v2 is armed."""
    from services.public_equity_live_executor import _live_exec_enabled
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.delenv("ALPHA_CORE_V2", raising=False)
    assert _live_exec_enabled() is True
    monkeypatch.setenv("ALPHA_CORE_V2", "1")
    assert _live_exec_enabled() is False   # v2 armed → Legacy forced off


@pytest.mark.asyncio
async def test_close_position_sells_broker_qty_then_reconciles():
    def sell(s, q):
        return OrderResult(ok=True, order_id="SELL-1", status="filled",
                           filled_qty=q, fill_price=250.0, requested_qty=q)
    b = FakeBroker(positions=[PositionState("AAA", 0.2, "long")], submit_result=sell)
    eng = _engine(b)
    r = await eng.close_position("AAA")
    assert r.outcome is Outcome.TRADED and r.action == "close"
    assert b.submitted[-1][0] == "AAA" and abs(b.submitted[-1][1] - 0.2) < 1e-9
    assert b.submitted[-1][2] == "sell"     # broker-reported qty, sell side
    assert "AAA" in eng._closing
    # reconcile: broker now flat → position reconciled, guard cleared
    b._order_result = OrderResult(ok=True, order_id="SELL-1", status="filled",
                                  filled_qty=0.2, fill_price=250.0)
    b._positions = []
    out = await eng.reconcile_outstanding()
    assert out["finalized"] == 1
    assert "AAA" not in eng._closing
    saved = eng.store.recent(1)[0]
    assert saved["position_reconciled"] is True
    assert saved["position_status"] == "reconciled_flat"


@pytest.mark.asyncio
async def test_close_when_flat_blocks_already_flat():
    b = FakeBroker(positions=[])
    eng = _engine(b)
    r = await eng.close_position("AAA")
    assert r.outcome is Outcome.BLOCKED and r.reason == "already_flat"
    assert not b.submitted


@pytest.mark.asyncio
async def test_close_broker_error_fails():
    b = FakeBroker(positions=[PositionState("AAA", 0.2, "long")],
                   submit_result=lambda s, q: OrderResult(
                       ok=False, status="rejected", error="broker_rejected",
                       requested_qty=q))
    eng = _engine(b)
    r = await eng.close_position("AAA")
    assert r.outcome is Outcome.FAILED and r.action == "close"


@pytest.mark.asyncio
async def test_double_close_blocked_in_flight():
    def accepted(s, q):
        return OrderResult(ok=True, order_id="SELL-9", status="accepted",
                           filled_qty=0.0, requested_qty=q)
    b = FakeBroker(positions=[PositionState("AAA", 0.2, "long")], submit_result=accepted)
    eng = _engine(b)
    r1, r2 = await asyncio.gather(eng.close_position("AAA"), eng.close_position("AAA"))
    assert len(b.submitted) == 1
    outs = {r1.outcome, r2.outcome}
    assert Outcome.TRADED in outs and Outcome.BLOCKED in outs
    blocked = r1 if r1.outcome is Outcome.BLOCKED else r2
    assert blocked.reason == "close_in_flight"
