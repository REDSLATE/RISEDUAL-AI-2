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

This module gives every supported notification type three states:

    active → superseded | resolved | dismissed

* **active**     → no terminal status set; alert is current.
* **superseded** → source rows no longer support the alert (e.g.,
                   predictions regraded, verdict flipped again).
* **resolved**   → operator manually cleared.
* **dismissed**  → operator dismissed without resolving.

The drawer API filters out anything with ``resolved=True`` /
``status ∈ {resolved, superseded, dismissed}``, so any notification
this module supersedes disappears from the UI on the next poll.

Pure read-only WRT the source-of-truth collections (``predictions``,
``hypotheses`` /``ai_decisions``, etc.). The only collection it
mutates is ``notifications``.

Per-type contract
─────────────────
Each supported notification type registers a coroutine
``supersede_stale_<type>(db) -> {checked, superseded, kept_active}``
that recounts source rows and supersedes any alert whose source
no longer supports it. The dispatcher ``supersede_stale_alerts(db)``
calls every registered superseder in turn and returns a per-type
breakdown.

Currently registered:

    * ``toxic_spike``       — superseded when source predictions
                              are no longer miss-graded (legacy
                              ``outcome`` OR ``verified_24h.outcome``)
    * ``verdict_change``    — superseded when the symbol's current
                              verdict no longer equals the alert's
                              ``new_verdict`` (i.e., flipped again
                              since the alert fired)

Adding a new type is a 5-line registration — see ``_REGISTRY`` at
the bottom of this file.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Outcome strings that count as "this row is still a miss". Covers
# both the legacy ``outcome`` field and the newer
# ``verified_24h.outcome`` shape used by the regrade backfill.
MISS_OUTCOMES: frozenset[str] = frozenset({
    "miss", "MISS", "STRONG_MISS", "WEAK_MISS",
})


# ── Generic helpers ──────────────────────────────────────────────


def _active_status_filter() -> dict[str, Any]:
    """The ``$and`` clause every superseder uses to find rows still
    in the ``active`` state. Keeps the lifecycle filter consistent
    across types."""
    return {
        "$and": [
            {"$or": [{"resolved": {"$exists": False}},
                     {"resolved": False}]},
            {"$or": [{"status": {"$exists": False}},
                     {"status": {"$nin": [
                         "resolved", "superseded", "dismissed",
                     ]}}]},
        ],
    }


async def _mark_superseded(
    db: Any, alert_id: Any, *, reason: str,
) -> bool:
    """Stamp the terminal status fields. Returns True on success."""
    if db is None:
        return False
    try:
        await db.notifications.update_one(
            {"_id": alert_id},
            {"$set": {
                "status": "superseded",
                "resolved": True,
                "resolved_at": datetime.now(timezone.utc),
                "resolved_reason": reason,
            }},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[notif-lifecycle] update failed for %s: %s",
                       alert_id, exc)
        return False


# ── toxic_spike superseder ───────────────────────────────────────


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
    whose source predictions no longer have miss outcomes."""
    if db is None:
        return {"checked": 0, "superseded": 0, "kept_active": 0}

    checked = superseded = kept_active = 0
    query = {
        "type": {"$in": ["toxic_spike", "toxic_spikes", "TOXIC_SPIKES"]},
        **_active_status_filter(),
    }
    async for alert in db.notifications.find(query):
        checked += 1
        meta = alert.get("metadata") or {}
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
            ok = await _mark_superseded(
                db, alert["_id"],
                reason="source_predictions_regraded_no_remaining_misses",
            )
            if ok:
                superseded += 1
                continue
        kept_active += 1

    return {
        "checked": checked,
        "superseded": superseded,
        "kept_active": kept_active,
    }


# ── verdict_change superseder ────────────────────────────────────


async def _current_verdict_for(db: Any, symbol: str) -> str | None:
    """Most recent verdict for ``symbol`` from ``hypotheses``.

    Reads the same source the verdict-change notifier reads at
    write time, so a verdict that has flipped AGAIN since the
    notification fired will be detected. Returns ``None`` when no
    hypothesis exists (in which case we leave the alert alone —
    can't safely supersede without proof of a flip).
    """
    if db is None or not symbol:
        return None
    try:
        row = await db.hypotheses.find_one(
            {"symbol": symbol.upper()},
            {"verdict": 1, "_id": 0},
            sort=[("created_at", -1)],
        )
        if row:
            return row.get("verdict")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[notif-lifecycle] verdict lookup failed: %s", exc)
    return None


async def supersede_stale_verdict_changes(db: Any) -> dict[str, int]:
    """A verdict_change notification is meaningful only while the
    symbol's verdict is still ``new_verdict``. Once the verdict has
    flipped again, the original alert is stale — the next
    verdict_change notification has the up-to-date signal."""
    if db is None:
        return {"checked": 0, "superseded": 0, "kept_active": 0}

    checked = superseded = kept_active = 0
    query = {
        "type": "verdict_change",
        **_active_status_filter(),
    }
    async for alert in db.notifications.find(query):
        checked += 1
        symbol = (alert.get("symbol") or "").upper()
        new_verdict = alert.get("new_verdict")
        if not symbol or not new_verdict:
            kept_active += 1
            continue

        current = await _current_verdict_for(db, symbol)
        # Supersede ONLY when we have evidence of a NEW flip:
        # current verdict exists AND differs from the alert's
        # ``new_verdict``. Missing data leaves the alert alone.
        if current is not None and current != new_verdict:
            ok = await _mark_superseded(
                db, alert["_id"],
                reason="verdict_flipped_again",
            )
            if ok:
                superseded += 1
                continue
        kept_active += 1

    return {
        "checked": checked,
        "superseded": superseded,
        "kept_active": kept_active,
    }


# ── Generic dispatcher ───────────────────────────────────────────


SupersederFn = Callable[[Any], Awaitable[dict[str, int]]]

# Registry of all per-type superseders. Adding a new notification
# type with its own lifecycle is a single line here.
_REGISTRY: dict[str, SupersederFn] = {
    "toxic_spike": supersede_stale_toxic_alerts,
    "verdict_change": supersede_stale_verdict_changes,
}


async def supersede_stale_alerts(
    db: Any, *, types: list[str] | None = None,
    trigger: str = "unknown",
) -> dict[str, Any]:
    """Run every registered superseder (or a subset). Returns a
    per-type breakdown plus a totals row.

    Idempotent: each individual superseder is idempotent, so the
    dispatcher inherits that property.

    Every run writes one receipt row to ``notification_lifecycle_runs``
    capturing ``ran_at``, ``types``, totals, and the ``trigger``
    string the caller passed (``manual_admin`` /
    ``backfill_regrade`` / ``oneshot_script`` / ``unknown``). Lets
    the operator audit "no stale alerts? when did we last check?"
    """
    target_types = types or list(_REGISTRY.keys())
    by_type: dict[str, dict[str, int]] = {}
    totals = {"checked": 0, "superseded": 0, "kept_active": 0}

    for t in target_types:
        fn = _REGISTRY.get(t)
        if fn is None:
            logger.debug("[notif-lifecycle] unknown type skipped: %s", t)
            continue
        result = await fn(db)
        by_type[t] = result
        for k in totals:
            totals[k] += result.get(k, 0)

    # Receipt — best-effort. Failure to log doesn't roll back the
    # supersede work that already landed.
    if db is not None:
        try:
            await db.notification_lifecycle_runs.insert_one({
                "ran_at": datetime.now(timezone.utc),
                "types": target_types,
                "checked": totals["checked"],
                "superseded": totals["superseded"],
                "kept_active": totals["kept_active"],
                "by_type": by_type,
                "trigger": trigger,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[notif-lifecycle] receipt write failed: %s", exc)

    return {"by_type": by_type, "totals": totals}


def lifecycle_defaults() -> dict[str, Any]:
    """Helper for new-notification writers. Stamps
    ``status="active"`` + ``resolved=False`` so every fresh row is
    on the lifecycle contract from day one."""
    return {"status": "active", "resolved": False}


def register_superseder(notification_type: str, fn: SupersederFn) -> None:
    """Public extension point — let other modules register their
    own superseders without editing this file. Keeps the lifecycle
    contract open for future notification types."""
    _REGISTRY[notification_type] = fn
