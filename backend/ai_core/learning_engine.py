"""Mongo-persisted LearningEngine.

Replaces the in-memory `stats` dict from the drop-in spec — this is a
long-lived FastAPI process so per-process memory would evaporate on
every `supervisorctl restart backend`. Stats live in the
`learning_engine_stats` collection, and every trade result is persisted
to `learning_engine_trades` for audit/replay.

Pending trades are logged but not counted toward win-rate. Explicit
filters on `status in ("win", "loss")` when computing win-rate prevent
the false-loss poison pattern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .models import Trade, TradeResult, Signal

logger = logging.getLogger(__name__)

_TRADES = "learning_engine_trades"
_STATS = "learning_engine_stats"
_STATS_DOC_ID = "global"  # single-doc roll-up; sharding if/when needed


class LearningEngine:
    """Accumulator for trade outcomes. Thread/async safe via Mongo atomics.

    Usage:
        le = LearningEngine(db)
        await le.log_trade(trade, result)
        summary = await le.get_summary()

    The class is cheap to instantiate — no heavy state. Holding a
    singleton is optional; per-request construction is fine.
    """

    def __init__(self, db):
        self._db = db

    async def log_trade(self, trade: Trade, result: TradeResult,
                        signal: Signal | None = None) -> None:
        """Persist a trade outcome. Safe to call with pending trades —
        they're logged for audit but excluded from win-rate math.

        When `signal` is supplied (recommended), its SL/TP/strategy_id
        are stamped on the record. The prediction_labeler's
        resolve-pending path reads `stop_loss` to compute the final
        r_multiple when the trade closes, so omitting the signal forces
        a 2%-risk-floor fallback instead of the real risk the trader
        took.
        """
        record = {
            "asset": trade.asset,
            "direction": trade.direction,
            "size": trade.size,
            "entry": trade.entry,
            "user_id": trade.user_id,
            "pnl": result.pnl,
            "exit_price": result.exit_price,
            "r_multiple": result.r_multiple,
            "status": result.status,
            "win": result.win,
            "logged_at": datetime.now(timezone.utc).isoformat(),
            # trade.timestamp is when the SIGNAL fired; logged_at is
            # when we wrote the outcome. Keeping both lets us compute
            # hold-time later if we care.
            "signal_timestamp": trade.timestamp,
        }
        if signal is not None:
            # SL/TP are the fields prediction_labeler uses to compute
            # real r_multiple on resolve. Strategy_id preserves the
            # provenance for per-strategy stats.
            if signal.stop_loss:
                record["stop_loss"] = float(signal.stop_loss)
            if signal.take_profit:
                record["take_profit"] = float(signal.take_profit)
            if signal.strategy_id:
                record["strategy_id"] = signal.strategy_id
            if signal.confidence:
                record["confidence"] = float(signal.confidence)
        try:
            await self._db[_TRADES].insert_one(record)
        except Exception as e:
            logger.warning(f"[learning-engine] insert failed: {e}")
            return

        # Increment the per-status bucket atomically. `$inc` on a
        # non-existent field initialises to the increment value, so no
        # upsert setup is needed beyond the `upsert=True` flag.
        status = result.status
        inc = {f"counts.{status}": 1}
        if status in ("win", "loss"):
            inc["counts.total_resolved"] = 1
            # Track running sum of r_multiples for expectancy.
            # LearningEngine.get_summary() divides by total_resolved
            # to yield expectancy_r. Keeping it here avoids a full
            # collection scan on every summary call.
            inc["running.r_multiple_sum"] = result.r_multiple
            inc["running.pnl_sum"] = result.pnl
        try:
            await self._db[_STATS].update_one(
                {"_id": _STATS_DOC_ID},
                {"$inc": inc, "$set": {
                    "last_update": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"[learning-engine] stats inc failed: {e}")

    async def get_summary(self) -> dict:
        """Fetch the running summary. Returns sane defaults when empty.

        Includes an ``r_distribution`` block derived from the most
        recent 500 resolved trades via
        :func:`ai_core.risk_weighting.summarize_r_distribution`:

          * ``mean_r`` — drift signal. Sustained negative mean_r = the
            book is dominated by losses → regime-shift warning.
          * ``strong_r_frac`` — fraction with ``|R| ≥ 1.5``; comparable
            to the severity pipeline's ``severity_strong_frac``.

        This is the "log surface" the retrain orchestrator and admin
        tiles consume. It uses the ``r_multiple`` already stamped on
        each resolved trade by the prediction_tracker resolve path,
        so no extra math or schema change is needed.
        """
        doc = await self._db[_STATS].find_one({"_id": _STATS_DOC_ID}, {"_id": 0})
        if not doc:
            return {
                "wins": 0,
                "losses": 0,
                "pending": 0,
                "total_resolved": 0,
                "win_rate": None,
                "expectancy_r": None,
                "pnl_sum": 0.0,
                "r_distribution": {"mean_r": 0.0, "strong_r_frac": 0.0},
            }
        counts = doc.get("counts", {})
        running = doc.get("running", {})
        wins = int(counts.get("win", 0))
        losses = int(counts.get("loss", 0))
        pending = int(counts.get("pending", 0))
        total_resolved = int(counts.get("total_resolved", 0))
        win_rate = (wins / total_resolved) if total_resolved else None
        expectancy_r = (
            running.get("r_multiple_sum", 0.0) / total_resolved
            if total_resolved else None
        )
        return {
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "total_resolved": total_resolved,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "expectancy_r": round(expectancy_r, 4) if expectancy_r is not None else None,
            "pnl_sum": round(float(running.get("pnl_sum", 0.0)), 2),
            "last_update": doc.get("last_update"),
            "r_distribution": await self._r_distribution(),
        }

    async def _r_distribution(self, sample_size: int = 500) -> dict:
        """Compute mean_r + strong_r_frac over the most recent N
        resolved trades.

        Resolved-only (status in win/loss) so pending trades with
        ``r_multiple=None`` don't poison the aggregate. Cap at 500
        rows: enough signal for drift detection, bounded cost on
        the admin summary endpoint.

        Returns zero-stats on any failure — this is a reporting
        sidecar, not a load-bearing metric. Never raise.
        """
        from ai_core.risk_weighting import summarize_r_distribution
        try:
            cursor = (
                self._db[_TRADES]
                .find(
                    {"status": {"$in": ["win", "loss"]}, "r_multiple": {"$ne": None}},
                    {"_id": 0, "r_multiple": 1},
                )
                .sort("logged_at", -1)
                .limit(int(sample_size))
            )
            rows = await cursor.to_list(length=int(sample_size))
            r_values = [float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None]
        except Exception as e:
            logger.warning(f"[learning-engine] r_distribution read failed: {e}")
            return {"mean_r": 0.0, "strong_r_frac": 0.0}
        summary = summarize_r_distribution(r_values)
        # Round for a clean JSON surface — raw floats produce noisy
        # 17-digit tails that make diffs on the admin tile churn.
        return {
            "mean_r": round(float(summary["mean_r"]), 4),
            "strong_r_frac": round(float(summary["strong_r_frac"]), 4),
        }

    async def recent_trades(self, limit: int = 50,
                            status: Optional[str] = None) -> list[dict]:
        """Return the most recent N trade records (win/loss/pending filter optional)."""
        q: dict = {}
        if status:
            q["status"] = status
        cursor = self._db[_TRADES].find(q, {"_id": 0}).sort("logged_at", -1).limit(limit)
        return await cursor.to_list(length=limit)
