"""
Notification Lifecycle Service.

Why this exists
───────────────
Toxic-spike notifications were stamped on the day they fired but
had no concept of being *resolved*. When the underlying predictions
got regraded (e.g., via ``backfill_strong_direction_grades``), the
"miss" outcomes that originally triggered the toxic spike vanished
— but the notifications stayed on the user's drawer forever,
creating phantom alerts that no longer reflected reality.

This module gives every toxic-spike notification three states:

    active → superseded | resolved | dismissed

* **active**   → no terminal status set; alert is current.
* **superseded** → source predictions no longer have miss outcomes
                   (regraded after the alert fired).
* **resolved** → operator manually cleared.
* **dismissed** → operator dismissed without resolving.

The drawer API already filters to ``resolved != True``, so any
notification this module supersedes / resolves / dismisses
disappears from the UI on the next poll.

Pure read-only WRT predictions. The only collection it mutates is
``notifications``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Outcome strings that count as "this row is still a miss". Covers
# both the legacy ``outcome`` field and the newer
# ``verified_24h.outcome`` shape used by the regrade backfill.
MISS_OUTCOMES: frozenset[str] = frozenset({
    "miss", "MISS", "STRONG_MISS", "WEAK_MISS",
})


async def _count_remaining_misses_for_alert(
    db: Any, alert_meta: dict[str, Any],
) -> int:
    """Re-evaluate the source predictions that triggered a toxic-
    spike alert. Returns the number that are STILL miss-graded.

    Looks up symbols + the original alert window (when available)
    and counts predictions in EITHER schema:
      * legacy: ``outcome ∈ {miss, MISS, STRONG_MISS, WEAK_MISS}``
      * new:    ``verified_24h.outcome ∈ {STRONG_MISS, WEAK_MISS}``
    """
    if db is None:
        return 0

    tickers = alert_meta.get("affected_tickers") or alert_meta.get("symbols") or []
    if not tickers:
        return 0

    miss_list = list(MISS_OUTCOMES)
    base_or = [
        {"outcome": {"$in": miss_list}},
        {"verified_24h.outcome": {"$in": miss_list}},
    ]
    query: dict[str, Any] = {
        "symbol": {"$in": list(tickers)},
        "$or": base_or,
    }

    # Apply the original alert window when stored, so refined
    # predictions falling outside don't keep the alert alive.
    episode_dates = alert_meta.get("episode_dates") or []
    if episode_dates:
        try:
            parsed: list[datetime] = []
            for d in episode_dates:
                if isinstance(d, datetime):
                    parsed.append(d)
                elif isinstance(d, str):
                    parsed.append(
                        datetime.fromisoformat(d.replace("Z", "+00:00"))
                    )
            if parsed:
                query["created_at"] = {
                    "$gte": min(parsed), "$lte": max(parsed),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[notif-lifecycle] window parse skipped: %s", exc)

    try:
        return int(await db.predictions.count_documents(query))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[notif-lifecycle] count failed: %s", exc)
        # Conservative: if we can't verify, leave the alert alone.
        return 1


async def supersede_stale_toxic_alerts(db: Any) -> dict[str, int]:
    """Walk every active toxic-spike notification, supersede those
    whose source predictions no longer have miss outcomes.

    Returns ``{checked, superseded, kept_active}``. Idempotent —
    re-running after a clean pass is a no-op (already-superseded
    rows are skipped via the status filter).
    """
    if db is None:
        return {"checked": 0, "superseded": 0, "kept_active": 0}

    now = datetime.now(timezone.utc)
    checked = 0
    superseded = 0
    kept_active = 0

    query = {
        "type": {"$in": ["toxic_spike", "toxic_spikes", "TOXIC_SPIKES"]},
        "$and": [
            {"$or": [{"resolved": {"$exists": False}},
                     {"resolved": False}]},
            {"$or": [{"status": {"$exists": False}},
                     {"status": {"$nin": [
                         "resolved", "superseded", "dismissed",
                     ]}}]},
        ],
    }

    async for alert in db.notifications.find(query):
        checked += 1
        meta = alert.get("metadata") or {}
        # Promote schema-tolerant field reads from either the
        # metadata block (current writer) or the top-level alert
        # (matches user's spec).
        alert_view = {
            "affected_tickers": (
                meta.get("affected_tickers")
                or alert.get("tickers")
                or alert.get("symbols")
            ),
            "symbols": meta.get("symbols") or alert.get("symbols"),
            "episode_dates": (
                meta.get("episode_dates") or alert.get("episode_dates")
            ),
        }

        remaining = await _count_remaining_misses_for_alert(db, alert_view)
        if remaining == 0:
            try:
                await db.notifications.update_one(
                    {"_id": alert["_id"]},
                    {"$set": {
                        "status": "superseded",
                        "resolved": True,
                        "resolved_at": now,
                        "resolved_reason":
                            "source_predictions_regraded_no_remaining_misses",
                    }},
                )
                superseded += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[notif-lifecycle] update failed for %s: %s",
                    alert.get("_id"), exc,
                )
        else:
            kept_active += 1

    return {
        "checked": checked,
        "superseded": superseded,
        "kept_active": kept_active,
    }
