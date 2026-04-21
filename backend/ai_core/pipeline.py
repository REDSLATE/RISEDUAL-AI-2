"""run_full_pipeline — the canonical signal → execute → learn orchestrator.

This is the single entry point downstream services (signal-bot dispatcher,
future live-trade endpoints, backtester) should use. Centralising here
means the status-gating, SL/TP propagation, and LearningEngine sink are
consistent across every code path.

Two flavours:
  * `run_full_pipeline_live(...)` — real execution via ExecutionClient.
    The outcome is NOT simulated; it's marked pending and the delayed
    `prediction_labeler` will produce ground truth on its own schedule.
    Trade is still logged (pending) so the audit trail is complete.
  * `run_full_pipeline_backtest(...)` — deterministic TP/SL simulation
    against a bar array. Used by the backtester. Produces resolved
    win/loss/pending results immediately.

Both flavours use the same status schema so downstream widgets can mix
live + backtest data on the same charts.
"""
from __future__ import annotations

import logging
from typing import Optional

from .execution import ExecutionClient
from .learning_engine import LearningEngine
from .models import Signal, Trade, TradeResult

logger = logging.getLogger(__name__)


async def run_full_pipeline_live(
    signal: Signal,
    size: float,
    user_id: str,
    db,
    mode: str = "paper",
    conviction_data: Optional[dict] = None,
) -> dict:
    """Live path: execute → log pending outcome → wait for labeler.

    Returns a uniform response dict containing execution status, a
    placeholder outcome_status="pending", and the conviction block
    (when supplied) so the UI can render everything in one pass.
    """
    trade = Trade.from_signal(signal, size=size, user_id=user_id)
    client = ExecutionClient(mode=mode, user_id=user_id)
    execution = await client.execute_trade(trade, signal=signal)

    if execution.status != "filled":
        return {
            "status": "rejected",
            "execution_status": execution.status,
            "reason": execution.reason or "execution_failed",
            "trade_id": execution.trade_id or None,
        }

    # Live path: no future candles → pending outcome. The prediction
    # labeler (running on its own cron) produces the real verdict
    # later; we just log that the trade is open.
    pending_result = TradeResult(
        pnl=0.0,
        exit_price=execution.filled_price,
        win=None,
        status="pending",
        r_multiple=0.0,
    )
    le = LearningEngine(db)
    await le.log_trade(trade, pending_result, signal=signal)

    response = {
        "status": "approved",
        "execution_status": execution.status,
        "outcome_status": pending_result.status,
        "trade_id": execution.trade_id,
        "pnl": 0.0,
        "r_multiple": 0.0,
        "filled_price": execution.filled_price,
    }
    if conviction_data is not None:
        response["conviction"] = conviction_data
    return response


def run_full_pipeline_backtest(
    signal: Signal,
    size: float,
    bars: list[dict],
    start_index: int,
    lookahead: int = 20,
) -> dict:
    """Backtest path: simulate → return resolved/pending outcome.

    Synchronous — no DB, no I/O. The caller decides whether to persist
    results via `LearningEngine.log_trade`. Separating the persistence
    lets the backtester run on tens of thousands of candles without
    spamming Mongo writes; typically the caller aggregates and writes
    one summary doc at the end.
    """
    trade = Trade.from_signal(signal, size=size)
    client = ExecutionClient(mode="paper")  # irrelevant here, just for get_trade_result
    result = client.get_trade_result(trade, signal, bars, start_index, lookahead)
    return {
        "status": "approved",
        "outcome_status": result.status,
        "pnl": result.pnl,
        "exit_price": result.exit_price,
        "r_multiple": result.r_multiple,
        "win": result.win,
    }


# Backwards-compatible alias matching the drop-in spec. Dispatchers that
# want "just do the live thing" can call this without caring about the
# live/backtest split.
run_full_pipeline = run_full_pipeline_live
