"""Backfill execution economics onto ``features_snapshots`` rows.

For every resolved row in ``learning_engine_trades`` (status in
{win, loss}) that carries ``entry``, ``exit_price``, optionally
``stop_loss``, and ``direction``, find the corresponding
``features_snapshot`` (matched by prediction_id) and stamp the
four execution fields on it.

Uses the canonical
:func:`services.snapshot_enricher.stamp_execution_on_snapshot`
helper so the write semantics match the live path in
``prediction_tracker``.

Usage
-----
    python scripts/backfill_snapshot_execution.py
    python scripts/backfill_snapshot_execution.py --dry-run
    python scripts/backfill_snapshot_execution.py --limit 1000

Environment variables
---------------------
    MONGO_URL   MongoDB connection string
    DB_NAME     Database name

Idempotent — running twice is safe: the helper's ``$set`` only
writes fields that are currently None-or-missing on the target row,
and re-writing the same execution block is a no-op against the
enrichment contract.

NOTE: the live resolve path in ``prediction_tracker`` stamps the
prediction_id link from ``pred["prediction_id"]``. The legacy
``learning_engine_trades`` rows don't carry prediction_id — they
use FIFO matching against ``(asset, direction, user_id, status)``.
For the backfill we need a reverse lookup from the trade record
back to the originating prediction. We use the ``predictions``
collection: match ``(symbol, direction, user_id)`` where the
prediction's ``timestamp`` is just before the trade's
``logged_at``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore[import-untyped]

from services.snapshot_enricher import stamp_execution_on_snapshot

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

MONGO_URL: str = os.environ["MONGO_URL"]
DB_NAME: str = os.environ["DB_NAME"]
TRADES_COLL: str = "learning_engine_trades"
PREDICTIONS_COLL: str = "predictions"
SNAPSHOTS_COLL: str = "features_snapshots"


def _parse_logged_at(value: Any) -> datetime | None:
    """learning_engine_trades.logged_at is stored as an ISO string.
    Tolerate datetime objects too for forward-compat."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def _find_prediction_id(db: Any, trade: dict) -> str | None:
    """Reverse-lookup from a trade → its originating prediction_id.

    Match on (symbol, direction, user_id) where the prediction
    fired most recently BEFORE the trade was logged. Returns None
    if no reasonable match is found.
    """
    asset = trade.get("asset")
    direction = trade.get("direction")
    user_id = trade.get("user_id")
    logged_at = _parse_logged_at(trade.get("logged_at"))
    if not (asset and direction and logged_at):
        return None

    # Direction on predictions is typically BUY/SELL or up/down; on
    # learning_engine_trades it's LONG/SHORT. Normalise to a set of
    # candidate strings the predictions collection might use.
    dir_candidates = {direction}
    if direction == "LONG":
        dir_candidates |= {"BUY", "BULLISH", "up", "LONG", "long"}
    elif direction == "SHORT":
        dir_candidates |= {"SELL", "BEARISH", "down", "SHORT", "short"}

    query: dict[str, Any] = {
        "symbol": asset,
        "direction": {"$in": list(dir_candidates)},
        "timestamp": {"$lte": logged_at.isoformat()},
    }
    if user_id:
        query["user_id"] = user_id

    pred = await db[PREDICTIONS_COLL].find_one(
        query,
        {"_id": 0, "prediction_id": 1},
        sort=[("timestamp", -1)],
    )
    if pred is None:
        return None
    return pred.get("prediction_id")


async def backfill(db: Any, dry_run: bool, limit: int) -> dict:
    """Walk resolved learning_engine_trades and stamp execution
    economics on the matching features_snapshots rows."""
    stats = {"scanned": 0, "stamped": 0, "no_prediction_match": 0,
             "no_snapshot_match": 0, "skipped_incomplete": 0}

    cursor = (
        db[TRADES_COLL]
        .find(
            {"status": {"$in": ["win", "loss"]},
             "entry": {"$ne": None}, "exit_price": {"$ne": None}},
            {"_id": 0},
        )
        .sort("logged_at", -1)
        .limit(int(limit))
    )

    async for trade in cursor:
        stats["scanned"] += 1
        entry = trade.get("entry")
        exit_p = trade.get("exit_price")
        stop = trade.get("stop_loss")  # optional
        direction = trade.get("direction")
        if entry is None or exit_p is None or not direction:
            stats["skipped_incomplete"] += 1
            continue

        pred_id = await _find_prediction_id(db, trade)
        if not pred_id:
            stats["no_prediction_match"] += 1
            continue

        if dry_run:
            log.info("[DRY] would stamp pred=%s asset=%s dir=%s entry=%.4f exit=%.4f stop=%s",
                     pred_id, trade.get("asset"), direction, entry, exit_p, stop)
            stats["stamped"] += 1
            continue

        matched = await stamp_execution_on_snapshot(
            db=db,
            prediction_id=pred_id,
            entry_price=float(entry),
            exit_price=float(exit_p),
            stop_loss=float(stop) if stop else None,
            direction=direction,
        )
        if matched:
            stats["stamped"] += 1
        else:
            stats["no_snapshot_match"] += 1

    return stats


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill execution economics on features_snapshots"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what would be stamped, don't write.")
    parser.add_argument("--limit", type=int, default=10000,
                        help="Max resolved trades to process (default 10000).")
    args = parser.parse_args()

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        log.info("[backfill] starting  dry_run=%s limit=%d", args.dry_run, args.limit)
        stats = await backfill(db, dry_run=args.dry_run, limit=args.limit)
        log.info("[backfill] done  %s", stats)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())
