"""Orphaned-intent reaper — the operator's fail-visible invariant.

Contract
--------
Every intent created by ``alpha_day_trader`` MUST terminate as one of:

* ``BROKER_SUBMITTED``       — order handed to the broker adapter.
* ``EXECUTION_BLOCKED(code)`` — a downstream gate refused it (with a
  specific reason code, not the meta "executor_rejected" bucket).

An intent that lingers longer than the reaper window with neither
terminal outcome is *orphaned* — the pipeline lost track of it. That
must never happen silently. The reaper:

1. Sweeps ``alpha_intent_outcomes`` for intents older than the window
   with ``submitted != True`` and ``reject_reason IS NULL``.
2. Emits an ``execution_blocked`` observation with reason
   ``orphaned:no_terminal_event`` so it shows up in why-not-trade.
3. Backfills the outcome row so the intent stops being counted as
   in-flight.

Runs as a background task on the same interval as Alpha ticks. No
mutations to the trading path — pure observability recovery.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _reaper_window_seconds() -> int:
    """How stale an intent must be before we consider it orphaned.

    Longer than one full Alpha tick (currently 5 min) + the broker
    ack watchdog (5s) so a healthy intent has plenty of time to
    terminate normally.
    """
    try:
        v = int(os.environ.get("ALPHA_ORPHAN_REAPER_SECONDS") or 600)
    except (TypeError, ValueError):
        v = 600
    # Never sweep sub-minute intents — false positives from race conditions.
    return max(60, v)


async def sweep_orphaned_intents(db: Any) -> dict[str, Any]:
    """Backfill terminal outcomes for orphaned intents.

    Returns a summary the admin panel can render.
    """
    if db is None:
        return {"reaped": 0, "reason": "no_db"}

    window = _reaper_window_seconds()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)

    reaped = 0
    sample: list[dict[str, Any]] = []
    try:
        cursor = db.alpha_intent_outcomes.find({
            "created_at": {"$lt": cutoff},
            "submitted": {"$ne": True},
            "$or": [
                {"reject_reason": {"$exists": False}},
                {"reject_reason": None},
                {"reject_reason": ""},
            ],
        }).limit(200)
        orphans = await cursor.to_list(length=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[orphan-reaper] outcome scan failed: %s", exc)
        return {"reaped": 0, "reason": f"scan_error:{exc.__class__.__name__}"}

    for row in orphans:
        intent_id = row.get("intent_id") or ""
        setup_id = row.get("setup_id") or ""
        symbol = row.get("symbol") or ""
        try:
            await db.alpha_intent_outcomes.update_one(
                {"_id": row["_id"]},
                {"$set": {
                    "reject_reason": "orphaned:no_terminal_event",
                    "reaped_at": datetime.now(timezone.utc),
                }},
            )
            await db.alpha_observations.insert_one({
                "ts": datetime.now(timezone.utc),
                "setup_id": setup_id,
                "event": "execution_blocked",
                "payload": {
                    "symbol": symbol,
                    "stage": "orphan_reaper",
                    "reject_reason": "orphaned:no_terminal_event",
                    "intent_id": intent_id,
                    "age_seconds": int(
                        (datetime.now(timezone.utc) - (row.get("created_at") or cutoff))
                        .total_seconds()
                    ),
                },
            })
            reaped += 1
            if len(sample) < 5:
                sample.append({
                    "intent_id": intent_id,
                    "symbol": symbol,
                    "setup_id": setup_id,
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[orphan-reaper] reap of %s failed: %s", intent_id, exc)

    if reaped:
        logger.warning(
            "[orphan-reaper] backfilled %d orphaned intent(s) (window=%ds)",
            reaped, window,
        )
    return {"reaped": reaped, "window_seconds": window, "sample": sample}


__all__ = ["sweep_orphaned_intents"]
