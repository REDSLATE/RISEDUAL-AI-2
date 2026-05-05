"""Regression tests for the paper-trade auto-closer + stuck-trade tripwire.

The 2026-04-25 audit found 5 AI-driven paper trades stuck `open`
for 9 days because no service closed them — the
`prediction_labeler` only updates `predictions`, not `paper_trades`.
The new `services.paper_trade_closer` runs hourly and computes
direction-aware P&L. The self-test tripwire alarms if any trade
slips past 2× the hold window (closer broken).

Tests:
  * Hold-window math: trade younger than threshold is left alone
  * Old AI trade gets closed with correct direction-aware P&L
  * Long P&L sign correctness
  * Short P&L sign correctness
  * Manual UI ticks (Schema B — no `status` field) are NOT touched
  * Quote failure → trade stays open, no error
  * Disabled flag short-circuits the run
  * Self-test PASS on clean DB / FAIL on stuck trade
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _open_trade(ticker: str, direction: str, entry: float, shares: float,
                hours_old: float, seed_tag: str) -> dict:
    return {
        "trade_id": f"{seed_tag}-{uuid.uuid4().hex[:8]}",
        "ticker": ticker,
        "direction": direction,
        "confidence": 0.5,
        "prediction_id": None,
        "position_size_usd": entry * shares,
        "entry_price": entry,
        "shares": shares,
        "patterns": [],
        "regime": "test",
        "status": "open",
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=hours_old),
        "closed_at": None,
        "exit_price": None,
        "pnl_usd": None,
        "pnl_pct": None,
        "outcome": None,
        "schema_version": 1,
        "seed_tag": seed_tag,
    }


def test_compute_close_long_winner():
    """Long: bought at 100, current 110, 10 shares → +$100 win."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, pnl_pct, outcome = _compute_close("up", 100.0, 110.0, 10.0)
    assert pnl_usd == 100.0
    assert pnl_pct == 0.1
    assert outcome == "win"


def test_compute_close_long_loser():
    from services.paper_trade_closer import _compute_close
    pnl_usd, _, outcome = _compute_close("up", 100.0, 90.0, 10.0)
    assert pnl_usd == -100.0
    assert outcome == "loss"


def test_compute_close_short_winner():
    """Short: sold at 100, covered at 90, 10 shares → +$100 win."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, pnl_pct, outcome = _compute_close("down", 100.0, 90.0, 10.0)
    assert pnl_usd == 100.0
    assert pnl_pct == 0.1
    assert outcome == "win"


def test_compute_close_short_loser():
    """Short: sold at 100, covered at 110, 10 shares → -$100 loss."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, _, outcome = _compute_close("down", 100.0, 110.0, 10.0)
    assert pnl_usd == -100.0
    assert outcome == "loss"


def test_close_due_skips_recent_trades(monkeypatch):
    """Trade younger than hold window stays open."""
    from services import paper_trade_closer

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "24")
    monkeypatch.delenv("PAPER_TRADE_CLOSER_DISABLED", raising=False)

    async def _run():
        db = _mongo_db()
        tag = f"close-fresh-{uuid.uuid4().hex[:6]}"
        try:
            await db.paper_trades.insert_one(
                _open_trade("NVDA", "up", 100.0, 10.0, hours_old=1.0, seed_tag=tag),
            )
            with patch.object(
                paper_trade_closer, "_fetch_price",
                new=AsyncMock(return_value=110.0),
            ):
                result = await paper_trade_closer.close_due_paper_trades(db)
            assert result["closed"] == 0
            still_open = await db.paper_trades.count_documents(
                {"seed_tag": tag, "status": "open"},
            )
            assert still_open == 1
        finally:
            await db.paper_trades.delete_many({"seed_tag": tag})

    asyncio.run(_run())


def test_close_due_closes_old_short_correctly(monkeypatch):
    """Replicates the April 16 incident: 24-hour-old short.
    closer must mark it closed with direction-aware P&L."""
    from services import paper_trade_closer

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "24")

    async def _run():
        db = _mongo_db()
        tag = f"close-short-{uuid.uuid4().hex[:6]}"
        try:
            await db.paper_trades.insert_one(
                _open_trade("AAPL", "down", 266.43, 38.17,
                            hours_old=48.0, seed_tag=tag),
            )
            with patch.object(
                paper_trade_closer, "_fetch_price",
                new=AsyncMock(return_value=271.06),
            ), patch(
                # The closer also probes ``get_alpaca_equity_quote`` to
                # apply exit slippage. Without this patch the test
                # silently fetches LIVE AAPL bid/ask and the assertion
                # against the mocked 271.06 fails any time AAPL is
                # actually trading near a different price (every weekday).
                "services.alpaca_equity_quotes.get_alpaca_equity_quote",
                AsyncMock(return_value=None),
            ):
                result = await paper_trade_closer.close_due_paper_trades(db)
            assert result["closed"] == 1, result
            row = await db.paper_trades.find_one(
                {"seed_tag": tag}, {"_id": 0},
            )
            assert row["status"] == "closed"
            assert row["exit_price"] == 271.06
            # Short on rising stock → loss
            assert row["pnl_usd"] < 0
            assert row["outcome"] == "loss"
            assert row["auto_closed"] is True
        finally:
            await db.paper_trades.delete_many({"seed_tag": tag})

    asyncio.run(_run())


def test_close_due_ignores_manual_ui_ticks(monkeypatch):
    """Schema B rows (no `status` field — manual buy/sell ticks)
    must not be touched. The closer's $match is `status: 'open'`,
    so they're naturally excluded — but the test locks that
    contract in case anyone refactors."""
    from services import paper_trade_closer

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "1")

    async def _run():
        db = _mongo_db()
        tag = f"manual-{uuid.uuid4().hex[:6]}"
        try:
            # Schema B — no `status` field. Inserted by the manual
            # buy/sell flow. opened_at is old, but the closer
            # should ignore it.
            await db.paper_trades.insert_one({
                "user_id": "test-user",
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 5.0,
                "price": 200.0,
                "total": 1000.0,
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),
                "opened_at": datetime.now(timezone.utc) - timedelta(hours=48),
                "seed_tag": tag,
            })
            with patch.object(
                paper_trade_closer, "_fetch_price",
                new=AsyncMock(return_value=210.0),
            ):
                result = await paper_trade_closer.close_due_paper_trades(db)
            assert result["closed"] == 0
            row = await db.paper_trades.find_one(
                {"seed_tag": tag}, {"_id": 0},
            )
            # No status was added, no exit_price, no auto_closed
            assert row.get("status") is None
            assert "exit_price" not in row
        finally:
            await db.paper_trades.delete_many({"seed_tag": tag})

    asyncio.run(_run())


def test_close_due_quote_failure_keeps_trade_open(monkeypatch):
    """Quote provider returns None → trade stays open, retry next tick."""
    from services import paper_trade_closer

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "1")

    async def _run():
        db = _mongo_db()
        tag = f"quote-fail-{uuid.uuid4().hex[:6]}"
        try:
            await db.paper_trades.insert_one(
                _open_trade("ZZZZZ", "up", 100.0, 10.0,
                            hours_old=48.0, seed_tag=tag),
            )
            with patch.object(
                paper_trade_closer, "_fetch_price",
                new=AsyncMock(return_value=None),
            ):
                result = await paper_trade_closer.close_due_paper_trades(db)
            assert result["closed"] == 0
            assert result["holds"] == 1
            row = await db.paper_trades.find_one({"seed_tag": tag})
            assert row["status"] == "open"
        finally:
            await db.paper_trades.delete_many({"seed_tag": tag})

    asyncio.run(_run())


def test_close_due_disabled_short_circuits(monkeypatch):
    """Kill switch halts the entire run, no DB read."""
    from services import paper_trade_closer

    monkeypatch.setenv("PAPER_TRADE_CLOSER_DISABLED", "true")

    async def _run():
        result = await paper_trade_closer.close_due_paper_trades(_mongo_db())
        assert result.get("disabled") is True
        assert result["closed"] == 0

    asyncio.run(_run())


def test_self_test_stuck_paper_trades_pass_on_clean_db(monkeypatch):
    """No AI-driven trades older than 2× hold window → PASS."""
    from services.self_test_service import _check_stuck_paper_trades

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "24")

    async def _run():
        db = _mongo_db()
        # Make sure no leftover stuck trades
        await db.paper_trades.delete_many({
            "status": "open",
            "opened_at": {"$lt": datetime.now(timezone.utc) - timedelta(hours=48)},
        })
        result = await _check_stuck_paper_trades(db)
        assert result["status"] == "PASS", result

    asyncio.run(_run())


def test_self_test_stuck_paper_trades_fail_when_closer_broken(monkeypatch):
    """Stuck trade older than 2× hold → FAIL with count in error msg."""
    from services.self_test_service import _check_stuck_paper_trades

    monkeypatch.setenv("PAPER_TRADE_HOLD_HOURS", "24")

    async def _run():
        db = _mongo_db()
        tag = f"stuck-self-{uuid.uuid4().hex[:6]}"
        try:
            await db.paper_trades.insert_one(
                _open_trade("AAPL", "down", 200.0, 10.0,
                            hours_old=72.0, seed_tag=tag),
            )
            result = await _check_stuck_paper_trades(db)
            assert result["status"] == "FAIL"
            assert "open longer" in result["error"]
        finally:
            await db.paper_trades.delete_many({"seed_tag": tag})

    asyncio.run(_run())
