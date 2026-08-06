"""
iter_188 — Verify _execute_bot_trade(mode='live') routes through
services.public_equity_live_executor.maybe_route_live so bot fires get
the Ring 1 + Ring 3 protection stack (confidence floor, cooldown,
chasing filter, evidence sizing, structured audit).

Paper branch regression: still routes through paper_trading_service.execute_trade.
"""
from __future__ import annotations

import asyncio
import sys
import os
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

sys.path.insert(0, "/app/backend")

from services import trading_bot_service as tbs  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bot(mode: str = "live", name: str = "RED", min_conf: float = 0.7,
         btype: str = "signal") -> dict:
    return {
        "_id": ObjectId(),
        "name": name,
        "user_id": "u1",
        "mode": mode,
        "min_ai_confidence": min_conf,
        "type": btype,
    }


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    """Provide a non-None _db so the live branch does not early-return."""
    monkeypatch.setattr(tbs, "_db", SimpleNamespace(name="test"))
    yield


@pytest.fixture(autouse=True)
def _no_op_risk_guards(monkeypatch):
    """Prevent DB-touching risk guards from mangling qty."""
    async def _fake_guards(bot, user_id, qty):
        return qty, {"halved": False}
    monkeypatch.setattr(tbs, "_apply_bot_risk_guards", _fake_guards)
    yield


class _RouteRecorder:
    def __init__(self, ret):
        self.ret = ret
        self.calls: list[tuple[Any, dict]] = []

    async def __call__(self, db, *, intent):
        self.calls.append((db, intent))
        return self.ret


def _patch_maybe_route_live(monkeypatch, ret):
    import services.public_equity_live_executor as pex
    rec = _RouteRecorder(ret)
    monkeypatch.setattr(pex, "maybe_route_live", rec)
    return rec


# ---------------------------------------------------------------------------
# LIVE branch — BUY -> open_long
# ---------------------------------------------------------------------------

def test_live_buy_maps_to_open_long_and_carries_bot_context(monkeypatch):
    rec = _patch_maybe_route_live(monkeypatch, {
        "trade_id": "t1",
        "broker_order_id": "ord-1",
        "symbol": "AAPL",
        "notional": 1.0,
    })
    bot = _bot(mode="live", name="RED", min_conf=0.7, btype="signal")

    out = asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))

    assert out["status"] == "filled"
    assert out["broker_order_id"] == "ord-1"
    assert out["trade_id"] == "t1"
    assert out["symbol"] == "AAPL"
    assert out["side"] == "BUY"
    assert out["qty"] == 1.0

    assert len(rec.calls) == 1
    _db_arg, intent = rec.calls[0]
    assert intent["symbol"] == "AAPL"
    assert intent["direction"] == "BUY"
    assert intent["intent_kind"] == "open_long"
    assert intent["confidence"] == 0.7
    assert intent["raw_confidence"] == 0.7
    assert intent["calibrated_confidence"] is None
    assert intent["strategy_id"] == "bot:RED"
    assert intent["source_signal"].startswith("trading_bot:")
    assert "signal" in intent["source_signal"]
    assert intent["user_id"] == "u1"


# ---------------------------------------------------------------------------
# LIVE branch — SELL -> close_long (bypasses entry filters in executor)
# ---------------------------------------------------------------------------

def test_live_sell_maps_to_close_long(monkeypatch):
    rec = _patch_maybe_route_live(monkeypatch, {
        "trade_id": "t2", "broker_order_id": "ord-2",
    })
    bot = _bot(mode="live", name="RED")
    out = asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "sell", 1.0, 150.0))

    assert out["status"] == "filled"
    assert out["side"] == "sell"  # preserves incoming case
    _, intent = rec.calls[0]
    assert intent["direction"] == "SELL"
    assert intent["intent_kind"] == "close_long"


# ---------------------------------------------------------------------------
# LIVE branch — gated -> {'error': 'gated by executor safety checks'}
# ---------------------------------------------------------------------------

def test_live_gated_returns_error(monkeypatch):
    _patch_maybe_route_live(monkeypatch, None)
    bot = _bot(mode="live", name="RED", min_conf=0.7)
    out = asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))
    assert out == {"error": "gated by executor safety checks"}


# ---------------------------------------------------------------------------
# LIVE branch — confidence fallback when min_ai_confidence missing/None
# ---------------------------------------------------------------------------

def test_live_confidence_fallback_to_065(monkeypatch):
    rec = _patch_maybe_route_live(monkeypatch, {
        "trade_id": "t", "broker_order_id": "o",
    })
    bot = _bot(mode="live", name="RED")
    bot["min_ai_confidence"] = None
    asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))
    _, intent = rec.calls[0]
    assert intent["confidence"] == 0.65
    assert intent["raw_confidence"] == 0.65


# ---------------------------------------------------------------------------
# LIVE branch — strategy_id falls back to bot id when name missing
# ---------------------------------------------------------------------------

def test_live_strategy_id_uses_id_when_name_missing(monkeypatch):
    rec = _patch_maybe_route_live(monkeypatch, {
        "trade_id": "t", "broker_order_id": "o",
    })
    bot = _bot(mode="live", name=None)
    bot["name"] = None
    asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))
    _, intent = rec.calls[0]
    # strategy_id should include the bot _id string
    assert intent["strategy_id"].startswith("bot:")
    assert str(bot["_id"]) in intent["strategy_id"]


# ---------------------------------------------------------------------------
# LIVE branch — exception path returns single 'Live execution failed' error
# ---------------------------------------------------------------------------

def test_live_exception_returns_single_error(monkeypatch):
    import services.public_equity_live_executor as pex

    async def _boom(db, *, intent):
        raise RuntimeError("boom")
    monkeypatch.setattr(pex, "maybe_route_live", _boom)

    bot = _bot(mode="live", name="RED")
    out = asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))
    assert "error" in out
    assert out["error"].startswith("Live execution failed:")
    assert "boom" in out["error"]


# ---------------------------------------------------------------------------
# LIVE branch — no _db handle -> early error
# ---------------------------------------------------------------------------

def test_live_no_db_returns_error(monkeypatch):
    monkeypatch.setattr(tbs, "_db", None)
    bot = _bot(mode="live")
    out = asyncio.run(tbs._execute_bot_trade(bot, "AAPL", "BUY", 1.0, 150.0))
    assert out == {"error": "DB unavailable"}


# ---------------------------------------------------------------------------
# PAPER branch regression — still calls paper_trading_service.execute_trade
# with (user_id, symbol, side.upper(), qty, stop_loss, take_profit)
# ---------------------------------------------------------------------------

def test_paper_branch_unchanged(monkeypatch):
    captured: dict = {}

    async def _fake_execute(user_id, symbol, side, qty, *,
                            stop_loss=None, take_profit=None):
        captured.update({
            "user_id": user_id, "symbol": symbol, "side": side,
            "qty": qty, "stop_loss": stop_loss, "take_profit": take_profit,
        })
        return {"status": "paper_filled", "id": "p1"}

    import services.paper_trading_service as pts
    monkeypatch.setattr(pts, "execute_trade", _fake_execute)

    # Also make sure live executor is NOT touched
    import services.public_equity_live_executor as pex
    rec = _RouteRecorder({"boom": True})
    monkeypatch.setattr(pex, "maybe_route_live", rec)

    bot = _bot(mode="paper", name="AAPL-Grid")
    out = asyncio.run(tbs._execute_bot_trade(
        bot, "AAPL", "buy", 2.0, 150.0, stop_loss=140.0, take_profit=160.0,
    ))

    assert out == {"status": "paper_filled", "id": "p1"}
    assert captured == {
        "user_id": "u1", "symbol": "AAPL", "side": "BUY", "qty": 2.0,
        "stop_loss": 140.0, "take_profit": 160.0,
    }
    assert rec.calls == []  # live path untouched


# ---------------------------------------------------------------------------
# Circuit-breaker regression — guards run for BOTH paper and live
# ---------------------------------------------------------------------------

def test_risk_guards_run_for_live_and_paper(monkeypatch):
    calls: list[str] = []

    async def _tracking_guards(bot, user_id, qty):
        calls.append(bot.get("mode"))
        return qty / 2, {"halved": True}
    monkeypatch.setattr(tbs, "_apply_bot_risk_guards", _tracking_guards)

    # live
    rec = _patch_maybe_route_live(monkeypatch, {
        "trade_id": "t", "broker_order_id": "o",
    })
    asyncio.run(tbs._execute_bot_trade(_bot(mode="live"), "AAPL", "BUY", 4.0, 150.0))

    # paper
    async def _fake_execute(*a, **k):
        return {"status": "paper_filled"}
    import services.paper_trading_service as pts
    monkeypatch.setattr(pts, "execute_trade", _fake_execute)
    asyncio.run(tbs._execute_bot_trade(_bot(mode="paper"), "AAPL", "BUY", 4.0, 150.0))

    assert calls == ["live", "paper"]
