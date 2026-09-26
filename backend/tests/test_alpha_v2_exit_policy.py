"""Alpha Core v2 exit / position-protection policy."""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.alpha_core_v2.contracts import Outcome, PositionState, Stage, Receipt
from services.alpha_core_v2.exit_policy import (
    ExitConfig, ExitPolicyRunner, decide_exit,
)


_CFG = ExitConfig(stop_pct=0.02, take_profit_pct=0.04, trail_pct=0.02, max_hold_s=3600)


def test_stop_loss():
    d = decide_exit(entry=100.0, current=97.9, high_watermark=100.0,
                    held_seconds=0, cfg=_CFG)
    assert d.should_exit and d.reason.startswith("stop_loss")


def test_take_profit():
    d = decide_exit(entry=100.0, current=104.5, high_watermark=104.5,
                    held_seconds=0, cfg=_CFG)
    assert d.should_exit and d.reason.startswith("take_profit")


def test_trailing_stop():
    # peaked at 110, trail 2% → exit if it slips to <=107.8
    d = decide_exit(entry=100.0, current=107.0, high_watermark=110.0,
                    held_seconds=0, cfg=_CFG)
    assert d.should_exit and d.reason.startswith("trailing_stop")


def test_max_hold():
    d = decide_exit(entry=100.0, current=101.0, high_watermark=101.0,
                    held_seconds=4000, cfg=_CFG)
    assert d.should_exit and d.reason.startswith("max_hold")


def test_hold_when_inside_bands():
    d = decide_exit(entry=100.0, current=101.0, high_watermark=101.0,
                    held_seconds=10, cfg=_CFG)
    assert not d.should_exit and d.reason == "hold"


def test_no_anchor():
    assert decide_exit(entry=0.0, current=100.0, high_watermark=100.0,
                       held_seconds=0, cfg=_CFG).reason == "no_anchor"
    assert decide_exit(entry=100.0, current=0.0, high_watermark=100.0,
                       held_seconds=0, cfg=_CFG).reason == "no_anchor"


# ── runner ──────────────────────────────────────────────────────────
class _Quote:
    def __init__(self, price):
        self.price = Decimal(str(price))


class _Store:
    def __init__(self, entry):
        self._entry = entry

    def last_entry_price(self, symbol):
        return self._entry


class _Broker:
    def __init__(self, positions, price):
        self._positions = positions
        self._price = price

    def get_positions(self):
        return list(self._positions)

    def get_execution_quote(self, symbol):
        return _Quote(self._price)


class _Engine:
    def __init__(self, positions, entry, price):
        self.broker = _Broker(positions, price)
        self.store = _Store(entry)
        self.closed = []

    async def close_position(self, symbol, *, reason="operator"):
        self.closed.append((symbol, reason))
        return Receipt("r", "c", symbol, 0, Outcome.TRADED, Stage.CONFIRM,
                       action="close")


@pytest.mark.asyncio
async def test_runner_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALPHA_V2_EXIT_POLICY", raising=False)
    eng = _Engine([PositionState("AAA", 1.0)], entry=100.0, price=50.0)
    r = await ExitPolicyRunner(eng).run()
    assert r["ran"] is False and not eng.closed


@pytest.mark.asyncio
async def test_runner_exits_on_stop(monkeypatch):
    monkeypatch.setenv("ALPHA_V2_STOP_PCT", "0.02")
    eng = _Engine([PositionState("AAA", 1.0)], entry=100.0, price=90.0)  # -10%
    r = await ExitPolicyRunner(eng).run(force=True)
    assert r["ran"] is True
    assert eng.closed and eng.closed[0][0] == "AAA"
    assert r["actions"][0]["exited"] is True


@pytest.mark.asyncio
async def test_runner_holds_when_no_anchor(monkeypatch):
    eng = _Engine([PositionState("AAA", 1.0)], entry=0.0, price=90.0)
    r = await ExitPolicyRunner(eng).run(force=True)
    assert r["ran"] is True and not eng.closed
    assert r["actions"][0]["decision"] == "no_anchor"
