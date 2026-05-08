"""Unit tests for the AI Core LearningEngine.

Verifies in-isolation behaviour:
  * Outcome canonicalisation (wins, losses, flats, mixed-shape inputs).
  * Confidence bucketing handles 0-1 and 0-100 scales.
  * Idempotent ingestion — same trade_key never double-counts.
  * Condition stats accumulate per (key, value).
  * stats_snapshot win-rate excludes flats from denominator.
  * Reset wipes all in-memory state.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services.ai_core_engine import (
    LearningEngine, _bucket_confidence, _canon_outcome,
    _normalise_trade, _condition_tuples,
)


# ── Pure helpers ─────────────────────────────────────────────────────


def test_canon_outcome():
    assert _canon_outcome("win") == "win"
    assert _canon_outcome("WIN") == "win"
    assert _canon_outcome("hit") == "win"
    assert _canon_outcome(True) == "win"
    assert _canon_outcome("loss") == "loss"
    assert _canon_outcome("MISS") == "loss"
    assert _canon_outcome(False) == "loss"
    assert _canon_outcome("flat") == "flat"
    assert _canon_outcome("neutral") == "flat"
    assert _canon_outcome(None) == "flat"
    assert _canon_outcome("garbage") == "pending"


def test_bucket_confidence_scales():
    # 0-1 scale tolerated
    assert _bucket_confidence(0.85) == "80-90"
    # 0-100 scale
    assert _bucket_confidence(85.0) == "80-90"
    # boundaries
    assert _bucket_confidence(60.0) == "60-70"
    assert _bucket_confidence(69.99) == "60-70"
    assert _bucket_confidence(70.0) == "70-80"
    assert _bucket_confidence(99.99) == "90-100"
    assert _bucket_confidence(100.0) == "90-100"
    # garbage
    assert _bucket_confidence(None) is None
    assert _bucket_confidence("x") is None


def test_normalise_trade_rejects_pending_and_missing():
    assert _normalise_trade({"source": "x", "source_id": "1", "symbol": "AAA", "outcome": "pending"}) is None
    assert _normalise_trade({"source": "x", "source_id": "1", "outcome": "win"}) is None  # no symbol
    assert _normalise_trade({"source_id": "1", "symbol": "AAA", "outcome": "win"}) is None  # no source

    ok = _normalise_trade({
        "source": "paper_trades", "source_id": "t-1", "symbol": "aapl",
        "outcome": "win", "confidence": 0.85, "regime": "trend_up",
    })
    assert ok is not None
    assert ok["trade_key"] == "paper_trades:t-1"
    assert ok["symbol"] == "AAPL"
    assert ok["outcome"] == "win"


def test_condition_tuples_yields_present_keys_only():
    doc = {"regime": "chop", "agent": None, "asset_type": "equity", "confidence": 88}
    pairs = list(_condition_tuples(doc))
    assert ("regime", "chop") in pairs
    assert ("asset_type", "equity") in pairs
    assert ("confidence_bucket", "80-90") in pairs
    assert all(k != "agent" for k, _ in pairs)  # None-valued key not yielded


# ── Engine behaviour (no real db; pass None) ─────────────────────────


@pytest.fixture
def engine():
    return LearningEngine()


@pytest.mark.asyncio
async def test_record_trade_idempotent(engine):
    raw = {
        "source": "paper_trades", "source_id": "t1",
        "symbol": "NVDA", "direction": "BUY", "outcome": "win",
        "confidence": 0.9, "regime": "trend_up", "agent": "war_room",
    }
    r1 = await engine.record_trade(raw)
    r2 = await engine.record_trade(raw)
    assert r1["ok"] and not r1["dedup"]
    assert r2["ok"] and r2["dedup"]
    assert engine.stats["total_resolved"] == 1
    assert engine.stats["wins"] == 1


@pytest.mark.asyncio
async def test_record_trade_aggregates_conditions(engine):
    base = {
        "source": "predictions", "symbol": "META",
        "direction": "BUY", "agent": "war_room", "regime": "chop",
    }
    # 2 wins, 1 loss, 1 flat — all war_room/chop
    await engine.record_trade({**base, "source_id": "1", "outcome": "win", "confidence": 0.85})
    await engine.record_trade({**base, "source_id": "2", "outcome": "win", "confidence": 0.85})
    await engine.record_trade({**base, "source_id": "3", "outcome": "loss", "confidence": 0.85})
    await engine.record_trade({**base, "source_id": "4", "outcome": "flat", "confidence": 0.85})

    snap = engine.stats_snapshot()
    assert snap["total_resolved"] == 4
    assert snap["wins"] == 2
    assert snap["losses"] == 1
    assert snap["flats"] == 1
    # Denominator excludes flats — 2 wins / 3 = 0.6667
    assert snap["denominator"] == 3
    assert snap["win_rate"] == 0.6667

    cond = engine.conditions_snapshot()
    by_agent = {row["value"]: row for row in cond["agent"]}
    assert by_agent["war_room"]["total"] == 4
    assert by_agent["war_room"]["win_rate"] == 0.6667
    by_regime = {row["value"]: row for row in cond["regime"]}
    assert by_regime["chop"]["total"] == 4


@pytest.mark.asyncio
async def test_record_trade_rejects_pending(engine):
    res = await engine.record_trade({
        "source": "p", "source_id": "x", "symbol": "AAA", "outcome": "pending",
    })
    assert res["ok"] is False
    assert engine.stats["total_resolved"] == 0


@pytest.mark.asyncio
async def test_reset_wipes_state(engine):
    await engine.record_trade({
        "source": "p", "source_id": "1", "symbol": "AAA",
        "outcome": "win", "agent": "a",
    })
    assert engine.stats["wins"] == 1
    res = await engine.reset()
    assert res["ok"]
    assert engine.stats["wins"] == 0
    assert engine.stats["total_resolved"] == 0
    assert len(engine.condition_stats) == 0
    assert len(engine.trade_log) == 0


@pytest.mark.asyncio
async def test_record_rejection_counts(engine):
    await engine.record_rejection({
        "source": "scanner", "source_id": "r-1", "symbol": "AAA",
        "reason": "low_confidence",
    })
    assert engine.stats["rejections"] == 1


@pytest.mark.asyncio
async def test_trades_returns_most_recent_first(engine):
    for i in range(5):
        await engine.record_trade({
            "source": "p", "source_id": str(i), "symbol": "AAA",
            "outcome": "win", "agent": "x",
        })
    out = engine.trades(limit=3)
    assert len(out) == 3
    # Most recent first → source_id 4, 3, 2
    assert [t["source_id"] for t in out] == ["4", "3", "2"]


if __name__ == "__main__":
    asyncio.run(test_record_trade_idempotent(LearningEngine()))
    print("OK")
