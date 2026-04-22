"""Paper-trading progress service — derives the canonical `live_days`
count from the actual `paper_trades` collection instead of the static
`RISEDUAL_LIVE_DAYS` env var.

ML Tier 3 (live execution) unlocks only after ≥30 days of paper
trading. Previously the orchestrator read `RISEDUAL_LIVE_DAYS` from
the environment — a manually-maintained number that drifts out of
sync with reality. This service computes the count from ground truth:
distinct UTC calendar dates on which at least one paper trade was
opened, bounded by the first trade's date and today.

Schema note: `paper_trades` is populated by TWO writers —

  1. `services.ml_paper_trader.maybe_paper_trade` (the ML
     orchestrator, which is what the Tier 3 gate cares about).
     Writes BSON-date `opened_at`.
  2. The manual paper-trading UI (`routes/paper_trading.py`), which
     writes ISO-string `timestamp` and omits `opened_at`.

We ONLY count rows with a BSON-date `opened_at`. This is
deliberate: the gate measures "days the ML pipeline auto-traded",
not "days a human clicked Buy". Don't broaden the filter without
changing the gate semantics.

Design rules:
  * Fails CLOSED: any exception returns 0 days. A broken counter
    that accidentally reports 30+ would skip the safety gate.
  * UTC only. `paper_trades.opened_at` is stored as
    `datetime.now(timezone.utc)` — we count distinct UTC dates.
  * Pure-read. This service never writes. Safe to call from any hot
    path (orchestrator, admin UI, cron).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Tier 3 unlock threshold. Kept in lock-step with
# `risedual_core.ml.calibration._T3_MIN_LIVE_DAYS` — if that ever
# changes, update here too (there's a test that pins them together).
TIER3_MIN_LIVE_DAYS: int = 30


async def compute_live_days(db: Any) -> int:
    """Count distinct UTC calendar dates with ≥1 `paper_trades` row.

    Returns 0 on any error. Caller may fall back to the
    `RISEDUAL_LIVE_DAYS` env var override via
    :func:`resolve_live_days` if they want manual control.
    """
    if db is None:
        return 0
    try:
        pipeline = [
            {"$match": {"opened_at": {"$type": "date"}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$opened_at",
                            "timezone": "UTC",
                        }
                    }
                }
            },
            {"$count": "days"},
        ]
        cursor = db["paper_trades"].aggregate(pipeline)
        doc = await cursor.to_list(length=1)
        return int(doc[0]["days"]) if doc else 0
    except Exception as exc:
        logger.warning("[tier3] paper-day count failed: %s", exc)
        return 0


async def resolve_live_days(db: Any) -> int:
    """Resolve the effective `live_days` to feed into the gate.

    Precedence:
      1. `RISEDUAL_LIVE_DAYS` env var (manual override, for testing
         or backfilling — same as existing orchestrator behaviour).
      2. DB-derived count from `paper_trades`.
    """
    override = os.getenv("RISEDUAL_LIVE_DAYS")
    if override is not None and override.strip():
        try:
            return int(override)
        except ValueError:
            logger.warning(
                "[tier3] ignoring non-numeric RISEDUAL_LIVE_DAYS=%r", override
            )
    return await compute_live_days(db)


async def tier3_progress(db: Any) -> dict:
    """Full Tier-3 accumulation snapshot — the payload the admin
    dashboard uses to render the 30-day progress badge.

    Returns a plain dict (no ObjectIds) with:
      * `days`             — distinct UTC calendar days with trades
      * `target_days`      — TIER3_MIN_LIVE_DAYS (30)
      * `remaining_days`   — max(target - days, 0)
      * `progress_pct`     — 0-100 int
      * `unlocked`         — days >= target
      * `first_trade_at`   — ISO string of earliest `opened_at`
      * `last_trade_at`    — ISO string of latest `opened_at`
      * `total_trades`     — total count of paper_trades rows
      * `window_days`      — calendar span first→last (for sparsity)
      * `override_env`     — RISEDUAL_LIVE_DAYS override string if set
      * `generated_at`     — ISO timestamp
    """
    snapshot: dict[str, Any] = {
        "days": 0,
        "target_days": TIER3_MIN_LIVE_DAYS,
        "remaining_days": TIER3_MIN_LIVE_DAYS,
        "progress_pct": 0,
        "unlocked": False,
        "first_trade_at": None,
        "last_trade_at": None,
        "total_trades": 0,
        "window_days": 0,
        "override_env": os.getenv("RISEDUAL_LIVE_DAYS") or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if db is None:
        return snapshot
    try:
        # Effective days (respects env override for parity with orchestrator)
        effective = await resolve_live_days(db)
        # Raw count from DB (for display even if override is set)
        db_days = await compute_live_days(db)

        total = await db["paper_trades"].count_documents({})

        first_doc = await db["paper_trades"].find_one(
            {"opened_at": {"$type": "date"}},
            sort=[("opened_at", 1)],
            projection={"_id": 0, "opened_at": 1},
        )
        last_doc = await db["paper_trades"].find_one(
            {"opened_at": {"$type": "date"}},
            sort=[("opened_at", -1)],
            projection={"_id": 0, "opened_at": 1},
        )

        first_at = first_doc.get("opened_at") if first_doc else None
        last_at = last_doc.get("opened_at") if last_doc else None
        window = 0
        if isinstance(first_at, datetime) and isinstance(last_at, datetime):
            window = (last_at - first_at).days + 1

        snapshot.update(
            {
                "days": int(effective),
                "db_days": int(db_days),
                "remaining_days": max(TIER3_MIN_LIVE_DAYS - int(effective), 0),
                "progress_pct": min(
                    100,
                    int(round(100 * int(effective) / TIER3_MIN_LIVE_DAYS)),
                ),
                "unlocked": int(effective) >= TIER3_MIN_LIVE_DAYS,
                "first_trade_at": first_at.isoformat() if isinstance(first_at, datetime) else None,
                "last_trade_at": last_at.isoformat() if isinstance(last_at, datetime) else None,
                "total_trades": int(total),
                "window_days": int(window),
            }
        )
    except Exception as exc:
        logger.warning("[tier3] progress snapshot failed: %s", exc)

    # ECO — also report last-7-days activity so admins can see if
    # accumulation is stalling (bots dead, market holiday streak, etc.)
    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        pipeline = [
            {"$match": {"opened_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$opened_at",
                            "timezone": "UTC",
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        cursor = db["paper_trades"].aggregate(pipeline)
        snapshot["last_7_days"] = [
            {"date": row["_id"], "count": int(row["count"])}
            async for row in cursor
        ]
    except Exception as exc:
        logger.warning("[tier3] 7-day activity lookup failed: %s", exc)
        snapshot["last_7_days"] = []

    return snapshot
