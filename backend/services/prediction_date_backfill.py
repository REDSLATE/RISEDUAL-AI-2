"""Nightly maintenance: backfill ``prediction_date`` on legacy rows.

Background
----------
The drift endpoint historically grouped Mongo rows by
``prediction_date`` — a denormalised ``YYYY-MM-DD`` string the
write path is supposed to populate alongside ``timestamp``. Older
rows (~101 of them in production) have ``prediction_date == None``
because the write path was added later. The drift endpoint now
falls back to ``to_iso_date(timestamp)`` for those rows, which
works but is wasted CPU on every dashboard fetch.

This backfill closes that gap permanently:

  * One pass per nightly cleanup tick.
  * Bounded at 5,000 rows per run so a fresh deploy with a giant
    backlog can't lock the cleanup window.
  * Idempotent: skips any row where ``prediction_date`` is already
    a string. Re-running on a clean DB is a no-op (1 indexed
    ``count_documents`` and zero writes).
  * Self-disabling: once the count of unfilled rows hits zero,
    the next call returns immediately with ``backfilled: 0``.

Failure isolation: any per-row exception is swallowed and counted
in ``skipped`` so a single corrupt timestamp can't halt the rest
of the sweep.
"""
from __future__ import annotations

import logging
from typing import Any

from services.datetime_utils import to_iso_date

logger = logging.getLogger(__name__)

# Cap per pass — protects nightly cleanup against runaway backlogs.
# At 5k rows × ~30 days of cleanup ticks the helper drains 150k
# rows before any operator intervention. Adjustable but rarely
# should need to be.
BACKFILL_MAX_ROWS_PER_PASS = 5_000


async def backfill_prediction_date(db: Any) -> dict[str, int]:
    """Fill ``prediction_date`` on legacy verified-prediction rows.

    Targets rows where:
      * ``prediction_date`` is missing or null, AND
      * ``timestamp`` is a usable string/datetime.

    Resolves the date via ``to_iso_date(timestamp)`` — the same
    helper Chroma's metadata uses, so the post-backfill bucket key
    matches the existing ChromaDB rows exactly.

    Returns a small summary the scheduler can log when ``backfilled
    > 0`` (quiet runs are skipped to avoid log noise).
    """
    if db is None:
        return {"backfilled": 0, "skipped": 0, "remaining": 0}

    query = {
        "$or": [
            {"prediction_date": {"$exists": False}},
            {"prediction_date": None},
        ],
        "timestamp": {"$exists": True, "$ne": None},
    }

    cursor = db.predictions.find(
        query, {"_id": 1, "timestamp": 1}
    ).limit(BACKFILL_MAX_ROWS_PER_PASS)

    backfilled = 0
    skipped = 0
    async for doc in cursor:
        try:
            iso = to_iso_date(doc.get("timestamp"))
            if not iso:
                # Garbage timestamp — skip rather than write an
                # empty string. ``to_iso_date`` returning None means
                # the value is genuinely unparseable, not just
                # tz-naive Mongo round-trip.
                skipped += 1
                continue
            await db.predictions.update_one(
                {"_id": doc["_id"]},
                {"$set": {"prediction_date": iso}},
            )
            backfilled += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            logger.warning(
                "[backfill_prediction_date] row failed _id=%s: %s",
                doc.get("_id"), exc,
            )

    # Query the residual count so the operator can see "still 47
    # rows pending" without having to inspect Mongo manually.
    try:
        remaining = await db.predictions.count_documents(query)
    except Exception:  # noqa: BLE001
        remaining = -1

    if backfilled > 0 or skipped > 0:
        logger.info(
            "[backfill_prediction_date] backfilled=%d skipped=%d remaining=%d",
            backfilled, skipped, remaining,
        )

    return {
        "backfilled": backfilled,
        "skipped": skipped,
        "remaining": remaining,
    }
