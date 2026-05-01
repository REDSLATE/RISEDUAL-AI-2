"""
Backfill: re-grade STRONG_* / WEAK_* direction predictions that were
incorrectly labelled `STRONG_MISS` by the pre-2026-05-01 direction-token bug.

Bug recap
---------
`prediction_tracker.DIRECTION_BULLISH` was {"BUY","BULLISH","LONG","UP"} —
missing STRONG_BUY/WEAK_BUY. Same story for BEARISH. So every prediction
written with one of the STRONG_*/WEAK_* tokens (which is what
`signal_dispatcher` and the AI verdict pipeline emit) fell through
`grade_prediction()` to the catch-all `return "STRONG_MISS"` line, getting
auto-graded a miss regardless of price movement.

This script re-grades those rows, mutating ONLY where the new grade differs
from the stored one. Every change is logged to a per-run ChangeLog
collection so the operation is auditable / reversible.

Usage
-----
    # Show what would change, change nothing:
    python -m scripts.backfill_strong_direction_grades --dry-run

    # Apply (writes are gated behind --apply explicitly):
    python -m scripts.backfill_strong_direction_grades --apply

    # Limit scope to a window (default: all-time on this collection):
    python -m scripts.backfill_strong_direction_grades --dry-run --days 30

Side effects (--apply only)
---------------------------
* Mongo `predictions`: rewrites `verified_24h.{grade,correct,failure_code,
  failure_reason}` for matching rows.
* Mongo `prediction_grade_backfill_log`: one row per change for audit.
* ChromaDB retag is in `backfill_chroma_supersede_after_regrade.py` — run that
  AFTER this one so it can read the corrected Mongo grades.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

# Path setup so this can run as a module from /app/backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from motor.motor_asyncio import AsyncIOMotorClient

from services.prediction_tracker import (
    grade_prediction,
    FAILURE_MODES,
    _classify_failure,
    _neutral_tolerance,
)

logger = logging.getLogger("backfill_strong_direction")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

AFFECTED_DIRECTIONS = ["STRONG_BUY", "WEAK_BUY", "STRONG_SELL", "WEAK_SELL"]
BACKFILL_REASON = "direction_token_backfill_v1"
LOG_COLLECTION = "prediction_grade_backfill_log"


def _derive_correct(grade: str) -> bool | None:
    """Mirror prediction_tracker.verify_pending_predictions storage rule."""
    if grade == "NEUTRAL":
        return None
    return grade in {"STRONG_HIT", "WEAK_HIT"}


async def _build_query(days: int | None) -> dict[str, Any]:
    """Match only STRONG_*/WEAK_* rows currently graded STRONG_MISS.

    The user's exact filter:
        direction in [STRONG_BUY, WEAK_BUY, STRONG_SELL, WEAK_SELL]
        AND verified_24h.grade == "STRONG_MISS"
    """
    q: dict[str, Any] = {
        "direction": {"$in": AFFECTED_DIRECTIONS},
        "verified_24h.grade": "STRONG_MISS",
        "price_at_prediction": {"$gt": 0},
    }
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q["verified_24h.verified_at"] = {"$gte": since}
    return q


async def run_backfill(*, dry_run: bool, days: int | None, run_id: str) -> dict:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    q = await _build_query(days)
    total = await db.predictions.count_documents(q)
    logger.info(
        "Scanning %d rows where direction ∈ %s AND verified_24h.grade == 'STRONG_MISS'%s",
        total,
        AFFECTED_DIRECTIONS,
        f" within last {days}d" if days else "",
    )

    cursor = db.predictions.find(
        q,
        {
            "_id": 0,
            "prediction_id": 1,
            "symbol": 1,
            "feature": 1,
            "direction": 1,
            "confidence": 1,
            "price_at_prediction": 1,
            "timestamp": 1,
            "verified_24h": 1,
        },
    )

    stats = {
        "scanned": 0,
        "regraded_to_hit": 0,
        "regraded_to_neutral": 0,
        "regraded_to_weak_miss": 0,
        "still_strong_miss": 0,
        "skipped_invalid": 0,
        "changes": [],  # detailed log entries
    }

    async for pred in cursor:
        stats["scanned"] += 1
        direction = pred["direction"]
        price_at = float(pred.get("price_at_prediction") or 0)
        v = pred.get("verified_24h") or {}
        price_now = float(v.get("price") or 0)
        old_grade = v.get("grade")

        if price_at <= 0 or price_now <= 0 or not old_grade:
            stats["skipped_invalid"] += 1
            continue

        # Use the same tolerance the original verifier would have used now.
        try:
            tol = await _neutral_tolerance(pred["symbol"], window="24h")
        except Exception:
            tol = None

        new_grade = grade_prediction(
            direction=direction,
            price_at_prediction=price_at,
            price_now=price_now,
            window="24h",
            neutral_tolerance=tol,
        )

        # Bucket counters for the operator summary.
        if new_grade in {"STRONG_HIT", "WEAK_HIT"}:
            stats["regraded_to_hit"] += 1
        elif new_grade == "NEUTRAL":
            stats["regraded_to_neutral"] += 1
        elif new_grade == "WEAK_MISS":
            stats["regraded_to_weak_miss"] += 1
        else:
            stats["still_strong_miss"] += 1

        if new_grade == old_grade:
            # Real STRONG_MISS — leave alone. (Counted in still_strong_miss.)
            continue

        new_correct = _derive_correct(new_grade)
        # Failure metadata: clear it on HIT/NEUTRAL, recompute on remaining MISS.
        if new_grade in {"STRONG_HIT", "WEAK_HIT", "NEUTRAL"}:
            new_failure_code = None
            new_failure_reason = "N/A"
        else:
            new_failure_code = _classify_failure(direction, price_at, price_now)
            new_failure_reason = FAILURE_MODES.get(
                new_failure_code, FAILURE_MODES["UNKNOWN"]
            )

        change = {
            "run_id": run_id,
            "prediction_id": pred.get("prediction_id"),
            "symbol": pred["symbol"],
            "direction": direction,
            "feature": pred.get("feature"),
            "confidence": pred.get("confidence"),
            "entry_price": price_at,
            "verified_price": price_now,
            "old_grade": old_grade,
            "new_grade": new_grade,
            "old_correct": v.get("correct"),
            "new_correct": new_correct,
            "old_failure_code": v.get("failure_code"),
            "new_failure_code": new_failure_code,
            "neutral_tolerance_used": (
                round(tol, 4) if tol is not None else None
            ),
            "reason": BACKFILL_REASON,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
        }
        stats["changes"].append(change)

        if dry_run:
            continue

        # Apply: targeted partial update of the verified_24h sub-doc.
        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {
                "$set": {
                    "verified_24h.grade": new_grade,
                    "verified_24h.correct": new_correct,
                    "verified_24h.failure_code": new_failure_code,
                    "verified_24h.failure_reason": new_failure_reason,
                    "verified_24h.regraded_by": BACKFILL_REASON,
                    "verified_24h.regraded_at": change["applied_at"],
                    "verified_24h.regraded_run_id": run_id,
                    "verified_24h.original_grade_before_backfill": old_grade,
                }
            },
        )

    if not dry_run and stats["changes"]:
        await db[LOG_COLLECTION].insert_many(
            [{"_id": str(uuid4()), **c} for c in stats["changes"]]
        )

    # ── Operator summary ──────────────────────────────────────────────
    n_to_change = (
        stats["regraded_to_hit"]
        + stats["regraded_to_neutral"]
        + stats["regraded_to_weak_miss"]
    )
    logger.info("=" * 70)
    logger.info("Backfill summary (run_id=%s, dry_run=%s)", run_id, dry_run)
    logger.info("=" * 70)
    logger.info("Scanned                : %d", stats["scanned"])
    logger.info("→ Will flip to HIT     : %d", stats["regraded_to_hit"])
    logger.info("→ Will flip to NEUTRAL : %d", stats["regraded_to_neutral"])
    logger.info("→ Will flip to WEAK    : %d", stats["regraded_to_weak_miss"])
    logger.info("→ Stay STRONG_MISS     : %d", stats["still_strong_miss"])
    logger.info("→ Skipped (no anchor)  : %d", stats["skipped_invalid"])
    logger.info("=" * 70)
    if n_to_change == 0:
        logger.info("Nothing to do — all rows already grade correctly.")
    else:
        logger.info(
            "%s %d row%s.",
            "Would mutate" if dry_run else "Mutated",
            n_to_change,
            "s" if n_to_change != 1 else "",
        )
        if dry_run:
            logger.info(
                "Sample of pending changes (first 10):"
            )
            for c in stats["changes"][:10]:
                logger.info(
                    "  %-5s %-12s %-12s @ %s → %s : %s → %s",
                    c["symbol"],
                    c["direction"],
                    c["feature"] or "?",
                    c["entry_price"],
                    c["verified_price"],
                    c["old_grade"],
                    c["new_grade"],
                )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true",
                   help="Scan + log, change nothing.")
    g.add_argument("--apply", action="store_true",
                   help="Actually mutate Mongo (writes audit log).")
    parser.add_argument("--days", type=int, default=None,
                        help="Restrict to predictions verified in the last N days "
                             "(default: all-time on this collection).")
    args = parser.parse_args()

    run_id = f"bkfl_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid4().hex[:6]}"
    asyncio.run(run_backfill(dry_run=args.dry_run, days=args.days, run_id=run_id))


if __name__ == "__main__":
    main()
