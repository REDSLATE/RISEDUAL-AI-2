"""Skip-log dedup with amplification counter (5-min rolling window).

The audit tile MUST distinguish between:
* unique rejection events   → headline rate
* refire_count               → amplification diagnostic

Guarantees:
* Same (symbol, reason, direction) within 5 min → increment refire.
* Different direction → new row.
* Different reason → new row.
* Different symbol → new row.
* Window rollover → new row with fresh first_seen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import public_equity_live_executor as executor


class _FakeSkipLog:
    """Tiny motor-like stub tracking inserts + updates."""

    def __init__(self):
        self._rows: list[dict[str, Any]] = []
        self._id_counter = 0
        self.inserts = 0
        self.updates = 0

    async def find_one(self, filter_q, *, sort=None):
        # Filter shape: {symbol, reason, direction, last_seen: {$gte: cutoff}}
        cutoff = filter_q.get("last_seen", {}).get("$gte")
        matches = [
            r for r in self._rows
            if r["symbol"] == filter_q["symbol"]
            and r["reason"] == filter_q["reason"]
            and r["direction"] == filter_q["direction"]
            and (cutoff is None or r["last_seen"] >= cutoff)
        ]
        if not matches:
            return None
        if sort:
            matches.sort(key=lambda r: r["last_seen"], reverse=(sort[0][1] == -1))
        return matches[0]

    async def insert_one(self, doc):
        self._id_counter += 1
        doc["_id"] = self._id_counter
        self._rows.append(doc)
        self.inserts += 1

    async def update_one(self, filter_q, update):
        for r in self._rows:
            if r["_id"] == filter_q["_id"]:
                r.update(update.get("$set", {}))
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = r.get(k, 0) + v
                self.updates += 1
                return


class _FakeDB:
    def __init__(self):
        self.intent_skip_log = _FakeSkipLog()


def _intent(direction="BUY", prediction_id="p-1"):
    return {
        "direction": direction,
        "prediction_id": prediction_id,
        "strategy_id": "test",
        "confidence": 0.7,
    }


@pytest.mark.asyncio
async def test_first_write_creates_new_row():
    db = _FakeDB()
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={"move_pct": 4.5})
    assert db.intent_skip_log.inserts == 1
    assert db.intent_skip_log.updates == 0
    row = db.intent_skip_log._rows[0]
    assert row["refire_count"] == 0
    assert row["first_seen"] == row["last_seen"]


@pytest.mark.asyncio
async def test_same_key_within_window_increments_refire():
    db = _FakeDB()
    for i in range(5):
        await executor._log_skip(db, symbol="SMCI", reason="chasing_filter",
                                  intent=_intent(), detail={"move_pct": 4.96})
    assert db.intent_skip_log.inserts == 1
    assert db.intent_skip_log.updates == 4
    row = db.intent_skip_log._rows[0]
    assert row["refire_count"] == 4
    # last_seen advanced but first_seen unchanged
    assert row["last_seen"] >= row["first_seen"]


@pytest.mark.asyncio
async def test_different_direction_creates_new_row():
    db = _FakeDB()
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(direction="BUY"), detail={})
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(direction="SELL"), detail={})
    assert db.intent_skip_log.inserts == 2


@pytest.mark.asyncio
async def test_different_reason_creates_new_row():
    db = _FakeDB()
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={})
    await executor._log_skip(db, symbol="AAPL", reason="no_mark_price",
                              intent=_intent(), detail={})
    assert db.intent_skip_log.inserts == 2


@pytest.mark.asyncio
async def test_different_symbol_creates_new_row():
    db = _FakeDB()
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={})
    await executor._log_skip(db, symbol="MSFT", reason="chasing_filter",
                              intent=_intent(), detail={})
    assert db.intent_skip_log.inserts == 2


@pytest.mark.asyncio
async def test_window_rollover_creates_new_row():
    db = _FakeDB()
    # First insert.
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={})
    # Age the existing row past the 5-min window.
    aged = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.intent_skip_log._rows[0]["last_seen"] = aged
    # New call should insert a fresh row, not increment.
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={})
    assert db.intent_skip_log.inserts == 2


@pytest.mark.asyncio
async def test_detail_reflects_latest_on_refire():
    """Amplification updates ``detail`` to the most recent payload so
    the operator can inspect the current state, not a stale first
    reading."""
    db = _FakeDB()
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={"move_pct": 4.5})
    await executor._log_skip(db, symbol="AAPL", reason="chasing_filter",
                              intent=_intent(), detail={"move_pct": 5.3})
    row = db.intent_skip_log._rows[0]
    assert row["detail"]["move_pct"] == 5.3
    assert row["refire_count"] == 1


@pytest.mark.asyncio
async def test_write_never_raises_on_db_error():
    class _BoomLog:
        async def find_one(self, *a, **kw): raise RuntimeError("mongo down")
        async def insert_one(self, doc):    raise RuntimeError("mongo down")
        async def update_one(self, *a, **kw): raise RuntimeError("mongo down")
    class _BoomDB:
        intent_skip_log = _BoomLog()
    # Must not propagate the exception — observability failures never
    # take down a trading gate.
    await executor._log_skip(_BoomDB(), symbol="X", reason="chasing_filter",
                              intent=_intent(), detail={})


@pytest.mark.asyncio
async def test_write_noops_when_db_is_none():
    # Must be safe to call with no db.
    await executor._log_skip(None, symbol="X", reason="chasing_filter",
                              intent=_intent(), detail={})
