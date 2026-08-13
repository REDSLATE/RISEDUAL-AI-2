"""Alpha Account-Aware Decision Overlay — maturation & gating tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fake Mongo replicating just what the overlay needs ─────────────
class _FakeColl:
    def __init__(self):
        self.state: dict = {}
        self.shadow: list = []

    async def find_one(self, filt, *_a, **_kw):
        if filt.get("_id") == "overlay":
            return dict(self.state) if self.state else None
        return None

    async def insert_one(self, doc):
        if doc.get("_id") == "overlay":
            self.state = {k: v for k, v in doc.items() if k != "_id"}
            return MagicMock(inserted_id="overlay")
        oid = f"shadow-{len(self.shadow)+1}"
        self.shadow.append({"_id": oid, **doc})
        return MagicMock(inserted_id=oid)

    async def update_one(self, filt, update, upsert=False):
        if filt.get("_id") == "overlay":
            self.state.update(update.get("$set", {}))
            return MagicMock(matched_count=1)
        for row in self.shadow:
            if row["_id"] == filt.get("_id"):
                row.update(update.get("$set", {}))
                return MagicMock(matched_count=1)
        return MagicMock(matched_count=0)

    def find(self, _query, _projection=None):
        rows = list(self.shadow)

        class _Cur:
            def sort(self, *_a, **_kw): return self

            def __aiter__(self_inner):
                self_inner._it = iter(rows)
                return self_inner

            async def __anext__(self_inner):
                try:
                    return next(self_inner._it)
                except StopIteration:
                    raise StopAsyncIteration
        return _Cur()

    async def create_index(self, *_a, **_kw):
        return None


class _FakeDB:
    def __init__(self):
        self._state = _FakeColl()
        self._shadow = _FakeColl()
        # Share so state and shadow can be inspected from the same coll?
        # Overlay uses two distinct collection names — we need separate.

    def __getitem__(self, name):
        return self._state if name == "alpha_overlay_state" else self._shadow


@pytest.fixture()
def db():
    return _FakeDB()


# ── Trading-day math ───────────────────────────────────────────────
def test_weekends_are_not_trading_days():
    from services.alpha_account_overlay import is_trading_day
    assert is_trading_day(date(2026, 2, 21)) is False  # Sat
    assert is_trading_day(date(2026, 2, 22)) is False  # Sun


def test_nyse_holidays_are_not_trading_days():
    from services.alpha_account_overlay import is_trading_day
    # Thanksgiving 2026, MLK day 2026
    assert is_trading_day(date(2026, 11, 26)) is False
    assert is_trading_day(date(2026, 1, 19)) is False


def test_weekdays_that_arent_holidays_are_trading_days():
    from services.alpha_account_overlay import is_trading_day
    assert is_trading_day(date(2026, 2, 24)) is True   # Tuesday
    assert is_trading_day(date(2026, 3, 3)) is True   # Tuesday


# ── State machine ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_first_status_creates_shadow_state(db):
    from services.alpha_account_overlay import status, MODE_SHADOW
    s = await status(db)
    assert s["mode"] == MODE_SHADOW
    assert s["trading_days_completed"] == 0
    assert s["days_remaining"] == 7
    assert s["faulted"] is False
    assert s["overlay_started_at"]


@pytest.mark.asyncio
async def test_seven_completed_trading_days_flip_hard_gate(db, monkeypatch):
    """Simulate 7 successive completed trading days; overlay must
    auto-transition to HARD_GATE without any operator intervention."""
    from services import alpha_account_overlay as ov
    # Seed initial state.
    await ov.status(db)
    # Fake a series of trading-day-finished dates.
    counted = [date(2026, 2, 24) + timedelta(days=i)
               for i in range(0, 14) if ov.is_trading_day(date(2026, 2, 24) + timedelta(days=i))][:7]
    from datetime import time as _t
    for d in counted:
        finished = datetime.combine(d, _t(16, 30), tzinfo=timezone.utc)
        monkeypatch.setattr(ov, "_trading_day_finished_ny", lambda finished=finished: finished.date())
        await ov.status(db)
    s = await ov.status(db)
    assert s["trading_days_completed"] == 7
    assert s["mode"] == "HARD_GATE"
    assert s["hard_gate_at"]


@pytest.mark.asyncio
async def test_faulted_overlay_never_auto_transitions(db, monkeypatch):
    from services import alpha_account_overlay as ov
    await ov.status(db)
    # Force a faulted state.
    await ov._save_state(db, {"faulted": True, "fault_reason": "broker_read_failed"})
    counted = [date(2026, 2, 24) + timedelta(days=i)
               for i in range(0, 14) if ov.is_trading_day(date(2026, 2, 24) + timedelta(days=i))][:8]
    from datetime import time as _t
    for d in counted:
        finished = datetime.combine(d, _t(16, 30), tzinfo=timezone.utc)
        monkeypatch.setattr(ov, "_trading_day_finished_ny", lambda finished=finished: finished.date())
        await ov.status(db)
    s = await ov.status(db)
    assert s["trading_days_completed"] >= 7
    assert s["faulted"] is True
    assert s["mode"] == "SHADOW", "faulted overlay must never transition"


@pytest.mark.asyncio
async def test_counter_does_not_double_count_same_day(db, monkeypatch):
    from services import alpha_account_overlay as ov
    await ov.status(db)
    monkeypatch.setattr(ov, "_trading_day_finished_ny", lambda: date(2026, 2, 24))
    await ov.status(db)
    await ov.status(db)  # second call same day
    await ov.status(db)  # third call same day
    s = await ov.status(db)
    assert s["trading_days_completed"] == 1


# ── evaluate_intent behaviour ──────────────────────────────────────
@pytest.mark.asyncio
async def test_evaluate_intent_faults_when_broker_unreachable(db, monkeypatch):
    from services import alpha_account_overlay as ov
    monkeypatch.setattr(ov, "get_account_context_for",
                         AsyncMock(return_value=None))
    dec = await ov.evaluate_intent(
        db, broker="public", symbol="AAPL", side="BUY", notional=500.0,
    )
    assert dec.verdict == "FAULTED_READ"
    assert dec.faulted is True
    # Shadow record was still journalled so the report is honest.
    assert dec.shadow_id
    s = await ov.status(db)
    assert s["faulted"] is True
    assert "broker_read_failed" in (s.get("fault_reason") or "")


@pytest.mark.asyncio
async def test_evaluate_intent_shadow_mode_never_blocks(db, monkeypatch):
    from services import alpha_account_overlay as ov
    from services.alpha_broker_account_context import AccountContext
    ctx = AccountContext(
        broker="public", captured_at_ms=0, equity=100.0, cash=0.0,
        buying_power=0.0, positions=(), open_orders=(),
    )
    monkeypatch.setattr(ov, "get_account_context_for",
                         AsyncMock(return_value=ctx))
    # Ensure we're in SHADOW.
    dec = await ov.evaluate_intent(
        db, broker="public", symbol="AAPL", side="BUY", notional=500.0,
    )
    assert dec.mode == "SHADOW"
    # BUY with zero buying_power should verdict BLOCK, but shadow
    # never enforces:
    assert dec.verdict == "BLOCK"
    assert dec.should_block is False
    # And the caller keeps the original size:
    assert dec.enforced_notional == 500.0


@pytest.mark.asyncio
async def test_evaluate_intent_hard_gate_enforces_block(db, monkeypatch):
    from services import alpha_account_overlay as ov
    from services.alpha_broker_account_context import AccountContext
    # Force HARD_GATE state.
    await ov.status(db)
    await ov._save_state(db, {"mode": "HARD_GATE",
                                "trading_days_completed": 7,
                                "hard_gate_at": datetime.now(timezone.utc).isoformat()})
    ctx = AccountContext(
        broker="public", captured_at_ms=0, equity=100.0, cash=0.0,
        buying_power=0.0, positions=(), open_orders=(),
    )
    monkeypatch.setattr(ov, "get_account_context_for",
                         AsyncMock(return_value=ctx))
    dec = await ov.evaluate_intent(
        db, broker="public", symbol="AAPL", side="BUY", notional=500.0,
    )
    assert dec.mode == "HARD_GATE"
    assert dec.verdict == "BLOCK"
    assert dec.should_block is True
    assert dec.enforced_notional == 0.0


@pytest.mark.asyncio
async def test_evaluate_intent_hard_gate_reduces_size(db, monkeypatch):
    from services import alpha_account_overlay as ov
    from services.alpha_broker_account_context import AccountContext
    await ov.status(db)
    await ov._save_state(db, {"mode": "HARD_GATE",
                                "trading_days_completed": 7})
    # BP $50, buffer 5% of equity ($5) → spendable $45. Desired $100 → half.
    ctx = AccountContext(
        broker="public", captured_at_ms=0, equity=100.0, cash=50.0,
        buying_power=50.0, positions=(), open_orders=(),
    )
    monkeypatch.setattr(ov, "get_account_context_for",
                         AsyncMock(return_value=ctx))
    dec = await ov.evaluate_intent(
        db, broker="public", symbol="AAPL", side="BUY", notional=100.0,
    )
    assert dec.mode == "HARD_GATE"
    assert dec.verdict == "REDUCE"
    assert dec.enforced_notional < 100.0
    assert dec.enforced_notional > 0.0


@pytest.mark.asyncio
async def test_transition_report_counts_shadow_records(db, monkeypatch):
    from services import alpha_account_overlay as ov
    from services.alpha_broker_account_context import AccountContext
    await ov.status(db)
    ctx_no_bp = AccountContext(
        broker="public", captured_at_ms=0, equity=100.0, cash=0.0,
        buying_power=0.0, positions=(), open_orders=(),
    )
    ctx_ok = AccountContext(
        broker="public", captured_at_ms=0, equity=10_000.0, cash=5_000.0,
        buying_power=5_000.0, positions=(), open_orders=(),
    )
    monkeypatch.setattr(ov, "get_account_context_for",
                         AsyncMock(side_effect=[ctx_no_bp, ctx_ok, ctx_no_bp]))
    await ov.evaluate_intent(db, broker="public", symbol="A", side="BUY", notional=500)
    await ov.evaluate_intent(db, broker="public", symbol="B", side="BUY", notional=100)
    await ov.evaluate_intent(db, broker="public", symbol="C", side="BUY", notional=500)
    r = await ov.transition_report(db)
    assert r["counts"]["BLOCK"] == 2
    assert r["counts"]["PASS"] == 1
    assert r["total_records"] == 3
