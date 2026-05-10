"""Counterfactual P&L tracker — what *would* have happened.

Reads synthetic ADL receipts written by the Operator Trading Gate
when a trade was blocked. For each row, simulates the close-to-
close P&L using the same direction, notional, and symbol the gate
saw. Aggregates per-day and surfaces a single number plus a
per-symbol breakdown.

NEVER opens a real position. Pure observation. Read-only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


ADL_COLLECTION = "alpha_decision_log"
SUMMARY_COLLECTION = "counterfactual_pnl_daily"
DEFAULT_NOTIONAL = 1000.0  # used when intended-action lacks size


# ── Helpers ─────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _direction_from(extras: dict, decision: str) -> Optional[str]:
    """Resolve the intended LONG/SHORT/HOLD from the synthetic
    receipt. Returns ``None`` when the row is unscoreable."""
    raw = (extras or {}).get("intended_action") or decision or ""
    raw = raw.upper()
    # Receipts are tagged like "PAUSED_BY_OPERATOR:BUY".
    for token in ("BUY", "LONG"):
        if token in raw:
            return "LONG"
    for token in ("SELL", "SHORT"):
        if token in raw:
            return "SHORT"
    return None


def _notional_from(extras: dict) -> float:
    """Best-effort notional inference. Falls back to the default
    so non-sized lanes (e.g. options paper) still contribute a
    bounded simulated P&L."""
    if not extras:
        return DEFAULT_NOTIONAL
    notional = extras.get("notional_usd")
    if notional and notional > 0:
        return float(notional)
    qty = extras.get("qty") or extras.get("size")
    price = extras.get("price") or extras.get("entry_price")
    if qty and price:
        try:
            return abs(float(qty) * float(price))
        except (TypeError, ValueError):
            pass
    return DEFAULT_NOTIONAL


async def _close_to_close_move(symbol: str, since: datetime) -> Optional[float]:
    """Return the fractional move from the most recent close
    BEFORE ``since`` to the most recent close AFTER ``since``.

    Returns ``None`` when price data is unavailable.
    """
    try:
        from services.price_provider import get_daily_history
        history = await get_daily_history(symbol, outputsize="compact")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[counterfactual_pnl] history failed for %s: %s", symbol, exc)
        return None
    if not history or len(history) < 2:
        return None

    # ``history`` rows are dicts sorted descending (newest first).
    # We want one row >= since (newer) and one row < since (entry).
    rows = sorted(history, key=lambda r: r.get("date") or r.get("timestamp") or "")
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    for r in rows:
        d = r.get("date") or r.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(str(d).split("T")[0]).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        close = r.get("close") or r.get("c")
        if close is None:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if ts <= since:
            entry_price = close_f
        elif entry_price is not None:
            exit_price = close_f
            break
    if entry_price is None or exit_price is None or entry_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price


# ── Core scorer ─────────────────────────────────────────────────────


async def score_one(db, row: dict) -> dict:
    """Score a single synthetic receipt. Always returns a dict —
    fields ``scored=True`` and ``simulated_pnl_usd`` populated when
    we managed to fetch prices, otherwise ``scored=False``."""
    symbol = row.get("symbol") or ""
    extras = row.get("extras") or {}
    direction = _direction_from(extras, row.get("decision", ""))
    notional = _notional_from(extras)
    recorded_at = row.get("recorded_at") or _utc_now()
    if isinstance(recorded_at, str):
        try:
            recorded_at = datetime.fromisoformat(recorded_at)
        except ValueError:
            recorded_at = _utc_now()

    base: dict[str, Any] = {
        "adl_id": str(row.get("_id") or ""),
        "symbol": symbol,
        "lane": row.get("lane"),
        "direction": direction,
        "notional_usd": notional,
        "recorded_at": recorded_at,
        "scored": False,
        "simulated_pnl_usd": 0.0,
        "close_to_close_pct": None,
    }
    if not symbol or not direction or direction == "HOLD":
        return base

    move = await _close_to_close_move(symbol, recorded_at)
    if move is None:
        return base

    sign = 1.0 if direction == "LONG" else -1.0
    pnl = sign * move * notional

    base.update({
        "scored": True,
        "simulated_pnl_usd": round(pnl, 2),
        "close_to_close_pct": round(move * 100.0, 4),
    })
    return base


# ── Daily roll-up ───────────────────────────────────────────────────


async def score_window(
    db,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Score all synthetic receipts in ``[start, end)``. Returns a
    summary dict with totals + per-symbol breakdown. Idempotent.
    """
    cursor = db[ADL_COLLECTION].find({
        "extras.blocker": "operator_trading_gate",
        "extras.synthetic": True,
        "recorded_at": {"$gte": start, "$lt": end},
    }).limit(2000)

    rows: list[dict] = []
    async for r in cursor:
        rows.append(r)

    scored: list[dict] = []
    for r in rows:
        s = await score_one(db, r)
        scored.append(s)

    total_receipts = len(rows)
    scored_rows = [s for s in scored if s.get("scored")]
    total_pnl = sum(s.get("simulated_pnl_usd", 0.0) for s in scored_rows)

    by_symbol: dict[str, dict[str, Any]] = {}
    for s in scored_rows:
        sym = s.get("symbol") or "UNKNOWN"
        bucket = by_symbol.setdefault(sym, {
            "symbol": sym,
            "n_trades": 0,
            "n_long": 0,
            "n_short": 0,
            "simulated_pnl_usd": 0.0,
        })
        bucket["n_trades"] += 1
        if s.get("direction") == "LONG":
            bucket["n_long"] += 1
        elif s.get("direction") == "SHORT":
            bucket["n_short"] += 1
        bucket["simulated_pnl_usd"] = round(
            bucket["simulated_pnl_usd"] + s.get("simulated_pnl_usd", 0.0), 2,
        )

    summary = {
        "window_start": start,
        "window_end": end,
        "total_receipts": total_receipts,
        "scored_receipts": len(scored_rows),
        "unscored_receipts": total_receipts - len(scored_rows),
        "simulated_pnl_usd": round(total_pnl, 2),
        "by_symbol": sorted(
            by_symbol.values(),
            key=lambda b: b["simulated_pnl_usd"],
            reverse=True,
        ),
        "computed_at": _utc_now(),
    }
    return summary


async def score_yesterday(db) -> dict[str, Any]:
    """Convenience — score the calendar UTC day before today."""
    now = _utc_now()
    today_utc = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end = today_utc
    start = end - timedelta(days=1)
    summary = await score_window(db, start=start, end=end)
    summary["window_label"] = "yesterday"
    return summary


async def score_last_n_days(db, *, days: int = 7) -> dict[str, Any]:
    days = max(1, min(int(days or 7), 30))
    now = _utc_now()
    today_utc = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end = today_utc + timedelta(days=1)
    start = end - timedelta(days=days)
    summary = await score_window(db, start=start, end=end)
    summary["window_label"] = f"last_{days}_days"
    return summary


async def persist_daily_summary(db) -> dict[str, Any]:
    """Upsert yesterday's summary into ``counterfactual_pnl_daily``.
    Idempotent on ``window_start``. Operator can call repeatedly."""
    summary = await score_yesterday(db)
    try:
        await db[SUMMARY_COLLECTION].update_one(
            {"window_start": summary["window_start"]},
            {"$set": {
                "window_start": summary["window_start"],
                "window_end": summary["window_end"],
                "total_receipts": summary["total_receipts"],
                "scored_receipts": summary["scored_receipts"],
                "unscored_receipts": summary["unscored_receipts"],
                "simulated_pnl_usd": summary["simulated_pnl_usd"],
                "by_symbol": summary["by_symbol"],
                "computed_at": summary["computed_at"],
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[counterfactual_pnl] upsert failed: %s", exc)
    return summary
