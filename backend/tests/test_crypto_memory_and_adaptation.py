"""Tests for the crypto memory writer + closer + adaptation loop."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.crypto_memory_writer import (
    classify_crypto_failure,
    classify_crypto_regime,
    write_crypto_trade_memory,
)
from services.crypto_closer import (
    close_expired_crypto_trades,
    compute_crypto_pnl,
    compute_crypto_r_multiple,
)
from services.crypto_adaptation_service import (
    BASE_DOWN_WEIGHT,
    apply_crypto_adaptations_to_signal,
    detect_crypto_adaptations,
)


# ── Regime classifier ─────────────────────────────────────────────────────────


def test_regime_parabolic_takes_priority_over_overbought():
    # |momentum| ≥ 8% AND RSI ≥ 70 → parabolic wins
    assert classify_crypto_regime({"rsi": 80, "momentum_5b": 0.10}) == "parabolic"


def test_regime_overbought_when_rsi_high_and_momentum_normal():
    assert classify_crypto_regime({"rsi": 75, "momentum_5b": 0.02}) == "overbought"


def test_regime_oversold_when_rsi_low():
    assert classify_crypto_regime({"rsi": 25, "momentum_5b": -0.02}) == "oversold"


def test_regime_trend_up_on_positive_momentum():
    assert classify_crypto_regime({"rsi": 55, "momentum_5b": 0.02}) == "trend_up"


def test_regime_trend_down_on_negative_momentum():
    assert classify_crypto_regime({"rsi": 45, "momentum_5b": -0.02}) == "trend_down"


def test_regime_neutral_on_missing_data():
    assert classify_crypto_regime({}) == "neutral"


# ── Failure-code classifier ───────────────────────────────────────────────────


def test_failure_code_none_on_winners():
    assert classify_crypto_failure({"r_multiple": 1.5}) is None
    assert classify_crypto_failure({"r_multiple": 0}) is None


def test_failure_code_liquidity_gap_takes_top_priority():
    """Low volume invalidates any technical interpretation."""
    code = classify_crypto_failure({
        "r_multiple": -1.0, "volume_ratio": 0.5,
        "rsi": 75, "momentum_5b": 0.10,
    })
    assert code == "LIQUIDITY_GAP"


def test_failure_code_parabolic_exhaustion():
    code = classify_crypto_failure({
        "r_multiple": -1.0, "momentum_5b": 0.09, "rsi": 65,
    })
    assert code == "PARABOLIC_EXHAUSTION"


def test_failure_code_extreme_rsi():
    code = classify_crypto_failure({"r_multiple": -1.0, "rsi": 75})
    assert code == "EXTREME_RSI_FAILURE"


def test_failure_code_trend_fakeout_default():
    code = classify_crypto_failure({
        "r_multiple": -0.8, "rsi": 55, "momentum_5b": 0.01,
    })
    assert code == "TREND_FAKEOUT"


# ── write_crypto_trade_memory ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_memory_no_op_when_db_missing():
    out = await write_crypto_trade_memory(None, {"status": "closed"})
    assert out["written"] is False
    assert out["reason"] == "db_missing"


@pytest.mark.asyncio
async def test_write_memory_skips_open_trades():
    db = AsyncMock()
    out = await write_crypto_trade_memory(db, {"status": "open"})
    assert out["written"] is False
    assert out["reason"] == "trade_not_closed"
    db.crypto_trade_memory.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_write_memory_upserts_full_record():
    db = AsyncMock()
    captured_filter: dict = {}
    captured_update: dict = {}

    async def _capture(filter_q, update_q, upsert=False):
        captured_filter.update(filter_q)
        captured_update.update(update_q)

    db.crypto_trade_memory.update_one = _capture

    closed = {
        "trade_id": "t-1",
        "status": "closed",
        "symbol": "BTC",
        "direction": "LONG",
        "entry_price": 70000.0,
        "exit_price": 71000.0,
        "quantity": 0.01,
        "pnl": 10.0,
        "r_multiple": 0.5,
        "rsi": 65, "ema20": 69500, "momentum_5b": 0.02,
        "strategist_conf": 0.85, "auditor_conf": 0.72, "confidence": 0.78,
        "strategist_reason": "trend",
        "auditor_reason": "head_room_ok",
    }

    out = await write_crypto_trade_memory(db, closed)

    assert out["written"] is True
    assert captured_filter == {"trade_id": "t-1"}
    doc = captured_update["$set"]
    assert doc["asset_class"] == "crypto"
    assert doc["outcome"] == "win"  # r_multiple > 0
    assert doc["regime"] == "trend_up"  # rsi 65, momentum +2%
    assert doc["failure_code"] is None  # winner
    assert doc["agent_context"]["strategist_conf"] == 0.85
    assert doc["agent_context"]["combined_conf"] == 0.78
    assert doc["strategy_snapshot"]["rsi"] == 65


@pytest.mark.asyncio
async def test_write_memory_loss_gets_failure_code():
    db = AsyncMock()
    captured_update: dict = {}

    async def _capture(filter_q, update_q, upsert=False):
        captured_update.update(update_q)

    db.crypto_trade_memory.update_one = _capture

    losing = {
        "trade_id": "t-loss",
        "status": "closed",
        "symbol": "BTC",
        "r_multiple": -1.2,
        "rsi": 75,
        "momentum_5b": 0.03,
    }

    await write_crypto_trade_memory(db, losing)
    doc = captured_update["$set"]
    assert doc["outcome"] == "loss"
    assert doc["failure_code"] == "EXTREME_RSI_FAILURE"


# ── PnL + R-multiple math ─────────────────────────────────────────────────────


def test_pnl_long_profit():
    assert compute_crypto_pnl("LONG", 100.0, 110.0, 1.0) == 10.0


def test_pnl_short_profit_on_falling_mark():
    assert compute_crypto_pnl("SHORT", 100.0, 90.0, 1.0) == 10.0


def test_pnl_long_loss():
    assert compute_crypto_pnl("LONG", 100.0, 95.0, 2.0) == -10.0


def test_r_multiple_zero_when_stop_loss_missing():
    assert compute_crypto_r_multiple("LONG", 100.0, 110.0, None) == 0.0
    assert compute_crypto_r_multiple("LONG", 100.0, 110.0, 0) == 0.0


def test_r_multiple_long_winner():
    # Risk = 100 - 95 = 5; reward = 110 - 100 = 10 → R = 2.0
    assert compute_crypto_r_multiple("LONG", 100.0, 110.0, 95.0) == 2.0


def test_r_multiple_short_winner():
    # SHORT: risk = 105 - 100 = 5; reward = 100 - 90 = 10 → R = 2.0
    assert compute_crypto_r_multiple("SHORT", 100.0, 90.0, 105.0) == 2.0


def test_r_multiple_loser_negative():
    assert compute_crypto_r_multiple("LONG", 100.0, 92.0, 95.0) < 0


# ── close_expired_crypto_trades end-to-end ────────────────────────────────────


class _FakeDB:
    """Async-iter capable Motor stub."""

    def __init__(self, open_trades: list[dict]):
        self._trades = list(open_trades)
        self.crypto_paper_trades = AsyncMock()
        self.crypto_paper_trades.find = self._find
        self.crypto_paper_trades.update_one = self._update
        self.crypto_trade_memory = AsyncMock()
        self.paper_trades = AsyncMock()  # MUST stay untouched
        self.updates: list[dict] = []
        self.memory_upserts: list[dict] = []
        self.crypto_trade_memory.update_one = self._memory_update

    def __getitem__(self, k):
        return getattr(self, k)

    def _find(self, query):
        # Filter the in-memory trades by the same predicates the
        # closer would use against Mongo.
        cutoff = (query.get("opened_at") or {}).get("$lte")
        rows = [
            t for t in self._trades
            if t.get("status") == "open"
            and (cutoff is None or t.get("opened_at") <= cutoff)
        ]

        class _Cursor:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        return _Cursor(rows)

    async def _update(self, filter_q, update_q):
        self.updates.append({"filter": filter_q, "update": update_q})

        class _Result:
            modified_count = 1

        return _Result()

    async def _memory_update(self, filter_q, update_q, upsert=False):
        self.memory_upserts.append({"filter": filter_q, "update": update_q})


def _fresh_trade(**overrides) -> dict[str, Any]:
    base = {
        "_id": "mongo-id-1",
        "trade_id": "t-1",
        "asset_class": "crypto",
        "symbol": "BTC",
        "direction": "LONG",
        "entry_price": 70000.0,
        "quantity": 0.01,
        "stop_loss": 65000.0,
        "status": "open",
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=15),
        "rsi": 65,
        "momentum_5b": 0.02,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_closer_no_op_when_db_missing():
    out = await close_expired_crypto_trades(None, AsyncMock())
    assert out["closed"] == 0
    assert out["reason"] == "db_missing"


@pytest.mark.asyncio
async def test_closer_closes_aged_trade_and_writes_memory():
    db = _FakeDB([_fresh_trade()])

    async def quote(_sym):
        return {"price": 71000.0}

    summary = await close_expired_crypto_trades(db, quote, hold_hours=12)

    assert summary["closed"] == 1
    assert summary["errors"] == 0
    # Lifecycle row updated
    update = db.updates[0]["update"]["$set"]
    assert update["status"] == "closed"
    assert update["close_reason"] == "hold_window_expired"
    assert update["pnl"] > 0  # LONG winning at higher exit
    # Memory writer fired
    assert len(db.memory_upserts) == 1
    mem = db.memory_upserts[0]["update"]["$set"]
    assert mem["asset_class"] == "crypto"
    assert mem["outcome"] == "win"
    assert mem["regime"] == "trend_up"
    # Equity collection untouched
    db.paper_trades.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_closer_skips_non_crypto_asset_class():
    bogus = _fresh_trade(asset_class="equity", symbol="AAPL")
    db = _FakeDB([bogus])

    async def quote(_sym):
        return {"price": 200.0}

    summary = await close_expired_crypto_trades(db, quote)
    assert summary["closed"] == 0
    assert summary["skipped"] == 1
    assert len(db.updates) == 0
    assert len(db.memory_upserts) == 0


@pytest.mark.asyncio
async def test_closer_recovers_from_quote_outage():
    db = _FakeDB([_fresh_trade()])

    async def quote(_sym):
        raise RuntimeError("upstream timeout")

    summary = await close_expired_crypto_trades(db, quote)
    assert summary["closed"] == 0
    assert summary["errors"] == 1


# ── Adaptation detector + applier ─────────────────────────────────────────────


class _AdaptationDB:
    """DB stub backed by lists for adaptations + memory rows."""

    def __init__(self, memory_rows: list[dict],
                 active_adaptations: list[dict] | None = None):
        self._memory = list(memory_rows)
        self._active = list(active_adaptations or [])
        self.crypto_trade_memory = AsyncMock()
        self.crypto_model_adaptations = AsyncMock()
        self.inserts: list[dict] = []
        self.crypto_trade_memory.find = self._memory_find
        self.crypto_model_adaptations.count_documents = self._count_active
        self.crypto_model_adaptations.find = self._adapt_find
        self.crypto_model_adaptations.find_one = self._adapt_find_one
        self.crypto_model_adaptations.insert_one = self._insert

    def _memory_find(self, query):
        rows = self._memory

        class _Cursor:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        return _Cursor(rows)

    async def _count_active(self, query):
        return len(self._active)

    def _adapt_find(self, query):
        rows = self._active

        class _Cursor:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        return _Cursor(rows)

    async def _adapt_find_one(self, query):
        # Return None → no cooldown
        return None

    async def _insert(self, doc):
        self.inserts.append(dict(doc))


@pytest.mark.asyncio
async def test_detect_returns_empty_when_no_evidence():
    db = _AdaptationDB(memory_rows=[])
    out = await detect_crypto_adaptations(db)
    assert out == []
    assert db.inserts == []


@pytest.mark.asyncio
async def test_detect_creates_adaptation_when_threshold_crossed():
    losers = [
        {"failure_code": "EXTREME_RSI_FAILURE", "regime": "overbought",
         "r_multiple": -1.2, "created_at": datetime.now(timezone.utc)}
        for _ in range(5)  # MIN_EVIDENCE_COUNT
    ]
    db = _AdaptationDB(memory_rows=losers)

    out = await detect_crypto_adaptations(db)

    assert len(out) == 1
    a = out[0]
    assert a["failure_code"] == "EXTREME_RSI_FAILURE"
    assert a["regime"] == "overbought"
    assert a["factor"] == BASE_DOWN_WEIGHT
    assert a["evidence_count"] == 5
    assert a["active"] is True
    assert a["asset_class"] == "crypto"


@pytest.mark.asyncio
async def test_detect_skips_when_below_evidence_threshold():
    losers = [
        {"failure_code": "TREND_FAKEOUT", "regime": "trend_up",
         "r_multiple": -0.5, "created_at": datetime.now(timezone.utc)}
        for _ in range(3)  # below MIN_EVIDENCE_COUNT (5)
    ]
    db = _AdaptationDB(memory_rows=losers)
    out = await detect_crypto_adaptations(db)
    assert out == []


@pytest.mark.asyncio
async def test_detect_caps_at_max_active_adaptations():
    """If MAX_ACTIVE already exists, detector skips entirely."""
    losers = [
        {"failure_code": "EXTREME_RSI_FAILURE", "regime": "overbought",
         "r_multiple": -1.0, "created_at": datetime.now(timezone.utc)}
        for _ in range(20)
    ]
    db = _AdaptationDB(memory_rows=losers, active_adaptations=[
        {"failure_code": "X", "regime": "Y", "factor": 0.85}
        for _ in range(4)  # MAX_ACTIVE_CRYPTO_ADAPTATIONS
    ])
    out = await detect_crypto_adaptations(db)
    assert out == []
    assert db.inserts == []


@pytest.mark.asyncio
async def test_apply_no_op_when_no_active_adaptations():
    db = _AdaptationDB(memory_rows=[], active_adaptations=[])
    sig = {"direction": "LONG", "confidence": 0.75, "regime": "trend_up"}
    out = await apply_crypto_adaptations_to_signal(db, sig)
    assert out["confidence"] == 0.75
    assert out["direction"] == "LONG"
    assert out["crypto_adaptations_applied"] == []


@pytest.mark.asyncio
async def test_apply_multiplies_confidence_by_matching_adaptation():
    db = _AdaptationDB(
        memory_rows=[],
        active_adaptations=[
            {"failure_code": "EXTREME_RSI_FAILURE", "regime": "overbought",
             "factor": 0.85, "reason": "stuff", "expires_at": datetime.now(timezone.utc) + timedelta(days=10)},
        ],
    )
    sig = {"direction": "LONG", "confidence": 0.80, "regime": "overbought"}
    out = await apply_crypto_adaptations_to_signal(db, sig)
    # 0.80 * 0.85 = 0.68
    assert abs(out["confidence"] - 0.68) < 1e-3
    assert out["direction"] == "LONG"  # still above floor
    assert len(out["crypto_adaptations_applied"]) == 1


@pytest.mark.asyncio
async def test_apply_forces_hold_when_below_floor():
    db = _AdaptationDB(
        memory_rows=[],
        active_adaptations=[
            {"failure_code": "EXTREME_RSI_FAILURE", "regime": "overbought",
             "factor": 0.5, "reason": "harsh", "expires_at": datetime.now(timezone.utc) + timedelta(days=10)},
        ],
    )
    sig = {"direction": "LONG", "confidence": 0.70, "regime": "overbought"}
    out = await apply_crypto_adaptations_to_signal(db, sig)
    # 0.70 * 0.5 = 0.35 < 0.60 → forced HOLD
    assert out["direction"] == "HOLD"
    assert "crypto_adaptation_reduced_confidence_below_floor" in out["reason"]


@pytest.mark.asyncio
async def test_apply_does_not_match_unrelated_adaptation():
    db = _AdaptationDB(
        memory_rows=[],
        active_adaptations=[
            {"failure_code": "PARABOLIC_EXHAUSTION", "regime": "parabolic",
             "factor": 0.5, "reason": "parabolic-only",
             "expires_at": datetime.now(timezone.utc) + timedelta(days=10)},
        ],
    )
    sig = {"direction": "LONG", "confidence": 0.80, "regime": "trend_up"}
    out = await apply_crypto_adaptations_to_signal(db, sig)
    assert out["confidence"] == 0.80
    assert out["crypto_adaptations_applied"] == []
