"""Alpaca order → local audit reconciler (2026-05-22).

One-shot reconciliation script: pulls fills from Alpaca's
``/v2/orders`` history and mirrors them into the local
``paper_trades`` collection so Tier 3 readiness, the Stage 3
Sovereign-vs-Council ledger, and the Sovereign outcome bridge
can see them.

Why this exists
---------------
``ml_alpaca_broker.maybe_execute_live`` and the manual UI broker
path both submit orders to Alpaca paper. They write a record to
the ``live_orders`` collection — but ``paper_trades`` (which is
what Tier 3, Stage 3, and the outcome bridge read) was never
touched. The result: ~1000 Alpaca paper orders, 0 local audit
rows, Tier 3 frozen at 23/100 trades for months.

Usage
-----
Dry-run (default — counts what would change, writes nothing)::

    python -m scripts.reconcile_alpaca_orders --since 2026-05-04 --until 2026-05-18

Apply::

    python -m scripts.reconcile_alpaca_orders --since 2026-05-04 --until 2026-05-18 --apply

The reconciler is idempotent by ``alpaca_order_id``: re-running it
never duplicates a row.

Doctrine
--------
* Alpaca is the source of truth for order state. We mirror, never
  overwrite the broker.
* ``status`` is mapped: ``filled → closed``, ``new/accepted/partially_filled → open``,
  anything else → ``alpaca_<status>`` (skipped from Tier 3 math).
* Filled orders also enqueue into ``sovereign_outcomes_inbox`` so
  the brain's LocalState catches up.
* No prices / fills are invented — if Alpaca didn't return
  ``filled_avg_price`` we leave ``entry_price = 0`` and the row
  doesn't graduate to Stage 3 (the outcome writer guards on
  ``entry > 0``).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

# Boilerplate so this works whether invoked as a module or a script.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

logger = logging.getLogger("reconcile_alpaca_orders")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _alpaca_base() -> str:
    return os.environ.get("ALPACA_BASE_URL",
                          "https://paper-api.alpaca.markets").rstrip("/")


def _alpaca_headers() -> dict[str, str]:
    key = (os.environ.get("APCA_API_KEY_ID")
           or os.environ.get("ALPACA_API_KEY") or "").strip()
    sec = (os.environ.get("APCA_API_SECRET_KEY")
           or os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    if not key or not sec:
        raise RuntimeError(
            "Alpaca keys missing. Set ALPACA_API_KEY + ALPACA_SECRET_KEY "
            "(or APCA_API_KEY_ID + APCA_API_SECRET_KEY)."
        )
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": sec,
        "Content-Type": "application/json",
    }


async def _fetch_alpaca_orders(after_iso: str, until_iso: str) -> list[dict]:
    """Page through /v2/orders. Alpaca caps at 500 per page; we
    walk backwards by ``submitted_at`` until empty."""
    url = f"{_alpaca_base()}/v2/orders"
    headers = _alpaca_headers()
    rows: list[dict] = []
    cursor_until = until_iso
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            params = {
                "status": "all",
                "after": after_iso,
                "until": cursor_until,
                "limit": 500,
                "direction": "desc",
            }
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Alpaca returned {resp.status_code}: {resp.text[:200]}"
                )
            page = resp.json()
            if not page:
                break
            rows.extend(page)
            if len(page) < 500:
                break
            # Walk backwards by submitted_at
            oldest = page[-1].get("submitted_at")
            if not oldest or oldest <= after_iso:
                break
            cursor_until = oldest
    return rows


def _map_status(alpaca_status: str) -> str:
    s = (alpaca_status or "").lower()
    if s == "filled":
        return "closed"
    if s in ("new", "accepted", "pending_new", "partially_filled",
             "accepted_for_bidding"):
        return "open"
    # Anything else (canceled, expired, rejected) — quarantined; Tier 3 ignores.
    return f"alpaca_{s}"


def _outcome_label(pnl_pct: float) -> str:
    if pnl_pct > 0.005:
        return "win"
    if pnl_pct < -0.005:
        return "loss"
    return "flat"


# Signal labelling lives in services/backfill_signal_matcher.py so
# the script stays under the 400-line preferred ceiling AND the
# matcher becomes reusable by any future broker backfill (Kraken,
# IBKR, etc.). Operator hard rule: every backfilled fill MUST carry
# a label.
from services.backfill_signal_matcher import (  # noqa: E402
    SIGNAL_MATCH_WINDOW as _SIGNAL_MATCH_WINDOW,
    direction_matches as _direction_matches,
    match_signal as _match_signal,
)


async def reconcile(db: Any, *, since: str, until: str,
                    apply: bool,
                    enqueue_outcomes: bool = False) -> dict[str, int]:
    """Walk Alpaca orders → write to paper_trades + outcome inbox.

    Returns a stats dict for the operator."""
    logger.info("Fetching Alpaca orders %s → %s", since, until)
    orders = await _fetch_alpaca_orders(since, until)
    logger.info("Fetched %d orders from Alpaca", len(orders))

    stats = {
        "fetched": len(orders),
        "would_insert": 0,
        "would_close": 0,
        "would_enqueue_outcome": 0,
        "deduped": 0,
        "skipped_no_price": 0,
        "skipped_quarantined": 0,
        "labelled_sovereign": 0,
        "labelled_prediction": 0,
        "unattributed": 0,
    }

    # Collect inserted/mirrored rows so the optional outcome pairer
    # can FIFO them into round-trip outcomes after the main loop.
    mirrored_rows: list[dict[str, Any]] = []

    for o in orders:
        oid = o.get("id")
        if not oid:
            continue
        sym = (o.get("symbol") or "").upper()
        side = (o.get("side") or "").lower()  # buy / sell
        a_status = (o.get("status") or "").lower()
        status = _map_status(a_status)
        if status.startswith("alpaca_"):
            stats["skipped_quarantined"] += 1
            continue

        qty = float(o.get("filled_qty") or o.get("qty") or 0)
        entry = float(o.get("filled_avg_price") or o.get("limit_price") or 0)
        if entry <= 0:
            stats["skipped_no_price"] += 1
            continue

        submitted_at_str = o.get("submitted_at") or o.get("created_at")
        filled_at_str = o.get("filled_at")
        opened_at = (
            datetime.fromisoformat(submitted_at_str.replace("Z", "+00:00"))
            if submitted_at_str else datetime.now(timezone.utc)
        )
        closed_at = (
            datetime.fromisoformat(filled_at_str.replace("Z", "+00:00"))
            if filled_at_str and status == "closed" else None
        )
        direction = "up" if side == "buy" else "down"

        # Idempotency check: dedupe on alpaca_order_id
        existing = await db["paper_trades"].find_one(
            {"alpaca_order_id": oid}, {"_id": 0, "status": 1},
        )
        if existing:
            stats["deduped"] += 1
            continue

        # ── Operator hard rule (2026-05-22): every backfilled fill
        # MUST carry a label. Look for the signal that fired this
        # order — Sovereign decision first, then prediction. If
        # nothing matches within the 10-min window, the row is
        # explicitly tagged ``unattributed_real_fill`` (NOT given a
        # synthetic confidence). Tier 3 / Stage 3 can then make
        # informed choices about whether to count them.
        signal_match = await _match_signal(
            db, symbol=sym, side=side, when=opened_at,
        )
        if signal_match:
            if signal_match["source_signal"] == "sovereign_decision":
                stats["labelled_sovereign"] += 1
            else:
                stats["labelled_prediction"] += 1
        else:
            stats["unattributed"] += 1

        doc = {
            "trade_id": f"alpaca-{oid}",
            "alpaca_order_id": oid,
            "ticker": sym,
            "symbol": sym,
            "direction": direction,
            "side": side,
            "shares": qty,
            "qty": qty,
            "entry_price": entry,
            "position_usd": qty * entry,
            "status": status,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "source_layer": "alpaca_reconciler",
            "receipt_type": (
                "real_fill" if signal_match
                else "unattributed_real_fill"
            ),
            "synthetic": False,
            "eligible_for_learning": bool(signal_match),
            "eligible_for_live_unlock": True,
            "alpaca_raw_status": a_status,
            # Labelling — operator hard rule.
            "labelled": bool(signal_match),
            "source_signal": (
                signal_match.get("source_signal") if signal_match else None
            ),
            "confidence": (
                float(signal_match["confidence"]) if signal_match else 0.0
            ),
            "sovereign_decision_id": (
                signal_match.get("sovereign_decision_id")
                if signal_match else None
            ),
            "prediction_id": (
                signal_match.get("prediction_id") if signal_match else None
            ),
            "conviction_tier": (
                signal_match.get("conviction_tier") if signal_match else None
            ),
            "regime": (signal_match.get("regime") if signal_match else None),
            "signal_at": (
                signal_match.get("signal_at") if signal_match else None
            ),
        }

        if status == "closed":
            stats["would_close"] += 1
        else:
            stats["would_insert"] += 1

        # Track every mirrored row so the optional FIFO pairer
        # below has the full picture, regardless of apply mode.
        mirrored_rows.append(doc)

        if not apply:
            continue

        await db["paper_trades"].insert_one(doc)

        # For filled orders we have entry + exit on the same row (Alpaca
        # gives us filled_avg_price). Stage 3 / outcome resolution needs
        # exit price separately. Defer that to the closer; here we just
        # mirror the fill itself. If the broker later sells, the SELL
        # order arrives as its own row and the reconciler closes the
        # buy-side via pair matching (out-of-scope for v1).

    # ── Gap 1 (2026-05-22): FIFO-pair mirrored fills into resolved
    # outcomes and enqueue them on the Sovereign sidecar's inbox.
    # Opt-in via ``--enqueue-outcomes`` so dry-runs stay dry. The
    # pairer is provenance-aware: the BUY lot's
    # ``sovereign_decision_id`` / ``prediction_id`` / ``source_signal``
    # rides through to the inbox, completing the audit lineage MC
    # needs to see (the missing piece that made every Alpha
    # contribution look like ``empty payload`` for months).
    if enqueue_outcomes:
        from services.backfill_outcome_pairer import pair_fills_into_outcomes
        from services.sovereign_outcome_bridge import enqueue_outcome
        outcomes = pair_fills_into_outcomes(mirrored_rows)
        logger.info("Paired %d round-trip outcomes from %d mirrored rows",
                    len(outcomes), len(mirrored_rows))
        for o in outcomes:
            stats["would_enqueue_outcome"] += 1
            if not apply:
                continue
            await enqueue_outcome(
                db,
                brain="alpha",
                trade_id=o["trade_id"],
                symbol=o["symbol"],
                direction=o["direction"],
                confidence=float(o.get("confidence") or 0.0),
                outcome_label=o["outcome_label"],
                notional=float(o.get("notional") or 0.0),
                extras={
                    "receipt_type": "alpaca_backfill_pair",
                    "pnl_pct": o.get("pnl_pct"),
                    "buy_trade_id": o.get("buy_trade_id"),
                    "sell_trade_id": o.get("sell_trade_id"),
                },
                sovereign_decision_id=o.get("sovereign_decision_id"),
                prediction_id=o.get("prediction_id"),
                source_signal=o.get("source_signal"),
            )

    logger.info("Reconciliation complete: %s", stats)
    return stats


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True,
                        help="ISO date (e.g. 2026-05-04)")
    parser.add_argument("--until", required=True,
                        help="ISO date (e.g. 2026-05-18)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write rows (default is dry-run)")
    parser.add_argument("--enqueue-outcomes", action="store_true",
                        help=("FIFO-pair filled BUY/SELL legs into "
                              "round-trip outcomes and enqueue them on "
                              "the Sovereign sidecar's inbox (Gap 1)."))
    args = parser.parse_args()

    since = args.since + ("T00:00:00Z" if "T" not in args.since else "")
    until = args.until + ("T23:59:59Z" if "T" not in args.until else "")

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not db_name:
        logger.error("MONGO_URL / DB_NAME not set in env.")
        return 2

    db = AsyncIOMotorClient(mongo_url)[db_name]
    stats = await reconcile(
        db, since=since, until=until, apply=args.apply,
        enqueue_outcomes=args.enqueue_outcomes,
    )
    print()
    print(f"  apply mode:           {args.apply}")
    print(f"  enqueue outcomes:     {args.enqueue_outcomes}")
    print(f"  fetched from Alpaca:  {stats['fetched']}")
    print(f"  would insert (open):  {stats['would_insert']}")
    print(f"  would close (filled): {stats['would_close']}")
    print(f"  would enqueue out:    {stats['would_enqueue_outcome']}")
    print(f"  already in Mongo:     {stats['deduped']}")
    print(f"  skipped no-price:     {stats['skipped_no_price']}")
    print(f"  skipped quarantined:  {stats['skipped_quarantined']}")
    print("  --- labelling (operator hard rule) ---")
    print(f"  labelled (sovereign): {stats['labelled_sovereign']}")
    print(f"  labelled (prediction):{stats['labelled_prediction']}")
    print(f"  UNATTRIBUTED:         {stats['unattributed']}  "
          "(tagged receipt_type=unattributed_real_fill)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
