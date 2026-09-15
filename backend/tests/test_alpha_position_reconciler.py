"""Regression tests — broker-authoritative position reconciliation.

Locks in the fix for the "total bust" regression: a phantom ``open`` ledger
row must never permanently block re-entry, the broker is authoritative for
holdings, and an unreachable broker fails CLOSED (never opens blind).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import alpha_position_reconciler as R


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        out = [d for d in self.docs
               if all(d.get(k) == v for k, v in query.items())]
        return _FakeCursor(out)

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update["$set"])
                n += 1

        class _R:
            modified_count = n
        return _R()


class _FakeDB:
    def __init__(self, rows):
        self.equity_live_trades = _FakeCollection(rows)


class _Client:
    def __init__(self, positions=None, raises=False):
        self._positions = positions or []
        self._raises = raises

    def get_positions(self):
        if self._raises:
            raise RuntimeError("broker unreachable")
        return self._positions


def _row(sym):
    return {"symbol": sym, "status": "open", "broker_id": "public"}


@pytest.mark.asyncio
async def test_held_symbol_blocks_as_real_duplicate():
    db = _FakeDB([_row("QQQ")])
    client = _Client(positions=[{"symbol": "QQQ", "qty": 0.0014}])
    res = await R.reconcile_symbol(db, client, "QQQ")
    assert res["ok"] is True
    assert res["held"] is True          # broker holds it → real dup → caller blocks
    assert res["reconciled"] == 0       # a held row is NOT touched
    assert db.equity_live_trades.docs[0]["status"] == "open"


@pytest.mark.asyncio
async def test_phantom_row_reconciled_and_proceeds():
    db = _FakeDB([_row("SPY")])
    client = _Client(positions=[])       # broker holds nothing
    res = await R.reconcile_symbol(db, client, "SPY")
    assert res["ok"] is True
    assert res["held"] is False          # not held → caller proceeds
    assert res["reconciled"] == 1
    row = db.equity_live_trades.docs[0]
    assert row["status"] == "closed"
    assert row["close_reason"] == "broker_reconciled_missing"
    assert "closed_at" in row and "reconciled_at" in row


@pytest.mark.asyncio
async def test_new_ticker_never_blocked():
    """A fresh mover with no ledger row and no broker position proceeds."""
    db = _FakeDB([])                     # no ledger rows at all
    client = _Client(positions=[])
    res = await R.reconcile_symbol(db, client, "NVDA")
    assert res["ok"] is True and res["held"] is False and res["reconciled"] == 0


@pytest.mark.asyncio
async def test_broker_unreachable_fails_closed():
    db = _FakeDB([_row("AAPL")])
    client = _Client(raises=True)
    res = await R.reconcile_symbol(db, client, "AAPL")
    assert res["ok"] is False            # caller MUST fail closed
    assert res["held"] is None
    # A ledger row must NOT be reconciled on an unknown broker state.
    assert db.equity_live_trades.docs[0]["status"] == "open"


@pytest.mark.asyncio
async def test_full_sweep_clears_only_phantoms():
    rows = [_row("SPY"), _row("AAPL"), _row("QQQ")]
    db = _FakeDB(rows)
    client = _Client(positions=[{"symbol": "QQQ", "qty": 0.5}])  # only QQQ held
    res = await R.reconcile_all(db, client)
    assert res["ok"] is True
    assert res["checked"] == 3
    assert res["reconciled"] == 2                    # SPY + AAPL
    assert res["held"] == ["QQQ"]
    statuses = {d["symbol"]: d["status"] for d in rows}
    assert statuses == {"SPY": "closed", "AAPL": "closed", "QQQ": "open"}
