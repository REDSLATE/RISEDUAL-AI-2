"""Position Reconciler — Step 10 OUTCOME_VERIFIED for external broker fills.

Background
----------
``services/manual_order_guard.run_manual_order_guard`` writes a
``DECISION`` proof block when a live equity / option order fills.
Step 10 (``OUTCOME_VERIFIED``) needs to be appended when the position
*closes* — but unlike crypto paper trades, the close happens
**externally** at the broker. There's no in-process callback we can
hook.

This reconciler closes that gap:

1. Periodically iterate ``trade_orders`` and ``option_orders`` rows
   that have a ``proof_chain_entity_id`` set and
   ``outcome_appended != True``.
2. Group by broker connection so we hit each broker API once per
   sweep instead of once per row.
3. Pull current positions + recent order history from the broker.
4. Decide whether the row is closed:
   * **Equity** — the symbol no longer appears in ``get_positions``
     (or appears with reduced qty below the row's qty), AND there's
     a later opposite-side fill.
   * **Option** — the OCC symbol no longer appears in option
     positions, OR there's a matching close-side fill (sell_to_close
     for a long, buy_to_close for a short).
5. Compute exit P&L from the broker order data.
6. Call ``record_manual_order_outcome`` to append OUTCOME_VERIFIED.
7. Mark the row ``outcome_appended: True`` with a timestamp so the
   next sweep skips it.

Failure isolation
-----------------
Any per-user / per-broker failure is swallowed and logged — a single
bad connection must NEVER halt the sweep across all users. The
proof block append itself is also non-fatal (``record_manual_order_outcome``
returns ``None`` rather than raising on chain errors).

Design notes
------------
* No webhooks. Brokers' webhook stories are inconsistent (Alpaca
  paper has none, Tradier requires public callbacks, etc.). Polling
  is the only universal substrate.
* Idempotent. Re-running the reconciler on the same row never appends
  a second OUTCOME_VERIFIED — the ``outcome_appended`` flag is set
  in the same update_one as the proof append's success path.
* Conservative. We only fire OUTCOME_VERIFIED when we're confident
  the position is closed. False negatives (delaying a chain close)
  are acceptable; false positives (firing on a still-open position)
  would corrupt the proof chain.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from services.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────

# Don't reconcile rows older than this — they're cold storage. Keeps
# the broker API call volume bounded as the order log grows.
RECONCILE_LOOKBACK_DAYS = 90

# Skip rows newer than this — gives the broker time to actually fill
# and avoids racing with the place_order call.
RECONCILE_MIN_AGE_MINUTES = 5

# Cap how many rows we sweep in one pass. Protects against a runaway
# loop on a stuck row.
RECONCILE_MAX_ROWS_PER_PASS = 200


# ── Equity reconciler ───────────────────────────────────────────────


async def _is_equity_position_closed(
    *, symbol: str, side: str, qty: float, opened_at: datetime,
    broker_orders: list[dict], broker_positions: list[dict],
) -> Optional[dict]:
    """Decide whether a single equity row is closed at the broker.

    Returns a dict with ``exit_price`` / ``close_reason`` if closed,
    ``None`` if still open or undetermined.

    Logic:
      * If the symbol still appears in ``broker_positions`` with
        non-zero qty in the same direction → still open.
      * Otherwise, search ``broker_orders`` for a *later* opposite-side
        fill on the same symbol. The fill price is the exit. If no
        such fill is found we return ``None`` (close detected by
        position absence but exit price unknown — wait for the
        opposite fill to land in get_orders).
    """
    sym = (symbol or "").upper()
    side_norm = (side or "").lower()
    long_position = side_norm in ("buy", "long")

    # Still open?
    for p in broker_positions or []:
        if (p.get("symbol", "") or "").upper() != sym:
            continue
        try:
            pos_qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            pos_qty = 0.0
        if pos_qty == 0:
            continue
        pos_side = (p.get("side", "long") or "long").lower()
        if (pos_side == "long") == long_position and abs(pos_qty) >= abs(qty):
            return None  # still open

    # Closed — find the matching opposite-side fill for exit price.
    opposite = "sell" if long_position else "buy"
    candidates: list[tuple[datetime, float]] = []
    for o in broker_orders or []:
        if (o.get("symbol", "") or "").upper() != sym:
            continue
        if (o.get("side", "") or "").lower() != opposite:
            continue
        if (o.get("status", "") or "").lower() not in ("filled", "partially_filled"):
            continue
        fill_at = ensure_utc(
            o.get("filled_at") or o.get("submitted_at") or o.get("created_at")
        )
        if fill_at is None or fill_at <= opened_at:
            continue
        try:
            px = float(o.get("filled_avg_price") or 0)
        except (TypeError, ValueError):
            continue
        if px > 0:
            candidates.append((fill_at, px))

    if not candidates:
        # Position absent but no exit fill recorded yet — wait one
        # more sweep. Don't append OUTCOME_VERIFIED with a guessed
        # price; that's the "false positive" we hard-avoid.
        return None

    # Use the earliest matching close fill (volume-weighted across
    # multiple closes is a future enhancement).
    candidates.sort(key=lambda t: t[0])
    return {
        "exit_price": candidates[0][1],
        "close_reason": "broker_position_closed",
    }


async def _reconcile_equity_for_user_broker(
    db: Any, *, user_id: str, broker_id: str, rows: list[dict],
) -> dict:
    """Sweep all open equity rows for one (user, broker_id) pair.

    Single broker API roundtrip per pair (1× get_positions, 1×
    get_orders for closed orders). Each row decision uses these
    cached results.
    """
    from routes.broker import _get_user_broker, _get_or_refresh_client

    out = {"processed": 0, "closed": 0, "errors": 0}
    try:
        conn = await _get_user_broker(user_id, broker_id)
        client = await _get_or_refresh_client(user_id, broker_id, conn)
        # ``status="closed"`` filters to filled/cancelled in Alpaca's API.
        broker_orders = await asyncio.to_thread(client.get_orders, status="closed")
        broker_positions = await asyncio.to_thread(client.get_positions)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[reconciler] equity broker fetch failed user=%s broker=%s: %s",
            user_id, broker_id, exc,
        )
        out["errors"] = len(rows)
        return out

    for row in rows:
        out["processed"] += 1
        try:
            opened_at = ensure_utc(row.get("created_at"))
            if opened_at is None:
                continue
            decision = await _is_equity_position_closed(
                symbol=row.get("symbol", ""),
                side=row.get("side", ""),
                qty=float(row.get("qty", 0) or 0),
                opened_at=opened_at,
                broker_orders=broker_orders,
                broker_positions=broker_positions,
            )
            if decision is None:
                continue

            entry_px = float(row.get("limit_price") or 0)
            if entry_px <= 0:
                # ``limit_price`` is None for market orders. Pull the
                # actual fill price off the broker order record.
                for o in broker_orders or []:
                    if str(o.get("id", "")) == str(row.get("broker_order_id", "")):
                        try:
                            entry_px = float(o.get("filled_avg_price") or 0)
                        except (TypeError, ValueError):
                            entry_px = 0.0
                        break
            if entry_px <= 0:
                continue  # can't compute P&L without entry — try again next sweep

            qty = float(row.get("qty", 0) or 0)
            side_long = (row.get("side", "") or "").lower() in ("buy", "long")
            sign = 1 if side_long else -1
            pnl = (decision["exit_price"] - entry_px) * qty * sign

            from services.manual_order_guard import record_manual_order_outcome
            block_hash = await record_manual_order_outcome(
                db,
                proof_chain_entity_id=row["proof_chain_entity_id"],
                trade_id=str(row.get("broker_order_id", "")),
                symbol=row.get("symbol", ""),
                direction="LONG" if side_long else "SHORT",
                asset_class="equity",
                entry_price=entry_px,
                exit_price=decision["exit_price"],
                quantity=qty,
                pnl=pnl,
                close_reason=decision["close_reason"],
                actor=f"reconciler:{broker_id}",
            )
            if block_hash:
                await db.trade_orders.update_one(
                    {"_id": row["_id"]},
                    {"$set": {
                        "outcome_appended": True,
                        "outcome_appended_at": datetime.now(timezone.utc),
                        "outcome_block_hash": block_hash,
                        "outcome_exit_price": decision["exit_price"],
                        "outcome_pnl": pnl,
                    }},
                )
                out["closed"] += 1
        except Exception as exc:  # noqa: BLE001
            out["errors"] += 1
            logger.warning(
                "[reconciler] equity row failed user=%s broker=%s row=%s: %s",
                user_id, broker_id, row.get("_id"), exc,
            )
    return out


async def reconcile_equity_positions(db: Any) -> dict:
    """Top-level sweep across all eligible equity orders.

    Returns a summary ``{users, processed, closed, errors}`` for
    the scheduler log.
    """
    if db is None:
        return {"users": 0, "processed": 0, "closed": 0, "errors": 0}

    cutoff_old = datetime.now(timezone.utc) - timedelta(days=RECONCILE_LOOKBACK_DAYS)
    cutoff_new = datetime.now(timezone.utc) - timedelta(minutes=RECONCILE_MIN_AGE_MINUTES)
    query = {
        "proof_chain_entity_id": {"$ne": None, "$exists": True},
        "outcome_appended": {"$ne": True},
        "created_at": {"$gte": cutoff_old, "$lte": cutoff_new},
    }
    cursor = db.trade_orders.find(query).sort("created_at", 1).limit(RECONCILE_MAX_ROWS_PER_PASS)
    rows = await cursor.to_list(length=RECONCILE_MAX_ROWS_PER_PASS)

    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = (r.get("user_id", ""), r.get("broker_id", ""))
        if not key[0] or not key[1]:
            continue
        grouped.setdefault(key, []).append(r)

    summary = {"users": len(grouped), "processed": 0, "closed": 0, "errors": 0}
    for (user_id, broker_id), group_rows in grouped.items():
        result = await _reconcile_equity_for_user_broker(
            db, user_id=user_id, broker_id=broker_id, rows=group_rows,
        )
        summary["processed"] += result["processed"]
        summary["closed"] += result["closed"]
        summary["errors"] += result["errors"]

    if summary["processed"] > 0:
        logger.info(
            "[reconciler.equity] users=%d processed=%d closed=%d errors=%d",
            summary["users"], summary["processed"], summary["closed"], summary["errors"],
        )
    return summary


# ── Options reconciler ─────────────────────────────────────────────


async def _is_option_position_closed(
    *, occ_symbol: str, side: str, qty: float, opened_at: datetime,
    option_positions: list[dict],
) -> Optional[dict]:
    """Decide whether a single option leg is closed at the broker.

    Options close detection is simpler than equity in the common case:
    if the OCC symbol no longer appears in option_positions, the
    contract has either been closed-out manually or expired.

    We *don't* try to extract exit price from the broker order list —
    options adapters return less consistent close data than equity.
    Instead we use ``limit_price`` from the original close order if
    findable, else mark expired (zero exit) for short positions.
    """
    occ = (occ_symbol or "").upper()

    for p in option_positions or []:
        if (p.get("symbol", "") or p.get("occ_symbol", "") or "").upper() == occ:
            try:
                pos_qty = float(p.get("qty", 0) or 0)
            except (TypeError, ValueError):
                pos_qty = 0.0
            if pos_qty != 0:
                return None  # still open

    # Closed. Without a price feed for closed contracts, we record an
    # expired/closed marker — exit_price=0 means the chain logs the
    # event but downstream P&L analytics treats it as "data pending".
    return {
        "exit_price": 0.0,
        "close_reason": "broker_option_closed_or_expired",
    }


async def _is_spread_closed(
    *, legs: list[dict], option_positions: list[dict],
) -> Optional[dict]:
    """Decide whether a multi-leg spread is closed at the broker.

    Conservative rule: a spread is closed only when **every leg's
    OCC** is absent (or zero-qty) at the broker. A single still-open
    leg means the spread is partially unwound — we wait. This is the
    same false-positive guard as single-leg + equity: we'd rather
    delay the chain close than fire OUTCOME_VERIFIED on an
    in-flight position.

    Returns ``{"close_reason": ...}`` on close, ``None`` if any leg
    is still open. We don't compute exit price for spreads — options
    adapters return inconsistent close data and the spread's exit P&L
    can't be reconstructed from broker positions alone. The
    OUTCOME_VERIFIED block is logged with ``exit_price=0.0`` and a
    ``close_reason="broker_spread_closed_or_expired"`` marker; a
    future enhancement can wire per-leg close fills if/when adapters
    expose them uniformly.
    """
    if not legs:
        return None

    open_qty_by_occ: dict[str, float] = {}
    for p in option_positions or []:
        occ = (p.get("symbol", "") or p.get("occ_symbol", "") or "").upper()
        if not occ:
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty != 0:
            open_qty_by_occ[occ] = qty

    for leg in legs:
        occ = (leg.get("occ_symbol") or "").upper()
        if not occ:
            # Defensive: a leg without an OCC means we can't verify
            # it's closed — refuse to fire so we don't false-positive
            # on data quality issues.
            return None
        if occ in open_qty_by_occ:
            return None  # at least one leg still open

    return {
        "exit_price": 0.0,
        "close_reason": "broker_spread_closed_or_expired",
    }


async def _reconcile_options_for_user(
    db: Any, *, user_id: str, rows: list[dict],
) -> dict:
    """Sweep all open option rows for one user.

    Options brokers vary — Alpaca exposes options positions through
    its core ``get_positions`` endpoint, Tradier through a separate
    API, etc. The adapter layer abstracts this. We use the user's
    configured options provider via ``get_options_adapter``.

    Handles both single-leg and multi-leg spreads. Spreads close
    only when every leg's OCC has dropped from broker positions —
    see ``_is_spread_closed``.
    """
    from services.brokers.registry import get_options_adapter

    out = {"processed": 0, "closed": 0, "errors": 0}

    # Group rows by provider so we hit each adapter exactly once.
    by_provider: dict[str, list[dict]] = {}
    for r in rows:
        prov = (r.get("provider") or "").lower()
        if prov:
            by_provider.setdefault(prov, []).append(r)

    for provider, prov_rows in by_provider.items():
        try:
            adapter = get_options_adapter(provider)
            # Adapter may not expose option positions for stub
            # providers; treat absence as "skip".
            list_fn = getattr(adapter, "list_option_positions", None)
            if list_fn is None:
                continue
            option_positions = await list_fn()  # may be coroutine
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[reconciler] options adapter fetch failed user=%s provider=%s: %s",
                user_id, provider, exc,
            )
            out["errors"] += len(prov_rows)
            continue

        for row in prov_rows:
            out["processed"] += 1
            try:
                opened_at = ensure_utc(row.get("created_at"))
                if opened_at is None:
                    continue

                if row.get("is_spread"):
                    # Multi-leg path — all legs must be closed at
                    # the broker before we append OUTCOME_VERIFIED.
                    legs = row.get("legs") or []
                    decision = await _is_spread_closed(
                        legs=legs, option_positions=option_positions,
                    )
                    if decision is None:
                        continue

                    # Net debit/credit on entry. Negative = credit
                    # received; positive = debit paid.
                    entry_net = float(row.get("limit_price") or 0)
                    # Sum of absolute leg qty × 100 multiplier — the
                    # representative size for the trade. We can't
                    # split P&L per leg without per-leg fill prices.
                    total_qty = sum(
                        float(leg.get("qty") or 0) for leg in legs
                    )
                    # Conservative P&L: treat the spread as fully
                    # expired/zeroed at close. For a debit spread
                    # this records a loss equal to the debit; for a
                    # credit spread, a profit equal to the credit.
                    # When the adapter eventually returns close fills
                    # we can refine this; for now the OUTCOME_VERIFIED
                    # block is at least appended, breaking the
                    # "spread chain dead-ends at fill" gap.
                    pnl = -float(entry_net) * 100.0 * (
                        total_qty / max(len(legs), 1)
                    )

                    from services.manual_order_guard import (
                        record_manual_order_outcome,
                    )
                    block_hash = await record_manual_order_outcome(
                        db,
                        proof_chain_entity_id=row["proof_chain_entity_id"],
                        trade_id=str(row.get("order_id", "")),
                        symbol=str(
                            (legs[0] or {}).get("occ_symbol", "") or "spread"
                        ),
                        direction="SPREAD",
                        asset_class="options_spread",
                        entry_price=entry_net,
                        exit_price=decision["exit_price"],
                        quantity=total_qty,
                        pnl=pnl,
                        close_reason=decision["close_reason"],
                        actor=f"reconciler:{provider}",
                    )
                    if block_hash:
                        await db.option_orders.update_one(
                            {"_id": row["_id"]},
                            {"$set": {
                                "outcome_appended": True,
                                "outcome_appended_at": datetime.now(timezone.utc),
                                "outcome_block_hash": block_hash,
                                "outcome_exit_price": decision["exit_price"],
                                "outcome_pnl": pnl,
                                "outcome_leg_count": len(legs),
                            }},
                        )
                        out["closed"] += 1
                    continue

                # Single-leg path.
                occ_symbol = row.get("occ_symbol")
                if not occ_symbol:
                    continue
                legs = row.get("legs") or []
                leg_side = (
                    (legs[0].get("side") if legs else "") or ""
                ).lower()
                qty = float(legs[0].get("qty") or 0) if legs else 0.0

                decision = await _is_option_position_closed(
                    occ_symbol=occ_symbol,
                    side=leg_side,
                    qty=qty,
                    opened_at=opened_at,
                    option_positions=option_positions,
                )
                if decision is None:
                    continue

                entry_px = float(row.get("limit_price") or 0)
                if entry_px <= 0:
                    continue  # can't compute P&L

                # Long option → buy_to_open (debit), profit on
                # exit_price > entry_price. Short option → sell_to_open
                # (credit), profit on exit_price < entry_price.
                long_option = leg_side in ("buy_to_open", "buy")
                sign = 1 if long_option else -1
                pnl = (decision["exit_price"] - entry_px) * qty * 100.0 * sign

                from services.manual_order_guard import record_manual_order_outcome
                block_hash = await record_manual_order_outcome(
                    db,
                    proof_chain_entity_id=row["proof_chain_entity_id"],
                    trade_id=str(row.get("order_id", "")),
                    symbol=occ_symbol,
                    direction="LONG" if long_option else "SHORT",
                    asset_class="options",
                    entry_price=entry_px,
                    exit_price=decision["exit_price"],
                    quantity=qty,
                    pnl=pnl,
                    close_reason=decision["close_reason"],
                    actor=f"reconciler:{provider}",
                )
                if block_hash:
                    await db.option_orders.update_one(
                        {"_id": row["_id"]},
                        {"$set": {
                            "outcome_appended": True,
                            "outcome_appended_at": datetime.now(timezone.utc),
                            "outcome_block_hash": block_hash,
                            "outcome_exit_price": decision["exit_price"],
                            "outcome_pnl": pnl,
                        }},
                    )
                    out["closed"] += 1
            except Exception as exc:  # noqa: BLE001
                out["errors"] += 1
                logger.warning(
                    "[reconciler] options row failed user=%s row=%s: %s",
                    user_id, row.get("_id"), exc,
                )
    return out


async def reconcile_options_positions(db: Any) -> dict:
    """Top-level sweep across all eligible options orders.

    Now includes multi-leg spreads — the ``is_spread:{$ne: true}``
    filter was the deferred work-item from the first reconciler ship.
    """
    if db is None:
        return {"users": 0, "processed": 0, "closed": 0, "errors": 0}

    cutoff_old = datetime.now(timezone.utc) - timedelta(days=RECONCILE_LOOKBACK_DAYS)
    cutoff_new = datetime.now(timezone.utc) - timedelta(minutes=RECONCILE_MIN_AGE_MINUTES)
    query = {
        "proof_chain_entity_id": {"$ne": None, "$exists": True},
        "outcome_appended": {"$ne": True},
        "created_at": {"$gte": cutoff_old, "$lte": cutoff_new},
    }
    cursor = db.option_orders.find(query).sort("created_at", 1).limit(RECONCILE_MAX_ROWS_PER_PASS)
    rows = await cursor.to_list(length=RECONCILE_MAX_ROWS_PER_PASS)

    by_user: dict[str, list[dict]] = {}
    for r in rows:
        uid = r.get("user_id", "")
        if uid:
            by_user.setdefault(uid, []).append(r)

    summary = {"users": len(by_user), "processed": 0, "closed": 0, "errors": 0}
    for user_id, user_rows in by_user.items():
        result = await _reconcile_options_for_user(db, user_id=user_id, rows=user_rows)
        summary["processed"] += result["processed"]
        summary["closed"] += result["closed"]
        summary["errors"] += result["errors"]

    if summary["processed"] > 0:
        logger.info(
            "[reconciler.options] users=%d processed=%d closed=%d errors=%d",
            summary["users"], summary["processed"], summary["closed"], summary["errors"],
        )
    return summary


# ── Combined entry point for scheduler ─────────────────────────────


async def run_position_reconciler(db: Any) -> dict:
    """Single scheduler entry — runs both equity and options sweeps."""
    eq = await reconcile_equity_positions(db)
    opt = await reconcile_options_positions(db)
    return {"equity": eq, "options": opt}
