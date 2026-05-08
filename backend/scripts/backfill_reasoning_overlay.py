"""
One-off: backfill the ``reasoning`` overlay onto historical
``predictions`` rows that pre-date the 2026-05-04 reasoning rollout.

Why this is a script, not a migration
─────────────────────────────────────
* Read-only by construction — the overlay is a pure function. No risk
  to action / confidence / sizing data.
* Idempotent — rows that already carry a ``reasoning`` field are
  skipped, so re-running the script is safe.
* The richer reason codes (``COMMANDER_DISAGREES`` etc.) won't appear
  on backfilled rows because the upstream call sites didn't capture
  ``passed_gates`` / ``commander_shadow`` / ``risk_adjustments`` at
  the time of writing. What we DO get on every row is the
  calibration-aware subset:
  ``REGIME_SUPPORTS_LONG/SHORT`` / ``NEUTRAL_ACTION``,
  ``UNDERCONFIDENT_MODEL`` / ``OVERCONFIDENT_MODEL``,
  ``PASSED_ALL_GATES``. That's enough for the operator query
  "show me every under-confident historical prediction".

Usage
─────
::

    cd /app/backend
    python -m scripts.backfill_reasoning_overlay --dry-run   # count
    python -m scripts.backfill_reasoning_overlay             # apply

The ``--dry-run`` mode reports the number of rows that would be
backfilled and the reason-code distribution the overlay would
produce, without touching the database.

Exit codes
──────────
* 0 — success (rows backfilled, OR --dry-run produced output)
* 1 — Mongo I/O failure
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient


async def _amain(args: argparse.Namespace) -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("error: MONGO_URL / DB_NAME not set", file=sys.stderr)
        return 1

    from services.decision_reasoning_overlay import build_reasoning_overlay

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    cursor = db.predictions.find(
        {"reasoning": {"$exists": False}},
        {
            "_id": 0,
            "prediction_id": 1,
            "symbol": 1,
            "direction": 1,
            "confidence": 1,
            "calibrated_confidence": 1,
        },
    ).batch_size(args.batch_size)

    n_total = await db.predictions.count_documents({})
    n_missing = await db.predictions.count_documents(
        {"reasoning": {"$exists": False}},
    )
    print(f"corpus  : {n_total} total predictions")
    print(f"missing : {n_missing} rows without 'reasoning'")
    if n_missing == 0:
        print("nothing to backfill — all rows already carry the overlay.")
        return 0

    code_counter: Counter[str] = Counter()
    n_updated = 0
    n_skipped = 0
    batch_writes: list[tuple[str, dict]] = []

    async for row in cursor:
        pid = row.get("prediction_id")
        if not pid:
            n_skipped += 1
            continue

        decision_view = {
            "symbol": row.get("symbol"),
            "action": row.get("direction"),
            "confidence": row.get("confidence"),
            "calibrated_confidence": row.get("calibrated_confidence"),
        }
        reasoning = build_reasoning_overlay(decision_view)
        for code in reasoning.get("reason_codes") or []:
            code_counter[code] += 1

        if args.dry_run:
            n_updated += 1
            continue

        batch_writes.append((pid, reasoning))
        if len(batch_writes) >= args.batch_size:
            await _flush(db, batch_writes)
            n_updated += len(batch_writes)
            batch_writes = []

    if not args.dry_run and batch_writes:
        await _flush(db, batch_writes)
        n_updated += len(batch_writes)

    verb = "would-update" if args.dry_run else "updated"
    print(f"{verb}  : {n_updated} rows")
    if n_skipped:
        print(f"skipped : {n_skipped} rows (missing prediction_id)")
    print("reason-code distribution:")
    for code, n in code_counter.most_common():
        print(f"  {code:35s}  {n}")
    return 0


async def _flush(db, batch: list[tuple[str, dict]]) -> None:
    """Apply a batch of (prediction_id, reasoning) updates.

    Uses single-row updates (not bulk_write) deliberately — the
    field is small, total volume is low (270 rows live as of
    backfill date), and per-row updates keep the failure mode
    bounded to a single row when something goes wrong.
    """
    for pid, reasoning in batch:
        await db.predictions.update_one(
            {"prediction_id": pid, "reasoning": {"$exists": False}},
            {"$set": {"reasoning": reasoning}},
        )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Count rows + show reason-code distribution without writing.",
    )
    p.add_argument(
        "--batch-size", type=int, default=200,
        help="Cursor + write batch size (default 200).",
    )
    return p


if __name__ == "__main__":
    args = _parser().parse_args()
    sys.exit(asyncio.run(_amain(args)))
