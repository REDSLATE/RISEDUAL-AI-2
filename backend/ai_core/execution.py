"""ExecutionClient — the single venue abstraction.

Two concrete routes:
  * `mode="paper"` → delegates to `paper_trading_service.execute_trade()`,
    which handles SL/TP + r_multiple persistence and returns
    `{status, price, ...}`. This is what the bot dispatcher and the
    `/api/paper/trade` route already use.
  * `mode="live"`  → routes through the user's connected broker
    (Alpaca for equities, Kraken for crypto — same pattern as
    `smart_order_service._execute_fill`). Reuses broker client
    plumbing that already exists; no new HTTP code here.

`get_trade_result` reuses the simulator when you pass a DataFrame of
future candles (backtest path) or accepts a pre-labeled result when the
outcome was produced by the delayed prediction_labeler (live path).
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

from .models import Signal, Trade, ExecutionResult, TradeResult
from .simulator import get_trade_result_from_bars

logger = logging.getLogger(__name__)


class ExecutionClient:
    """Paper/live trade router with a uniform status-aware return shape."""

    def __init__(self, mode: str = "paper", user_id: Optional[str] = None):
        if mode not in ("paper", "live"):
            raise ValueError(f"ExecutionClient mode must be paper|live, got {mode!r}")
        self.mode = mode
        self.user_id = user_id

    @staticmethod
    def _reject(size: float, reason: str, trade_id: str = "") -> ExecutionResult:
        """Canonical rejection-shape builder. Keeps the three early-exit
        branches from drifting from one another."""
        return ExecutionResult(
            trade_id=trade_id,
            filled_price=0.0,
            size=size,
            status="rejected",
            reason=reason,
        )

    async def _execute_paper(
        self,
        user_id: str,
        trade: Trade,
        signal: Optional[Signal],
        side: str,
    ) -> ExecutionResult:
        """Paper route — delegates to paper_trading_service.execute_trade
        (handles SL/TP + r_multiple persistence)."""
        from services.paper_trading_service import execute_trade as _pt_exec
        sl = signal.stop_loss if signal else None
        tp = signal.take_profit if signal else None
        raw = await _pt_exec(
            user_id, trade.asset, side, trade.size,
            stop_loss=sl, take_profit=tp,
        )
        return ExecutionResult(
            trade_id=f"paper_{trade.asset}_{trade.timestamp}",
            filled_price=float(raw.get("price") or trade.entry),
            size=float(raw.get("qty") or trade.size),
            status=raw.get("status", "rejected"),  # type: ignore[arg-type]
            reason=raw.get("error"),
        )

    async def _execute_live(
        self,
        user_id: str,
        trade: Trade,
        side: str,
    ) -> ExecutionResult:
        """Live route — places a market order through the user's connected
        broker (Alpaca for equities, Kraken for crypto). SL/TP bracket
        wrap-around is the caller's responsibility."""
        import asyncio
        try:
            from server import db as _db  # type: ignore
            from routes.broker import _get_or_refresh_client, _get_user_broker

            broker_conn = await _db.broker_connections.find_one(
                {"user_id": user_id}, {"_id": 0, "broker_id": 1},
            )
            if not broker_conn:
                return self._reject(trade.size, "no_broker_connected")

            conn = await _get_user_broker(user_id, broker_conn["broker_id"])
            client = await _get_or_refresh_client(user_id, broker_conn["broker_id"], conn)
            result = await asyncio.to_thread(
                client.place_order,
                symbol=trade.asset,
                qty=trade.size,
                side=side.lower(),
                order_type="market",
                time_in_force="day",
            )
            if not result:
                return self._reject(trade.size, "broker_rejected")

            return ExecutionResult(
                trade_id=str(result.get("id") or uuid4()),
                filled_price=float(result.get("filled_avg_price") or trade.entry),
                size=float(result.get("filled_qty") or trade.size),
                status="filled",
            )
        except Exception as e:
            logger.exception(f"[execution] live route failed: {e}")
            return self._reject(trade.size, f"live_exception:{type(e).__name__}")

    async def execute_trade(
        self,
        trade: Trade,
        signal: Optional[Signal] = None,
    ) -> ExecutionResult:
        """Route a trade to paper or live. Always returns `ExecutionResult`.

        When `signal` is provided we extract SL/TP from it and stamp
        them on the paper-trading record so the eventual exit computes
        `r_multiple`. For live trades SL/TP go via the bracket-order
        path in `smart_order_service._execute_fill` — here we only
        place the market order; bracket wrap-around is the caller's
        responsibility (existing pattern).
        """
        user_id = trade.user_id or self.user_id
        if not user_id:
            return self._reject(trade.size, "no_user_id")

        side = "BUY" if trade.direction == "LONG" else "SELL"

        if self.mode == "paper":
            return await self._execute_paper(user_id, trade, signal, side)
        return await self._execute_live(user_id, trade, side)

    # ------------------------------------------------------------------
    # Outcome lookup (simulator-backed backtest path)
    # ------------------------------------------------------------------
    def get_trade_result(
        self,
        trade: Trade,
        signal: Signal,
        bars: list[dict],
        start_index: int,
        lookahead: int = 20,
    ) -> TradeResult:
        """Simulate the trade against future bars. Pure, no I/O.

        For the live path, callers should NOT use this — the delayed
        `prediction_labeler` produces real-world outcomes. This method
        is purely the backtest seam.
        """
        return get_trade_result_from_bars(signal, bars, start_index, lookahead)
