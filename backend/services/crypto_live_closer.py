"""Crypto live position closer + orphan-leg auto-canceller.

Doctrine
--------
Every BTC/ETH live entry is a 3-leg bracket: BUY (market) + SL
(stop-loss SELL) + TP (limit SELL). Kraken does NOT auto-link
the SL and TP as an OCO pair on the AddOrder endpoint, so when
ONE leg fires the OTHER stays resting on the book. If the
operator doesn't cancel it manually, a price revisit on the
other side flips the account net SHORT — exactly the failure
mode we're paid to prevent.

This closer's only job is to make that orphan-cancel automatic
and to keep ``crypto_live_trades`` row state consistent with
the actual broker state.

Run cadence
-----------
Scheduled every 5 minutes via :mod:`services.scheduling.jobs`.
Same cadence as the equity ``alpaca_position_closer`` job;
crypto fills are fast enough that a 5-min lag between leg-fire
and orphan-cancel is acceptable risk (max exposure window is a
few minutes of unprotected open orders).

Algorithm per tick
------------------
1. Read every ``crypto_live_trades`` row with ``status="open"``.
2. Query Kraken's ``OpenOrders`` once for the whole batch.
3. For each open row, check which of ``stop_loss_order_id`` /
   ``take_profit_order_id`` is still resting on the book:

      Both present  → position still live, no action.
      SL gone, TP resting → SL fired. Cancel TP. Mark row closed
          with ``closed_reason="sl_hit"`` and exit price ≈ SL.
      TP gone, SL resting → TP fired. Cancel SL. Mark row closed
          with ``closed_reason="tp_hit"`` and exit price ≈ TP.
      Both gone → unusual (operator manual close, or race).
          Mark row closed with ``closed_reason="manual_or_race"``
          and exit price ≈ entry (placeholder — operator can
          reconcile via Kraken UI).

4. Read-only on Kraken from this loop except for the cancel-orphan
   leg. We never place new orders here.

What this loop does NOT do
--------------------------
* **It does NOT place an exit order itself.** If both legs are
  somehow gone but the position is still open on Kraken (highly
  unusual), this closer will mark the Mongo row closed but the
  Kraken position stays. The operator must reconcile via the
  Kraken UI. Building a "force-close orphaned position" path here
  would mean placing market SELLs autonomously, which is exactly
  the authority we want to keep manual until we have more live
  evidence.

* **It does NOT touch the paper closer.** ``crypto_closer.py``
  reads ``crypto_paper_trades`` exclusively. The two collections
  stay hard-separated.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _live_exec_enabled() -> bool:
    """Master switch mirror — closer only runs when the wire is
    armed. Prevents the loop from chattering Kraken's OpenOrders
    endpoint on quiet pods."""
    return os.environ.get("RISEDUAL_CRYPTO_LIVE_EXEC", "").strip() in (
        "1", "true", "True", "yes", "on",
    )


def _kraken_client():
    """Lazy Kraken client. Returns None on missing keys / import
    failure. Mirrors :func:`crypto_live_executor._kraken_client`
    but kept local so the executor module doesn't have to expose
    private helpers."""
    api_key = os.environ.get("KRAKEN_API_KEY", "").strip()
    api_secret = os.environ.get("KRAKEN_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None
    try:
        from services.broker_service import KrakenTradingService
        return KrakenTradingService(api_key=api_key, api_secret=api_secret)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-closer] kraken client init failed: %s", exc)
        return None


def classify_open_state(
    sl_order_id: str, tp_order_id: str, open_order_ids: set[str],
) -> str:
    """Pure-function classifier for unit-test grip.

    Returns one of:
      ``"both_resting"`` — both legs still on the book; row stays open.
      ``"sl_hit"`` — SL gone, TP resting; SL fired.
      ``"tp_hit"`` — TP gone, SL resting; TP fired.
      ``"both_gone"`` — both gone; operator manual close or race.
      ``"missing_ids"`` — row has empty leg IDs (e.g. SL placement
          failed at entry time); cannot classify safely — caller
          should skip and let the operator handle the row manually.
    """
    if not sl_order_id and not tp_order_id:
        return "missing_ids"
    sl_resting = bool(sl_order_id) and sl_order_id in open_order_ids
    tp_resting = bool(tp_order_id) and tp_order_id in open_order_ids
    # If one ID is empty (SL or TP placement failed at entry), treat
    # the missing-ID side as "unmanaged" — the closer has no orphan
    # to act on for that leg. Combine with the present-side state
    # to derive what the closer should do.
    sl_resting_or_unmanaged = sl_resting or not sl_order_id
    tp_resting_or_unmanaged = tp_resting or not tp_order_id

    if sl_resting_or_unmanaged and tp_resting_or_unmanaged:
        return "both_resting"
    if not sl_resting_or_unmanaged and tp_resting_or_unmanaged:
        return "sl_hit"
    if sl_resting_or_unmanaged and not tp_resting_or_unmanaged:
        return "tp_hit"
    return "both_gone"


async def _fetch_open_kraken_order_ids(client) -> Optional[set[str]]:
    """Pull the active order-ID set from Kraken. Returns None on
    error so the caller can skip the tick safely (better to do
    nothing than to mass-cancel based on a phantom empty set)."""
    try:
        orders = client.get_orders(status="all", limit=200)
        if orders is None:
            return None
        return {o.get("id") for o in orders if o.get("id")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-closer] get_orders failed: %s", exc)
        return None


def _cancel_orphan(client, order_id: str, label: str) -> bool:
    """Cancel a single orphan leg by ID. Returns True on success
    (cancel ack OR already-gone). Defensive: never raises."""
    if not order_id:
        return True
    try:
        ok = bool(client.cancel_order(order_id))
        if ok:
            logger.info(
                "[crypto-live-closer] orphan %s leg cancelled order_id=%s",
                label, order_id,
            )
        else:
            logger.warning(
                "[crypto-live-closer] orphan %s leg cancel returned False "
                "(may already be filled/cancelled) order_id=%s",
                label, order_id,
            )
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[crypto-live-closer] orphan %s leg cancel raised: %s order_id=%s",
            label, exc, order_id,
        )
        return False


async def _close_row(
    db: Any,
    row: dict[str, Any],
    *,
    closed_reason: str,
    exit_price: float,
) -> None:
    """Patch the live row to status=closed with the inferred exit
    state. Idempotent — repeated calls with the same payload are
    safe (Mongo ``$set`` over identical values is a no-op)."""
    decision_id = row.get("_id") or row.get("trade_id")
    entry = float(row.get("entry_price") or 0.0)
    pnl_pct = ((exit_price - entry) / entry) * 100.0 if entry > 0 else 0.0
    notional = float(row.get("live_notional_usd") or row.get("size_usd") or 0.0)
    pnl_usd = round(notional * (pnl_pct / 100.0), 4)
    update = {
        "$set": {
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "closed_reason": closed_reason,
            "exit_price": exit_price,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": pnl_usd,
        },
    }
    try:
        if "_id" in row:
            await db.crypto_live_trades.update_one({"_id": row["_id"]}, update)
        elif row.get("trade_id"):
            await db.crypto_live_trades.update_one(
                {"trade_id": row["trade_id"]}, update,
            )
        else:
            logger.warning(
                "[crypto-live-closer] row missing _id and trade_id, "
                "cannot persist close: %s", row.get("symbol"),
            )
            return
        logger.info(
            "[crypto-live-closer] closed %s %s @ $%.2f reason=%s "
            "pnl=%+.2f%% ($%+.2f) decision_id=%s",
            row.get("symbol"), row.get("direction"),
            exit_price, closed_reason, pnl_pct, pnl_usd, decision_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[crypto-live-closer] close-row update failed: %s row=%s",
            exc, row.get("symbol"),
        )


async def run_crypto_live_closer_pass(db: Any) -> dict[str, int]:
    """One tick of the live closer. Returns a small counter dict
    so the scheduler can log a summary line per run.

    Safe to call when:
      * live wire disabled (``RISEDUAL_CRYPTO_LIVE_EXEC`` unset) → no-op
      * Kraken keys missing → no-op
      * no open live rows → no-op
      * Kraken API briefly down → no-op (skips, retries next tick)
    """
    if db is None:
        return {"scanned": 0, "sl_hit": 0, "tp_hit": 0, "skipped": 0, "errors": 0}
    if not _live_exec_enabled():
        return {"scanned": 0, "sl_hit": 0, "tp_hit": 0, "skipped": 0, "errors": 0}

    try:
        rows = await db.crypto_live_trades.find(
            {"status": "open"}, {"_id": 1, "trade_id": 1, "symbol": 1,
                                  "direction": 1, "entry_price": 1,
                                  "stop_loss_order_id": 1,
                                  "stop_loss_price": 1,
                                  "take_profit_order_id": 1,
                                  "take_profit_price": 1,
                                  "live_notional_usd": 1,
                                  "size_usd": 1},
        ).to_list(50)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-closer] open-rows read failed: %s", exc)
        return {"scanned": 0, "sl_hit": 0, "tp_hit": 0, "skipped": 0, "errors": 1}

    if not rows:
        return {"scanned": 0, "sl_hit": 0, "tp_hit": 0, "skipped": 0, "errors": 0}

    client = _kraken_client()
    if client is None:
        return {"scanned": len(rows), "sl_hit": 0, "tp_hit": 0,
                "skipped": len(rows), "errors": 0}

    open_ids = await _fetch_open_kraken_order_ids(client)
    if open_ids is None:
        # Don't act on a phantom empty set — skip and retry next tick.
        return {"scanned": len(rows), "sl_hit": 0, "tp_hit": 0,
                "skipped": len(rows), "errors": 1}

    sl_hit = tp_hit = skipped = 0
    for row in rows:
        sl_id = str(row.get("stop_loss_order_id") or "")
        tp_id = str(row.get("take_profit_order_id") or "")
        state = classify_open_state(sl_id, tp_id, open_ids)
        if state == "both_resting":
            skipped += 1
            continue
        if state == "missing_ids":
            # Entry-time SL/TP placement BOTH failed — operator's
            # call whether to keep or close. Closer skips.
            skipped += 1
            continue
        if state == "sl_hit":
            _cancel_orphan(client, tp_id, "TP")
            sl_price = float(row.get("stop_loss_price") or 0.0)
            await _close_row(
                db, row, closed_reason="sl_hit", exit_price=sl_price,
            )
            sl_hit += 1
            continue
        if state == "tp_hit":
            _cancel_orphan(client, sl_id, "SL")
            tp_price = float(row.get("take_profit_price") or 0.0)
            await _close_row(
                db, row, closed_reason="tp_hit", exit_price=tp_price,
            )
            tp_hit += 1
            continue
        # both_gone
        await _close_row(
            db, row, closed_reason="manual_or_race",
            exit_price=float(row.get("entry_price") or 0.0),
        )
        skipped += 1

    summary = {
        "scanned": len(rows), "sl_hit": sl_hit, "tp_hit": tp_hit,
        "skipped": skipped, "errors": 0,
    }
    logger.info(
        "[crypto-live-closer] tick: %s", summary,
    )
    return summary


__all__ = [
    "classify_open_state",
    "run_crypto_live_closer_pass",
]
