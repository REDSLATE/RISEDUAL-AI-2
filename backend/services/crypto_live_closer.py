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


def _max_hold_hours() -> float:
    """Time-based stale-exit threshold (2026-06).

    Returns the configured max-hold in hours; ``0`` disables the
    feature (default OFF — safe rollout). When > 0, any live row
    older than the threshold is force-closed: SL + TP cancelled, a
    market SELL placed for the position size, and the Mongo row
    stamped ``closed_reason="time_based_exit"``.

    Context: the PDT cliff (Jun 4, 2026 — $25k → $2.5k) reframes
    every overnight equity hold as a free option that the PDT clock
    can unilaterally close on. Crypto is exempt from PDT (24/7
    market), but the same operator discipline applies to small-
    account live BTC/ETH positions: don't let a position rot past
    its thesis window. Disabled by default so existing live
    positions keep their indefinite SL/TP-only behaviour until
    the operator flips ``CRYPTO_LIVE_MAX_HOLD_HOURS`` on.
    """
    raw = (os.environ.get("CRYPTO_LIVE_MAX_HOLD_HOURS") or "").strip()
    if not raw:
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # Bound to [0, 720h = 30d]; absurd values fall back to disabled.
    if v <= 0 or v > 720.0:
        return 0.0
    return v


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
        return

    # ── 2026-06 (Gap 2): enqueue live outcome to the Sovereign
    # sidecar's inbox so MC's recent_outcomes snapshot accumulates
    # live closes. Without this, MC's Scorecard sees
    # ``total_resolved=0`` for Alpha live crypto. Best-effort —
    # never blocks the close path.
    try:
        from services.sovereign_outcome_bridge import enqueue_outcome
        pnl_for_label = float(pnl_usd or 0.0)
        outcome_label = (
            "win" if pnl_for_label > 0 else (
                "loss" if pnl_for_label < 0 else "flat"
            )
        )
        await enqueue_outcome(
            db,
            brain="alpha",
            trade_id=str(
                row.get("trade_id") or row.get("_id") or ""
            ),
            symbol=str(row.get("symbol") or ""),
            direction=str(row.get("direction") or "LONG"),
            confidence=float(row.get("confidence") or 0.0),
            outcome_label=outcome_label,
            notional=notional,
            extras={
                "lane": "crypto",
                "receipt_type": "live",
                "close_reason": closed_reason,
                "pnl_pct": round(pnl_pct, 4),
            },
            sovereign_decision_id=row.get("sovereign_decision_id"),
            prediction_id=row.get("prediction_id"),
            source_signal=row.get("source_signal"),
        )
    except Exception as _bridge_exc:  # noqa: BLE001
        logger.debug(
            "[crypto-live-closer] outcome bridge enqueue failed: %s",
            _bridge_exc,
        )


def _row_age_hours(row: dict[str, Any], now: datetime) -> Optional[float]:
    """Return the row's age in hours since ``opened_at``, or None
    if ``opened_at`` is missing/unparseable. Tolerates naive and
    aware datetimes (Mongo stores both depending on writer).
    """
    raw = row.get("opened_at")
    if raw is None:
        return None
    opened: Optional[datetime] = None
    if isinstance(raw, datetime):
        opened = raw
    elif isinstance(raw, str):
        try:
            opened = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if opened is None:
        return None
    # Normalise both sides to naive UTC for subtraction safety.
    if opened.tzinfo is not None:
        opened = opened.astimezone(timezone.utc).replace(tzinfo=None)
    n = now
    if n.tzinfo is not None:
        n = n.astimezone(timezone.utc).replace(tzinfo=None)
    delta = (n - opened).total_seconds()
    return max(0.0, delta / 3600.0)


def _fetch_mark_price(client, pair: str) -> Optional[float]:
    """Best-effort last-traded price probe for the close-row stamp.

    Returns ``None`` if Kraken's quote path is unavailable — caller
    falls back to ``entry_price`` so the row still persists.
    """
    if not pair or client is None:
        return None
    try:
        # KrakenTradingService exposes ``get_ticker`` returning a
        # dict-like with ``last``/``bid``/``ask``. We tolerate any of
        # them since the cancel + market-sell will determine the
        # real fill anyway.
        quote = client.get_ticker(pair)
        if not quote:
            return None
        for key in ("last", "mid", "bid", "ask"):
            v = quote.get(key)
            if v:
                return float(v)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[crypto-live-closer] mark probe failed: %s", exc)
    return None


async def _force_close_stale_row(
    db: Any,
    client,
    row: dict[str, Any],
    *,
    age_hours: float,
    max_hold_hours: float,
) -> bool:
    """Time-based stale exit (2026-06).

    1. Cancels both SL and TP orphan legs (best-effort).
    2. Places a market SELL for the position size (LONG-only, per
       executor doctrine — short live entries are gated upstream).
    3. Marks the Mongo row closed with
       ``closed_reason="time_based_exit"`` and the executor's mark
       price (or entry price as fallback if quote probe fails).

    Returns ``True`` if the row was marked closed, ``False`` if
    skipped for safety (missing qty, sell raised, etc.). The
    market SELL is mandatory: skipping the broker leg would leave
    the operator with a paper-only ``closed`` row that doesn't
    match Kraken's actual position state.
    """
    symbol = str(row.get("symbol") or "")
    direction = str(row.get("direction") or "LONG").upper()
    if direction != "LONG":
        # The executor doctrine forbids live shorts, so this branch
        # should be unreachable — but if it ever fires we refuse to
        # mint a covering BUY autonomously. Skip the row.
        logger.warning(
            "[crypto-live-closer] refusing time-based exit for non-LONG "
            "row symbol=%s direction=%s", symbol, direction,
        )
        return False

    qty = float(row.get("size") or 0.0)
    if qty <= 0:
        logger.warning(
            "[crypto-live-closer] time-based exit skipped (missing qty) "
            "symbol=%s row_id=%s", symbol, row.get("_id"),
        )
        return False

    # 1. Cancel both legs first so the market SELL doesn't race them.
    sl_id = str(row.get("stop_loss_order_id") or "")
    tp_id = str(row.get("take_profit_order_id") or "")
    if sl_id:
        _cancel_orphan(client, sl_id, "SL (time-based)")
    if tp_id:
        _cancel_orphan(client, tp_id, "TP (time-based)")

    # 2. Place the market SELL. Lazy import to keep test surfaces
    #    isolated — same pattern the executor uses.
    from services.crypto_live_executor import kraken_pair
    pair = kraken_pair(symbol)
    if pair is None:
        logger.warning(
            "[crypto-live-closer] time-based exit skipped (pair unmapped) "
            "symbol=%s", symbol,
        )
        return False

    sell_resp = None
    try:
        sell_resp = client.place_order(
            symbol=pair, qty=qty, side="sell", order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[crypto-live-closer] CRITICAL — time-based market SELL raised "
            "symbol=%s qty=%.8f age=%.1fh: %s",
            symbol, qty, age_hours, exc,
        )
        return False
    if not sell_resp:
        logger.error(
            "[crypto-live-closer] CRITICAL — time-based market SELL returned "
            "empty symbol=%s qty=%.8f age=%.1fh — OPERATOR MUST RECONCILE",
            symbol, qty, age_hours,
        )
        return False

    sell_id = sell_resp.get("id") or ""
    logger.info(
        "[crypto-live-closer] time-based SELL filled symbol=%s qty=%.8f "
        "age=%.1fh (limit=%.1fh) order_id=%s",
        symbol, qty, age_hours, max_hold_hours, sell_id,
    )

    # 3. Stamp the row closed. Use the live mark price if we can grab
    #    one, else fall back to entry so pnl_pct = 0 (honest "didn't
    #    move enough to hit either bracket") instead of a phantom
    #    extreme.
    mark = _fetch_mark_price(client, pair)
    exit_price = float(
        mark if mark and mark > 0 else (row.get("entry_price") or 0.0)
    )
    await _close_row(
        db, row, closed_reason="time_based_exit", exit_price=exit_price,
    )
    return True



async def run_crypto_live_closer_pass(db: Any) -> dict[str, int]:
    """One tick of the live closer. Returns a small counter dict
    so the scheduler can log a summary line per run.

    Safe to call when:
      * live wire disabled (``RISEDUAL_CRYPTO_LIVE_EXEC`` unset) → no-op
      * Kraken keys missing → no-op
      * no open live rows → no-op
      * Kraken API briefly down → no-op (skips, retries next tick)
    """
    empty = {
        "scanned": 0, "sl_hit": 0, "tp_hit": 0,
        "skipped": 0, "errors": 0, "time_based_exit": 0,
    }
    if db is None:
        return dict(empty)
    if not _live_exec_enabled():
        return dict(empty)

    try:
        rows = await db.crypto_live_trades.find(
            {"status": "open"}, {"_id": 1, "trade_id": 1, "symbol": 1,
                                  "direction": 1, "entry_price": 1,
                                  "stop_loss_order_id": 1,
                                  "stop_loss_price": 1,
                                  "take_profit_order_id": 1,
                                  "take_profit_price": 1,
                                  "live_notional_usd": 1,
                                  "size_usd": 1,
                                  "size": 1,
                                  "opened_at": 1,
                                  "confidence": 1,
                                  "sovereign_decision_id": 1,
                                  "prediction_id": 1,
                                  "source_signal": 1},
        ).to_list(50)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-closer] open-rows read failed: %s", exc)
        return {**empty, "errors": 1}

    if not rows:
        return dict(empty)

    client = _kraken_client()
    if client is None:
        return {**empty, "scanned": len(rows), "skipped": len(rows)}

    open_ids = await _fetch_open_kraken_order_ids(client)
    if open_ids is None:
        # Don't act on a phantom empty set — skip and retry next tick.
        return {**empty, "scanned": len(rows),
                "skipped": len(rows), "errors": 1}

    # ── Time-based stale-exit sweep (2026-06) ─────────────────────
    # Operator-armed feature (default OFF). Any row older than
    # ``CRYPTO_LIVE_MAX_HOLD_HOURS`` gets force-flatted before the
    # SL/TP orphan classifier runs. Rows that pass through the
    # stale check still run the normal orphan-cancel path.
    max_hold_h = _max_hold_hours()
    now_utc = datetime.now(timezone.utc)
    remaining_rows: list[dict[str, Any]] = []
    time_based_exit = 0
    if max_hold_h > 0:
        for row in rows:
            age_h = _row_age_hours(row, now_utc)
            if age_h is None or age_h < max_hold_h:
                remaining_rows.append(row)
                continue
            ok = await _force_close_stale_row(
                db, client, row,
                age_hours=age_h, max_hold_hours=max_hold_h,
            )
            if ok:
                time_based_exit += 1
            else:
                # Force-close refused (missing qty, sell raised, …).
                # Leave the row for the normal orphan-classifier so
                # we don't double-act.
                remaining_rows.append(row)
    else:
        remaining_rows = list(rows)

    sl_hit = tp_hit = skipped = 0
    for row in remaining_rows:
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
        "time_based_exit": time_based_exit,
    }
    logger.info(
        "[crypto-live-closer] tick: %s", summary,
    )
    return summary


__all__ = [
    "classify_open_state",
    "run_crypto_live_closer_pass",
]
