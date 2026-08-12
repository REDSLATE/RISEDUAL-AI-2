"""Alpha Fill Writer — close the measurement loop.

Every Alpha trade needs real fill economics on its resolved outcome
row so the Edge Engine sees actual costs, not modeled ones. This
module owns two responsibilities:

1. **MFE/MAE tracking during the trade** — on every tick, walk open
   Alpha positions and update ``peak_price`` / ``trough_price`` on
   the ``equity_live_trades`` row.

2. **Resolve outcomes on close** — when an Alpha trade transitions
   from ``open`` to ``closed``, compute
   ``realized_r``, ``mfe_r``, ``mae_r``, and ``slippage_bps`` from
   the tracked extremes + fill prices, then update the matching
   ``alpha_outcomes`` doc (looked up by ``setup_id`` from the
   ``alpha_daytrader.setup_id`` extension field).

Idempotent — a re-run after ``outcome_resolved=True`` is a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _extract_setup_id(row: dict) -> Optional[str]:
    info = row.get("alpha_daytrader") or {}
    if isinstance(info, dict):
        sid = info.get("setup_id")
        if sid:
            return str(sid)
    return None


def _trigger_price_of(row: dict) -> Optional[float]:
    info = row.get("alpha_daytrader") or {}
    if isinstance(info, dict):
        p = info.get("confirmation_price")
        try:
            return float(p) if p is not None else None
        except (TypeError, ValueError):
            return None
    return None


async def _quote(symbol: str) -> Optional[float]:
    try:
        from services.market_data_pool import market_quote
    except Exception:  # noqa: BLE001
        return None
    try:
        q = await market_quote(symbol)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(q, dict):
        return None
    try:
        v = q.get("price") or q.get("last") or q.get("close")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def track_open_excursions(db: Any) -> dict:
    """Update peak_price / trough_price on every open Alpha trade.

    Kept small: single fresh quote per open symbol, bounded by
    however many Alpha positions are open (usually 1-2 at a time).
    Callers schedule this every 1 minute.
    """
    if db is None:
        return {"updated": 0, "seen": 0}
    updated = 0
    seen = 0
    try:
        cursor = db.equity_live_trades.find({
            "status": "open",
            "strategy_id": {"$regex": "^alpha_daytrader:"},
        })
    except Exception:  # noqa: BLE001
        return {"updated": 0, "seen": 0, "error": "query_failed"}

    async for row in cursor:
        seen += 1
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        price = await _quote(symbol)
        if price is None:
            continue
        peak = row.get("peak_price")
        trough = row.get("trough_price")
        new_peak = max(price, float(peak)) if isinstance(peak, (int, float)) else price
        new_trough = min(price, float(trough)) if isinstance(trough, (int, float)) else price
        if peak == new_peak and trough == new_trough:
            continue
        try:
            await db.equity_live_trades.update_one(
                {"_id": row["_id"]},
                {"$set": {
                    "peak_price": float(new_peak),
                    "trough_price": float(new_trough),
                    "excursion_updated_at": datetime.now(timezone.utc),
                }},
            )
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_fills] excursion update failed for %s: %s", symbol, exc)
    return {"updated": updated, "seen": seen}


def _resolve_metrics(row: dict) -> dict:
    """Return the metrics dict to stamp on the outcome row."""
    entry = float(row.get("entry_price") or 0.0)
    close = float(row.get("close_price") or 0.0)
    stop = float(row.get("stop_price") or 0.0)
    peak = row.get("peak_price")
    trough = row.get("trough_price")
    trigger = _trigger_price_of(row) or entry
    close_reason = str(row.get("close_reason") or "")
    size = float(row.get("size") or row.get("quantity") or 0.0)

    risk_per_share = max(0.0, entry - stop) if stop and entry > stop else None
    metrics: dict = {}

    if risk_per_share and risk_per_share > 0:
        metrics["realized_r"] = round((close - entry) / risk_per_share, 4)
        if isinstance(peak, (int, float)):
            metrics["mfe_r"] = round((float(peak) - entry) / risk_per_share, 4)
        if isinstance(trough, (int, float)):
            metrics["mae_r"] = round((float(trough) - entry) / risk_per_share, 4)
    else:
        # No stop distance — fall back to raw % return so the outcome
        # is still resolvable, just labelled honestly.
        if entry > 0:
            metrics["realized_r"] = round((close - entry) / entry, 4)
        metrics["risk_unknown"] = True

    if trigger and trigger > 0 and entry > 0:
        metrics["entry_slippage_bps"] = round((entry - trigger) / trigger * 10_000.0, 2)

    # Exit slippage: measured against the *decision* reference the exit
    # monitor used. For stop hits → stop_price; for target hits →
    # target_price (pulled from the setup); otherwise unknown (e.g.
    # time-based exits have no reference price).
    exit_ref: Optional[float] = None
    if close_reason.startswith("stop") and stop > 0:
        exit_ref = stop
    else:
        info = row.get("alpha_daytrader") or {}
        target = info.get("target_price") if isinstance(info, dict) else None
        try:
            t = float(target) if target is not None else None
        except (TypeError, ValueError):
            t = None
        if t and close_reason.startswith(("target", "profit")):
            exit_ref = t
    if exit_ref and exit_ref > 0 and close > 0:
        metrics["exit_slippage_bps"] = round((close - exit_ref) / exit_ref * 10_000.0, 2)

    # Dollar P&L when we have position size.
    if size > 0 and entry > 0 and close > 0:
        metrics["realized_pnl_usd"] = round((close - entry) * size, 2)
        metrics["gross_notional_usd"] = round(entry * size, 2)

    metrics["entry_fill_price"] = entry
    metrics["exit_fill_price"] = close
    metrics["close_reason"] = close_reason or None
    metrics["resolved_at"] = datetime.now(timezone.utc)
    return metrics


async def resolve_closed_outcomes(db: Any) -> dict:
    """For every recently-closed Alpha trade whose outcome hasn't been
    resolved yet, compute the metrics and update ``alpha_outcomes``."""
    if db is None:
        return {"resolved": 0, "seen": 0}
    resolved = 0
    seen = 0
    try:
        cursor = db.equity_live_trades.find({
            "status": "closed",
            "strategy_id": {"$regex": "^alpha_daytrader:"},
            "outcome_resolved": {"$ne": True},
        })
    except Exception:  # noqa: BLE001
        return {"resolved": 0, "seen": 0, "error": "query_failed"}

    async for row in cursor:
        seen += 1
        setup_id = _extract_setup_id(row)
        if not setup_id:
            # No setup linkage — mark resolved so we stop re-processing.
            try:
                await db.equity_live_trades.update_one(
                    {"_id": row["_id"]}, {"$set": {"outcome_resolved": True}},
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        metrics = _resolve_metrics(row)
        try:
            await db.alpha_outcomes.update_one(
                {"setup_id": setup_id},
                {"$set": metrics},
                upsert=False,
            )
            await db.equity_live_trades.update_one(
                {"_id": row["_id"]}, {"$set": {"outcome_resolved": True}},
            )
            resolved += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_fills] outcome update failed for %s: %s", setup_id, exc)

        # Emit a hot-store event so the setup timeline reflects the resolution.
        try:
            from services import alpha_hot_store
            alpha_hot_store.record_event(
                setup_id, "outcome_resolved",
                stage="resolved", symbol=str(row.get("symbol") or ""),
                payload={k: v for k, v in metrics.items()
                          if not isinstance(v, datetime)},
            )
        except Exception:  # noqa: BLE001
            pass

    return {"resolved": resolved, "seen": seen}


__all__ = ["track_open_excursions", "resolve_closed_outcomes"]
