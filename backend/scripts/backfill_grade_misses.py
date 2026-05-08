#!/usr/bin/env python3
"""Backfill: re-evaluate existing toxic/miss episodes under the new
volatility-aware tolerance rules, and normalise mixed-scale confidence
values in ChromaDB.

Run once (manually) after deploying the evaluator fix. Idempotent —
re-runs don't double-process rows (we tag each row with
`regraded_at` in the chroma metadata so we can skip on next run).

Usage:
    cd /app/backend && python3 -m scripts.backfill_grade_misses
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import Counter

from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill")


async def main() -> None:
    mc = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]

    # Lazy imports so logging shows the boot phase clearly.
    from services.market_memory_service import init_memory  # noqa: E402
    init_memory(db)
    from services.market_memory_service import _collection as chroma  # noqa: E402
    from services.prediction_tracker import (  # noqa: E402
        grade_prediction, normalize_confidence,
    )

    if chroma is None:
        log.error("ChromaDB not initialised — aborting")
        return

    log.info("=== Pass 1: Mongo predictions re-grade ===")
    # Predictions with `verified_24h.correct` but missing the new
    # `verified_24h.grade` → backfill the grade WITHOUT flipping
    # correct (keeps legacy dashboards stable; we only enrich).
    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$exists": True},
            "verified_24h.grade": {"$exists": False},
            "verified_24h.price": {"$gt": 0},
            "price_at_prediction": {"$gt": 0},
        },
        {"_id": 1, "direction": 1, "price_at_prediction": 1,
         "verified_24h.price": 1, "verified_24h.correct": 1},
    )
    grade_counts: Counter = Counter()
    updates = 0
    async for row in cursor:
        grade = grade_prediction(
            row["direction"],
            row["price_at_prediction"],
            row["verified_24h"]["price"],
            window="24h",
            neutral_tolerance=None,  # sync path → 0.25% floor
        )
        grade_counts[grade] += 1
        # For legacy rows that are now NEUTRAL under the new rules,
        # demote `correct: False` → `correct: None` so they stop
        # counting as misses in training/calibration queries.
        update = {"verified_24h.grade": grade}
        if grade == "NEUTRAL" and row["verified_24h"].get("correct") is False:
            update["verified_24h.correct"] = None
            update["verified_24h.regraded_at"] = "backfill_2026_02_20"
        await db.predictions.update_one({"_id": row["_id"]}, {"$set": update})
        updates += 1

    log.info(f"  → graded {updates} predictions. Distribution: {dict(grade_counts)}")
    demoted = await db.predictions.count_documents(
        {"verified_24h.regraded_at": "backfill_2026_02_20"}
    )
    log.info(f"  → {demoted} former 'miss' rows now NEUTRAL (no longer counted as failures)")

    log.info("\n=== Pass 2: ChromaDB confidence scale normalisation + regrade ===")
    # Fetch all episodes. ChromaDB doesn't support server-side filtering
    # with OR conditions well, so we pull everything and filter locally.
    all_rows = await asyncio.to_thread(
        chroma.get, include=["metadatas", "documents"]
    )
    ids = all_rows.get("ids", [])
    metas = all_rows.get("metadatas", []) or []
    docs = all_rows.get("documents", []) or []

    log.info(f"  → inspecting {len(metas)} chroma episodes")
    touched = 0
    demoted_miss = 0
    for doc_id, meta, text in zip(ids, metas, docs):
        if meta is None:
            continue
        new_meta = dict(meta)
        changed = False

        # 2a. Normalise confidence to 0-100.
        raw_conf = meta.get("confidence")
        norm_conf = normalize_confidence(raw_conf)
        if raw_conf != norm_conf:
            new_meta["confidence"] = norm_conf
            changed = True

        # 2b. Re-grade `outcome=miss` episodes that have price deltas
        # we can recover. Most training-path rows store only the
        # outcome (no price_at/price_now), so we can only upgrade
        # those whose metadata carries the necessary numbers. We
        # don't downgrade; we only UPGRADE miss → neutral when the
        # new tolerance rules say so, matching the "stop punishing
        # noise-day drift" intent.
        if meta.get("outcome") == "miss":
            pa = meta.get("price_at_prediction") or meta.get("price_at")
            pn = meta.get("price_now") or meta.get("price_then")
            direction = meta.get("direction") or meta.get("prediction")
            if pa and pn and direction:
                try:
                    g = grade_prediction(
                        str(direction), float(pa), float(pn),
                        window="24h", neutral_tolerance=None,
                    )
                    if g == "NEUTRAL":
                        new_meta["outcome"] = "neutral"
                        new_meta["grade"] = "NEUTRAL"
                        new_meta["regraded_at"] = "backfill_2026_02_20"
                        changed = True
                        demoted_miss += 1
                    elif g in ("STRONG_HIT", "WEAK_HIT"):
                        # Sanity check — if a historical 'miss' is
                        # now a clear hit, the original label was
                        # probably a data error. Upgrade it.
                        new_meta["outcome"] = "hit"
                        new_meta["grade"] = g
                        new_meta["regraded_at"] = "backfill_2026_02_20"
                        changed = True
                        demoted_miss += 1
                except (ValueError, TypeError):
                    pass

        if changed:
            await asyncio.to_thread(
                chroma.update,
                ids=[doc_id],
                metadatas=[new_meta],
                documents=[text],
            )
            touched += 1

    log.info(f"  → updated {touched} chroma episodes")
    log.info(f"  → {demoted_miss} 'miss' episodes re-graded to neutral/hit")

    log.info("\n=== Backfill complete ===")
    log.info("Next nightly_cleanup run should show dramatically fewer toxic matches.")


if __name__ == "__main__":
    asyncio.run(main())
