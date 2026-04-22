"""One-time migration: backfill `paper_trades.opened_at` from
`timestamp` (ISO string) for manual-UI rows that never had it set.

Context
-------
The Tier-3 paper-trading gate counts distinct UTC dates on which at
least one `paper_trades` row has a BSON-date `opened_at`. That field
is populated by `services.ml_paper_trader.maybe_paper_trade` but NOT
by the manual paper-trading UI (`routes/paper_trading.py`), which
writes ISO-string `timestamp` instead. As a result 77/82 existing
rows today are invisible to the gate.

This migration broadens the gate's view by normalising `opened_at`
across both writers. AFTER running it, `tier3_progress(db)` will
include days on which the user manually clicked Buy — matching the
user's explicit request to treat both writers as paper-trading
activity.

Rules
-----
* Idempotent — only touches rows where `opened_at` is missing/null.
* Prefers (in order): `opened_at` (already set → skip),
  `created_at`, `timestamp`. Each candidate is parsed into a
  timezone-aware datetime; strings are parsed via `fromisoformat`
  and coerced to UTC.
* Dry-run by default (prints the planned updates without writing).
  Pass `--apply` to actually mutate the collection.
* Unparseable rows are skipped and reported — never silently
  mislabelled.

Usage
-----
    cd /app/backend
    python -m scripts.backfill_opened_at            # dry run
    python -m scripts.backfill_opened_at --apply    # write

The script is safe to re-run; successive invocations after `--apply`
will report zero updates.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_opened_at")


def _parse_any(value: Any) -> datetime | None:
    """Best-effort coerce a value into a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Support trailing 'Z' (Zulu time) which fromisoformat doesn't
        # accept pre-3.11 and is a common serialisation.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def _run(apply: bool) -> int:
    url = os.environ["MONGO_URL"]
    dbname = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(url)
    db = client[dbname]

    # Rows to consider: missing or null `opened_at`.
    query = {
        "$or": [
            {"opened_at": {"$exists": False}},
            {"opened_at": None},
        ]
    }
    total = await db.paper_trades.count_documents(query)
    log.info("candidates: %d (rows missing opened_at)", total)
    if total == 0:
        log.info("nothing to do — already migrated")
        return 0

    planned = 0
    skipped = 0
    unparseable: list[dict] = []

    cursor = db.paper_trades.find(
        query,
        projection={"_id": 1, "timestamp": 1, "created_at": 1},
    )
    async for row in cursor:
        # Priority: explicit created_at, then timestamp.
        candidate_dt = None
        for field in ("created_at", "timestamp"):
            candidate_dt = _parse_any(row.get(field))
            if candidate_dt is not None:
                break
        if candidate_dt is None:
            unparseable.append({"_id": str(row["_id"])})
            skipped += 1
            continue
        planned += 1
        if apply:
            await db.paper_trades.update_one(
                {"_id": row["_id"]},
                {"$set": {"opened_at": candidate_dt}},
            )
        else:
            log.info(
                "  would update %s -> opened_at = %s",
                row["_id"],
                candidate_dt.isoformat(),
            )

    verb = "updated" if apply else "planned"
    log.info("%s: %d, skipped (unparseable): %d", verb, planned, skipped)
    if unparseable:
        log.warning("unparseable row ids: %s", unparseable[:10])

    if apply:
        # Post-check: confirm the Tier-3 count now reflects reality.
        from services.paper_trading_progress import compute_live_days
        days = await compute_live_days(db)
        log.info("post-migration distinct UTC paper-trading days: %d", days)

    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write updates. Without this flag, the script "
        "only reports what it would do.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(0 if main() >= 0 else 1)
