"""Alpha Break-Even Protection.

Once a live Alpha trade reaches +1R (measured against the frozen
stop/entry distance from setup detection), arm break-even protection:
move the stop to ``entry_price + buffer`` so a normal noise pullback
does not turn a good winner into a loser.

Policy
------
* Stops may **tighten**, never widen.
* At +2R, existing profit / trailing logic takes control (this module
  becomes a no-op once ``breakeven_armed=true`` and price >= +2R).
* Buffer is configurable via ``ALPHA_BREAKEVEN_BUFFER_BPS`` (default 5bps).
* Break-even only fires for trades tagged with ``strategy_id`` starting
  ``alpha_daytrader:`` — the existing exit monitor still owns everything
  else.

The tick reads open Alpha trades from ``equity_live_trades``, fetches
a fresh quote, computes the current R multiple, and updates the
``stop_price`` field on the row when the +1R threshold is crossed.

Actual broker-side stop orders are out of scope for V1 — we adjust
the *tracked* stop and let ``day_trade_exit_monitor`` close the
position when price crosses it. This is consistent with how the
current exit path works and avoids two writers on the broker stop.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _buffer_bps() -> float:
    try:
        return float(os.environ.get("ALPHA_BREAKEVEN_BUFFER_BPS") or "5.0")
    except (TypeError, ValueError):
        return 5.0


def _r_multiple(current: float, entry: float, stop: float) -> Optional[float]:
    risk = entry - stop
    if risk <= 0 or entry <= 0 or current <= 0:
        return None
    return (current - entry) / risk


async def _fetch_quote_price(symbol: str) -> Optional[float]:
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
        return float(q.get("price") or q.get("last") or q.get("close") or 0.0) or None
    except (TypeError, ValueError):
        return None


async def evaluate_and_apply(db: Any) -> dict:
    """One tick. Returns a summary of changes."""
    if db is None:
        return {"skipped": True, "reason": "no_db"}
    updated: list[dict] = []
    buf_bps = _buffer_bps()
    try:
        cursor = db.equity_live_trades.find({
            "status": {"$in": ["open", "submitted"]},
            "strategy_id": {"$regex": "^alpha_daytrader:"},
        })
    except Exception:  # noqa: BLE001
        return {"skipped": True, "reason": "query_failed"}

    async for row in cursor:
        symbol = (row.get("symbol") or "").upper()
        entry = float(row.get("entry_price") or 0.0)
        stop = float(row.get("stop_price") or 0.0)
        if not symbol or entry <= 0 or stop <= 0 or stop >= entry:
            continue
        # Already at break-even or better? Skip — stops only tighten.
        if row.get("breakeven_armed") and stop >= entry:
            continue
        current = await _fetch_quote_price(symbol)
        if current is None:
            continue
        r = _r_multiple(current, entry, stop)
        if r is None or r < 1.0:
            continue
        # Arm break-even. Buffer above entry (bps → fraction).
        new_stop = entry * (1.0 + buf_bps / 10_000.0)
        if new_stop <= stop:
            continue  # can only tighten
        try:
            await db.equity_live_trades.update_one(
                {"_id": row["_id"]},
                {"$set": {
                    "stop_price": new_stop,
                    "breakeven_armed": True,
                    "breakeven_at": datetime.now(timezone.utc),
                    "breakeven_r": r,
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_breakeven] update failed for %s: %s", symbol, exc)
            continue
        updated.append({
            "symbol": symbol,
            "trade_id": row.get("trade_id"),
            "prev_stop": stop, "new_stop": round(new_stop, 4),
            "entry": entry, "current": current, "r": round(r, 3),
        })
        # Hot-store event so this shows up in the setup timeline.
        try:
            from services import alpha_hot_store
            setup_id = ""
            info = row.get("alpha_daytrader") or {}
            if isinstance(info, dict):
                setup_id = str(info.get("setup_id") or "")
            if setup_id:
                alpha_hot_store.record_event(
                    setup_id, "breakeven_armed",
                    stage="post_entry", symbol=symbol,
                    payload={"prev_stop": stop, "new_stop": new_stop, "r": r},
                )
        except Exception:  # noqa: BLE001
            pass
    return {"updated": updated, "count": len(updated), "buffer_bps": buf_bps}


__all__ = ["evaluate_and_apply"]
