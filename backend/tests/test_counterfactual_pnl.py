"""Counterfactual P&L tracker — unit tests with a mocked price source."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import counterfactual_pnl as cf


# ── In-memory Mongo stub (mirrors the gate test pattern) ────────────


class _Cursor:
    def __init__(self, items):
        self._items = list(items)

    def limit(self, _n):
        return self

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration  # noqa: B904


class _Coll:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.upserts: list[dict] = []

    def find(self, _q):
        return _Cursor(self._rows)

    async def update_one(self, _q, update, *, upsert=False):
        self.upserts.append(update.get("$set", {}))
        return MagicMock()


class _DB:
    def __init__(self, adl_rows=None):
        self._adl = _Coll(adl_rows or [])
        self._summary = _Coll([])

    def __getitem__(self, name):
        if name == cf.ADL_COLLECTION:
            return self._adl
        if name == cf.SUMMARY_COLLECTION:
            return self._summary
        return _Coll([])


# ── Direction + notional resolution ─────────────────────────────────


def test_direction_resolves_long_short_hold():
    assert cf._direction_from({"intended_action": "PAUSED_BY_OPERATOR:BUY"}, "") == "LONG"
    assert cf._direction_from({"intended_action": "PAUSED_BY_OPERATOR:SELL"}, "") == "SHORT"
    assert cf._direction_from({"intended_action": "PAUSED_BY_OPERATOR:LONG_CALL"}, "") == "LONG"
    assert cf._direction_from({"intended_action": "PAUSED_BY_OPERATOR:SHORT_PUT"}, "") == "SHORT"
    assert cf._direction_from({"intended_action": "HOLD"}, "") is None


def test_notional_uses_qty_times_price_when_present():
    n = cf._notional_from({"qty": 5, "price": 100.0})
    assert n == 500.0


def test_notional_falls_back_to_default():
    assert cf._notional_from({}) == cf.DEFAULT_NOTIONAL
    assert cf._notional_from({"qty": 0, "price": 0}) == cf.DEFAULT_NOTIONAL


def test_notional_from_explicit_notional_usd():
    assert cf._notional_from({"notional_usd": 2500.0}) == 2500.0


# ── Single-row scorer ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_one_long_with_positive_move(monkeypatch):
    async def fake_move(_sym, _ts):
        return 0.02  # +2%
    monkeypatch.setattr(cf, "_close_to_close_move", fake_move)
    row = {
        "_id": "x",
        "symbol": "AAPL",
        "lane": "equity_paper",
        "decision": "NO_TRADE",
        "extras": {"intended_action": "PAUSED_BY_OPERATOR:BUY", "qty": 5, "price": 100},
        "recorded_at": datetime.now(timezone.utc),
    }
    result = await cf.score_one(_DB(), row)
    assert result["scored"] is True
    assert result["direction"] == "LONG"
    # Notional = 5 * 100 = 500. Move = +2%. PnL = +10.
    assert result["simulated_pnl_usd"] == 10.0
    assert result["close_to_close_pct"] == 2.0


@pytest.mark.asyncio
async def test_score_one_short_with_positive_move_loses(monkeypatch):
    async def fake_move(_sym, _ts):
        return 0.03
    monkeypatch.setattr(cf, "_close_to_close_move", fake_move)
    row = {
        "_id": "y", "symbol": "TSLA", "lane": "equity_paper",
        "decision": "NO_TRADE",
        "extras": {"intended_action": "PAUSED_BY_OPERATOR:SELL", "notional_usd": 1000},
        "recorded_at": datetime.now(timezone.utc),
    }
    result = await cf.score_one(_DB(), row)
    assert result["scored"] is True
    assert result["direction"] == "SHORT"
    # Short the move → -1 * 0.03 * 1000 = -30
    assert result["simulated_pnl_usd"] == -30.0


@pytest.mark.asyncio
async def test_score_one_unscored_when_no_price(monkeypatch):
    async def fake_move(_sym, _ts):
        return None
    monkeypatch.setattr(cf, "_close_to_close_move", fake_move)
    row = {
        "_id": "z", "symbol": "ABC", "lane": "equity_paper",
        "decision": "NO_TRADE",
        "extras": {"intended_action": "PAUSED_BY_OPERATOR:BUY"},
        "recorded_at": datetime.now(timezone.utc),
    }
    result = await cf.score_one(_DB(), row)
    assert result["scored"] is False
    assert result["simulated_pnl_usd"] == 0.0


@pytest.mark.asyncio
async def test_score_one_unscored_for_hold():
    row = {
        "_id": "h", "symbol": "AAPL", "lane": "x",
        "decision": "NO_TRADE",
        "extras": {"intended_action": "HOLD"},
        "recorded_at": datetime.now(timezone.utc),
    }
    result = await cf.score_one(_DB(), row)
    assert result["scored"] is False
    assert result["direction"] is None


# ── Window roll-up ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_window_aggregates_per_symbol(monkeypatch):
    async def fake_move(symbol, _ts):
        return {"AAPL": 0.01, "TSLA": -0.02}.get(symbol)
    monkeypatch.setattr(cf, "_close_to_close_move", fake_move)
    now = datetime.now(timezone.utc)
    rows = [
        {"_id": "1", "symbol": "AAPL", "lane": "equity_paper",
         "decision": "NO_TRADE",
         "extras": {"intended_action": "PAUSED_BY_OPERATOR:BUY", "notional_usd": 1000},
         "recorded_at": now},
        {"_id": "2", "symbol": "AAPL", "lane": "equity_paper",
         "decision": "NO_TRADE",
         "extras": {"intended_action": "PAUSED_BY_OPERATOR:BUY", "notional_usd": 1000},
         "recorded_at": now},
        {"_id": "3", "symbol": "TSLA", "lane": "equity_paper",
         "decision": "NO_TRADE",
         "extras": {"intended_action": "PAUSED_BY_OPERATOR:SELL", "notional_usd": 1000},
         "recorded_at": now},
    ]
    db = _DB(rows)
    summary = await cf.score_window(
        db, start=now - timedelta(hours=1), end=now + timedelta(hours=1),
    )
    # AAPL long +1% × 1000 × 2 = +20
    # TSLA short, -2% move → +20 (short profits when price falls)
    assert summary["total_receipts"] == 3
    assert summary["scored_receipts"] == 3
    assert summary["simulated_pnl_usd"] == 40.0
    syms = {b["symbol"]: b for b in summary["by_symbol"]}
    assert syms["AAPL"]["n_trades"] == 2
    assert syms["AAPL"]["n_long"] == 2
    assert syms["AAPL"]["simulated_pnl_usd"] == 20.0
    assert syms["TSLA"]["simulated_pnl_usd"] == 20.0


@pytest.mark.asyncio
async def test_persist_daily_summary_upserts(monkeypatch):
    async def fake_yesterday(_db):
        return {
            "window_start": datetime.now(timezone.utc),
            "window_end": datetime.now(timezone.utc),
            "total_receipts": 0,
            "scored_receipts": 0,
            "unscored_receipts": 0,
            "simulated_pnl_usd": 0.0,
            "by_symbol": [],
            "computed_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr(cf, "score_yesterday", fake_yesterday)
    db = _DB()
    out = await cf.persist_daily_summary(db)
    assert out["total_receipts"] == 0
    # Upsert was attempted.
    assert len(db._summary.upserts) == 1
