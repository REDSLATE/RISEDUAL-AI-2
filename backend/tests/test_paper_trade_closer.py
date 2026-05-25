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


# ── 2026-02-23 regression pins ──────────────────────────────────
# Pre-2026-02-23: outcome was computed from ``pnl_usd``, which is
# always 0 when shares=0 (the observation_fill case). Every
# observation rung was being labelled "flat" regardless of the
# brain's directional accuracy. These tests pin the pct-based
# outcome rule so the Sovereign learning signal stays coherent
# across all receipt types.


def test_compute_close_observation_long_winner_zero_shares():
    """shares=0 (observation_fill), long, +48% price move →
    outcome MUST be "win" (was "flat" pre-fix)."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, pnl_pct, outcome = _compute_close("up", 145.50, 215.33, 0.0)
    assert pnl_usd == 0.0
    assert round(pnl_pct, 4) == 0.4799
    assert outcome == "win", (
        "Observation rung with +48% pct must be 'win'. Pre-fix this "
        "was 'flat' because outcome was derived from $ PnL (always "
        "$0 when shares=0), silently killing the learning signal."
    )


def test_compute_close_observation_long_loser_zero_shares():
    """shares=0, long, -3% price move → outcome MUST be 'loss'."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, pnl_pct, outcome = _compute_close("up", 100.0, 97.0, 0.0)
    assert pnl_usd == 0.0
    assert pnl_pct == -0.03
    assert outcome == "loss"


def test_compute_close_observation_short_winner_zero_shares():
    """shares=0, short, price falls -10% → outcome MUST be 'win'."""
    from services.paper_trade_closer import _compute_close
    pnl_usd, pnl_pct, outcome = _compute_close("down", 100.0, 90.0, 0.0)
    assert pnl_usd == 0.0
    assert pnl_pct == 0.1
    assert outcome == "win"


def test_compute_close_observation_within_threshold_is_flat():
    """±0.5% threshold must hold — small moves both directions
    grade as 'flat' so noise doesn't dominate the learning tape."""
    from services.paper_trade_closer import _compute_close
    # +0.3% — under the +0.5% win threshold
    _, _, outcome_pos = _compute_close("up", 100.0, 100.3, 0.0)
    assert outcome_pos == "flat"
    # -0.3% — under the -0.5% loss threshold
    _, _, outcome_neg = _compute_close("up", 100.0, 99.7, 0.0)
    assert outcome_neg == "flat"
    # Exactly +0.5% — boundary is exclusive (strict >), so "flat".
    _, _, outcome_boundary = _compute_close("up", 100.0, 100.5, 0.0)
    assert outcome_boundary == "flat"
    # +0.6% crosses the boundary → "win"
    _, _, outcome_just_over = _compute_close("up", 100.0, 100.6, 0.0)
    assert outcome_just_over == "win"


def test_compute_close_observation_threshold_matches_backfill_pairer():
    """The observation threshold MUST match
    ``backfill_outcome_pairer._WIN_THRESHOLD`` so the learning
    signal is consistent across receipt types — real fills,
    Alpaca backfill pairs, and observation rungs all grade on
    the same scale. If these drift, MC's outcome stream will
    have a hidden per-source-type bias."""
    from services.paper_trade_closer import _OBSERVATION_PCT_THRESHOLD
    from services.backfill_outcome_pairer import _WIN_THRESHOLD
    assert _OBSERVATION_PCT_THRESHOLD == _WIN_THRESHOLD


def test_compute_close_real_trade_outcome_still_from_pct_and_aligned():
    """For real Kelly-sized trades (shares > 0), $ PnL and pct
    PnL agree in sign — so the pct-based rule produces the same
    win/loss as the old $-based rule. This pins that property so
    the fix didn't quietly flip real-trade labelling."""
    from services.paper_trade_closer import _compute_close
    # +10% gain on $1000 → +$100 → "win" both ways.
    _, _, out_long = _compute_close("up", 100.0, 110.0, 10.0)
    assert out_long == "win"
    # -10% on a short → loss both ways.
    _, _, out_short = _compute_close("down", 100.0, 110.0, 10.0)
    assert out_short == "loss"


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
