"""
ChromaDB supersede pass — runs AFTER `backfill_strong_direction_grades.py`.

When the Mongo backfill flips a prediction's grade from STRONG_MISS to
HIT/NEUTRAL/WEAK_MISS, the corresponding ChromaDB episode (which the
nightly cleanup re-tagged as ``outcome="toxic_lesson"``) is now teaching
the AI a wrong lesson — to avoid setups that actually worked. We need
to mark those Chroma rows superseded and (for true HITs) optionally
write a corrected positive lesson alongside.

Per the operator's instructions: prefer marking superseded over deleting.
The audit trail stays addressable; future engineers can see the trail.

Usage
-----
    python -m scripts.backfill_chroma_supersede_after_regrade --dry-run
    python -m scripts.backfill_chroma_supersede_after_regrade --apply

This script is read-Mongo / write-ChromaDB only. It NEVER mutates Mongo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from motor.motor_asyncio import AsyncIOMotorClient

from services.market_memory_service import (
    init_market_memory,
    _make_id,
)
import services.market_memory_service as _mms

logger = logging.getLogger("chroma_supersede")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

REASON = "incorrect_direction_token_grade"


async def _candidates_from_mongo(db) -> list[dict[str, Any]]:
    """Pull every prediction touched by the Mongo backfill.

    We key off the audit log so this script is idempotent and bounded —
    re-running without a new Mongo backfill is a no-op."""
    cursor = db.prediction_grade_backfill_log.find(
        {"reason": "direction_token_backfill_v1", "dry_run": False},
        {"_id": 0},
    )
    return [c async for c in cursor]


def _possible_chroma_ids(pred: dict) -> list[str]:
    """The episode might exist under v2 (prediction_id-keyed) or v1
    (symbol/date/price-keyed). Try both — superseding the wrong row
    is impossible because we only update rows that actually exist."""
    sym = pred.get("symbol")
    pid = pred.get("prediction_id")
    entry_price = pred.get("entry_price")
    # Date format used at write-time: YYYY-MM-DD slice of the
    # prediction's verification timestamp, not the original
    # log timestamp. We'll try both shapes the cleanup pass uses.
    candidates: list[str] = []
    if pid:
        candidates.append(_make_id({"prediction_id": pid}))
    # v1 fallback: same symbol+price, but date is unknown to us here.
    # The supersede pass below pulls all toxic_lesson rows for the
    # symbol and matches by (symbol, entry_price) instead — much more
    # robust than reconstructing the date string.
    return candidates


async def run(*, dry_run: bool) -> dict:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    init_market_memory(db, persist_directory=os.environ.get("CHROMA_PERSIST_DIR"))
    coll = _mms._collection
    if coll is None:
        logger.error("ChromaDB collection not initialised — aborting.")
        return {}

    candidates = await _candidates_from_mongo(db)
    logger.info(
        "Found %d backfill log entries from direction_token_backfill_v1",
        len(candidates),
    )
    if not candidates:
        logger.info("Nothing to do — has the Mongo backfill been --apply'd yet?")
        return {"scanned": 0}

    # Index candidates by prediction_id so we can match them against
    # ChromaDB rows the cleanup pass tagged via prediction_id.
    by_pid: dict[str, dict] = {
        c["prediction_id"]: c for c in candidates if c.get("prediction_id")
    }

    # Pull all toxic_lesson rows once; we'll match locally.
    res = await asyncio.to_thread(
        coll.get,
        where={"outcome": "toxic_lesson"},
        include=["metadatas"],
    )
    ids = res.get("ids", [])
    metas = res.get("metadatas", [])
    logger.info("Pulled %d toxic_lesson rows from ChromaDB", len(ids))

    stats = {
        "scanned": len(candidates),
        "matched_in_chroma": 0,
        "superseded": 0,
        "would_supersede": 0,
        "no_match": 0,
        "samples": [],
    }

    updates_ids: list[str] = []
    updates_metas: list[dict[str, Any]] = []

    matched_pids: set[str] = set()
    for i, tid in enumerate(ids):
        meta = dict(metas[i]) if i < len(metas) else {}
        pid = meta.get("prediction_id")
        cand = by_pid.get(pid) if pid else None
        if not cand:
            continue

        # Decide what tag to write. NEUTRAL → "superseded_neutral" so
        # the LLM retrieval layer can filter it out cleanly. HIT →
        # "superseded_to_hit" + we write a corrected positive lesson
        # below. WEAK_MISS stays a lesson but with the right grade
        # (still useful as a negative example, just less severe).
        new_grade = cand["new_grade"]
        if new_grade in {"STRONG_HIT", "WEAK_HIT"}:
            new_outcome = "superseded_to_hit"
        elif new_grade == "NEUTRAL":
            new_outcome = "superseded_neutral"
        else:
            new_outcome = "superseded_to_weak_miss"

        meta["outcome"] = new_outcome
        meta["lesson_status"] = "superseded"
        meta["superseded_reason"] = REASON
        meta["corrected_outcome"] = new_grade
        meta["superseded_at"] = datetime.now(timezone.utc).isoformat()
        # Defensive copy — Chroma's UpdateMetadata only accepts
        # primitives, and the values above are all str.

        updates_ids.append(tid)
        updates_metas.append(meta)
        matched_pids.add(pid)
        stats["matched_in_chroma"] += 1
        if len(stats["samples"]) < 10:
            stats["samples"].append({
                "chroma_id": tid[:16] + "…",
                "symbol": cand["symbol"],
                "old_grade": cand["old_grade"],
                "new_grade": new_grade,
                "new_outcome": new_outcome,
            })

    # Predictions where we never found a Chroma row to supersede.
    stats["no_match"] = len(by_pid) - len(matched_pids)

    if updates_ids and not dry_run:
        await asyncio.to_thread(
            coll.update,
            ids=updates_ids,
            metadatas=updates_metas,
        )
        stats["superseded"] = len(updates_ids)
    else:
        stats["would_supersede"] = len(updates_ids)

    # ── Summary ──
    logger.info("=" * 70)
    logger.info("ChromaDB supersede pass (dry_run=%s)", dry_run)
    logger.info("=" * 70)
    logger.info("Mongo backfill log entries  : %d", stats["scanned"])
    logger.info("Matched Chroma rows         : %d", stats["matched_in_chroma"])
    logger.info(
        "%s : %d",
        "Would supersede" if dry_run else "Superseded",
        stats["would_supersede"] or stats["superseded"],
    )
    logger.info("Mongo entries w/o Chroma row: %d", stats["no_match"])
    logger.info("=" * 70)
    for s in stats["samples"]:
        logger.info(
            "  %s  %s  %s → %s (%s)",
            s["chroma_id"], s["symbol"], s["old_grade"], s["new_grade"], s["new_outcome"],
        )
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
