"""Tests for the isolated crypto paper-trade closer.

Architecture this test pins down
--------------------------------
* The closer reads/writes ONLY ``crypto_paper_trades`` —
  never the legacy ``paper_trades`` collection.
* Exit priority is SL → TP → max_hold (12h default).
* Direction-aware PnL: LONG profits on rising mark, SHORT profits
  on falling mark.
* Quote outage on one trade leaves it open — never crashes the tick.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.crypto_paper_trade_closer import (
    _calc_pnl,
    _hit_stop,
    _hit_target,
    close_due_crypto_trades,
)


# ── PnL math ──────────────────────────────────────────────────────────────────


def test_pnl_long_profit():
    pnl_usd, pnl_pct = _calc_pnl("LONG", 100.0, 110.0, 1.0)
    assert pnl_usd == 10.0
    assert pnl_pct == 10.0


def test_pnl_short_profit_on_falling_mark():
    pnl_usd, pnl_pct = _calc_pnl("SHORT", 100.0, 90.0, 1.0)
    assert pnl_usd == 10.0
    assert pnl_pct == 10.0


def test_pnl_long_loss():
    pnl_usd, pnl_pct = _calc_pnl("LONG", 100.0, 95.0, 2.0)
    assert pnl_usd == -10.0
    assert pnl_pct == -5.0


# ── Exit triggers ─────────────────────────────────────────────────────────────


def test_stop_loss_triggers_for_long_below_threshold():
    assert _hit_stop("LONG", 100.0, 89.0, 90.0) is True
    assert _hit_stop("LONG", 100.0, 91.0, 90.0) is False


def test_stop_loss_triggers_for_short_above_threshold():
    assert _hit_stop("SHORT", 100.0, 111.0, 110.0) is True
    assert _hit_stop("SHORT", 100.0, 109.0, 110.0) is False


def test_take_profit_triggers_for_long_above_threshold():
    assert _hit_target("LONG", 100.0, 121.0, 120.0) is True
    assert _hit_target("LONG", 100.0, 119.0, 120.0) is False


def test_take_profit_triggers_for_short_below_threshold():
    assert _hit_target("SHORT", 100.0, 79.0, 80.0) is True
    assert _hit_target("SHORT", 100.0, 81.0, 80.0) is False


# ── End-to-end closer ─────────────────────────────────────────────────────────


class _FakeDB:
    """Motor-shaped stub. Only the ``crypto_paper_trades`` collection
    can be touched — accessing ``paper_trades`` records the breach."""

    def __init__(self, trades: list[dict]):
        self._trades = list(trades)
        self.crypto_paper_trades = AsyncMock()
        self.crypto_paper_trades.find = self._find
        self.crypto_paper_trades.update_one = self._update
        self.paper_trades = AsyncMock()
        self.updates: list[dict] = []

    def _find(self, query):
        class _Cursor:
            async def to_list(self_inner, length=None):
                return [t for t in self._trades if t.get("status") == "open"]
        return _Cursor()

    async def _update(self, filter_q, update_doc):
        self.updates.append({"filter": filter_q, "update": update_doc})


def _open_trade(**overrides) -> dict:
    base = {
        "trade_id": "t-1",
        "asset_class": "crypto",
        "symbol": "BTC",
        "direction": "LONG",
        "entry_price": 70000.0,
        "quantity": 0.01,
        "status": "open",
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "stop_loss": None,
        "take_profit": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_closer_closes_long_on_stop_loss():
    sl = 65000.0
    db = _FakeDB([_open_trade(stop_loss=sl)])

    async def quote(symbol):
        return {"price": 64000.0}  # below SL

    summary = await close_due_crypto_trades(db, quote)

    assert summary["closed"] == 1
    assert summary["reasons"]["stop_loss"] == 1
    update = db.updates[0]["update"]["$set"]
    assert update["status"] == "closed"
    assert update["exit_reason"] == "stop_loss"
    assert update["exit_price"] == 64000.0
    # LONG losing money
    assert update["pnl_usd"] < 0


@pytest.mark.asyncio
async def test_closer_closes_short_on_take_profit():
    tp = 65000.0
    db = _FakeDB([_open_trade(direction="SHORT", take_profit=tp, stop_loss=None)])

    async def quote(symbol):
        return {"price": 64000.0}  # below TP for SHORT → profit

    summary = await close_due_crypto_trades(db, quote)

    assert summary["closed"] == 1
    assert summary["reasons"]["take_profit"] == 1
    update = db.updates[0]["update"]["$set"]
    assert update["exit_reason"] == "take_profit"
    # SHORT making money
    assert update["pnl_usd"] > 0


@pytest.mark.asyncio
async def test_closer_force_closes_after_max_hold():
    # 15h old, no SL/TP, max_hold=12 → force close
    old_open = datetime.now(timezone.utc) - timedelta(hours=15)
    db = _FakeDB([_open_trade(opened_at=old_open)])

    async def quote(symbol):
        return {"price": 71000.0}  # mild profit

    summary = await close_due_crypto_trades(db, quote, max_hold_hours=12.0)

    assert summary["closed"] == 1
    assert summary["reasons"]["max_hold"] == 1
    update = db.updates[0]["update"]["$set"]
    assert update["exit_reason"] == "max_hold"
    assert update["pnl_usd"] > 0


@pytest.mark.asyncio
async def test_closer_leaves_fresh_trade_open():
    # 1h old, no SL/TP, well within max_hold
    db = _FakeDB([_open_trade()])

    async def quote(symbol):
        return {"price": 70500.0}

    summary = await close_due_crypto_trades(db, quote, max_hold_hours=12.0)

    assert summary["closed"] == 0
    assert summary["skipped"] == 1
    assert len(db.updates) == 0


@pytest.mark.asyncio
async def test_closer_handles_quote_outage_safely():
    db = _FakeDB([_open_trade()])

    async def quote(symbol):
        raise RuntimeError("upstream timeout")

    summary = await close_due_crypto_trades(db, quote)

    assert summary["closed"] == 0
    assert summary["errors"] == 1
    # Trade stays open — next tick will retry
    assert len(db.updates) == 0


@pytest.mark.asyncio
async def test_closer_skips_non_crypto_asset_class_belt_and_suspenders():
    """Even though the architectural firewall stops equity trades from
    ever reaching this collection, the closer also defends in depth:
    if a row with ``asset_class`` other than 'crypto' somehow appears,
    skip it (don't run equity exit logic)."""
    bogus = _open_trade(asset_class="equity", symbol="AAPL")
    db = _FakeDB([bogus])

    async def quote(symbol):
        return {"price": 200.0}

    summary = await close_due_crypto_trades(db, quote)

    assert summary["closed"] == 0
    assert summary["skipped"] == 1
    assert len(db.updates) == 0


@pytest.mark.asyncio
async def test_closer_priority_sl_beats_tp_beats_maxhold():
    """If SL, TP, and max_hold all triggered simultaneously, SL wins."""
    old_open = datetime.now(timezone.utc) - timedelta(hours=15)
    db = _FakeDB([_open_trade(
        opened_at=old_open, stop_loss=69000.0, take_profit=80000.0,
    )])

    async def quote(symbol):
        return {"price": 60000.0}  # < SL for LONG

    summary = await close_due_crypto_trades(db, quote, max_hold_hours=12.0)

    assert summary["closed"] == 1
    assert summary["reasons"]["stop_loss"] == 1
    assert summary["reasons"]["take_profit"] == 0
    assert summary["reasons"]["max_hold"] == 0


@pytest.mark.asyncio
async def test_closer_never_touches_equity_collection():
    db = _FakeDB([_open_trade()])

    async def quote(symbol):
        return {"price": 71000.0}

    await close_due_crypto_trades(db, quote, max_hold_hours=12.0)

    db.paper_trades.update_one.assert_not_called()
    db.paper_trades.insert_one.assert_not_called()
    db.paper_trades.find.assert_not_called()
