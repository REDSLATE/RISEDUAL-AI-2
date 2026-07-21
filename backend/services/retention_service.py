"""Retention & Backlog Purge service.

Keeps Atlas breathing by evicting stale telemetry on a configurable
window. Executed live trades and the account/user surface are
NEVER touched — those are source-of-truth data.

Design notes:
- Every purge is idempotent and batch-capped. Callers who want a
  full drain re-issue the call while ``more_remains == True``.
- Timestamp fields vary (some ISO strings, some BSON datetimes).
  ``_build_cutoff_query`` handles both, matching whichever type the
  collection actually stores.
- The scheduler runs ``purge_backlog`` hourly with the same cap so
  a normal-load pod never accumulates more than one batch of
  eligible rows between sweeps.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Env-tunable knobs ────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def default_retention_hours() -> int:
    return _env_int("RISEDUAL_RETENTION_HOURS", 72)


def paper_retention_hours() -> int:
    return _env_int("RISEDUAL_RETENTION_PAPER_HOURS", 24)


def purge_batch_cap() -> int:
    return _env_int("RISEDUAL_RETENTION_PURGE_BATCH", 5000)


# ── Retention table ──────────────────────────────────────────────


@dataclass(frozen=True)
class RetentionRule:
    collection: str
    timestamp_field: str
    ttl_hours_env: str = ""       # "" → use default_retention_hours()
    preserve_filter: dict = field(default_factory=dict)  # rows matching this are NEVER purged
    label: str = ""

    @property
    def ttl_hours(self) -> int:
        if self.ttl_hours_env == "paper":
            return paper_retention_hours()
        return default_retention_hours()


# Only telemetry. Anything under ``PRESERVED_COLLECTIONS`` is a
# source of truth (real fills, user data, credentials) and is
# ineligible for purge — it never appears in this table.
RETENTION_RULES: list[RetentionRule] = [
    RetentionRule(
        collection="predictions",
        timestamp_field="timestamp",
        label="Signal predictions",
    ),
    RetentionRule(
        collection="day_trade_targets",
        timestamp_field="queued_at",
        label="Day-trade scanner candidates",
    ),
    RetentionRule(
        collection="paper_trades",
        timestamp_field="opened_at",
        ttl_hours_env="paper",
        preserve_filter={"status": {"$in": ["open", "active", "pending"]}},
        label="Paper trades (closed only)",
    ),
    RetentionRule(
        collection="mc2_heartbeats",
        timestamp_field="recorded_at_dt",
        label="MC2 heartbeats",
    ),
    RetentionRule(
        collection="mc2_contributions",
        timestamp_field="recorded_at_dt",
        label="MC2 contributions",
    ),
    RetentionRule(
        collection="mc2_stances",
        timestamp_field="recorded_at_dt",
        label="MC2 stances",
    ),
    RetentionRule(
        collection="mc2_intents",
        timestamp_field="recorded_at_dt",
        label="MC2 intents",
    ),
    RetentionRule(
        collection="signal_dispatcher_events",
        timestamp_field="created_at",
        label="Signal dispatcher events",
    ),
    RetentionRule(
        collection="alert_events",
        timestamp_field="created_at",
        label="Alert events",
    ),
]


# Collections that MUST NEVER be purged (executed trades + user data).
# Documented here so the operator can see the invariant in one place.
PRESERVED_COLLECTIONS = [
    "equity_live_trades",
    "trade_orders",
    "broker_connections",
    "users",
    "watchlists",
    "portfolio_snapshots",
    "portfolio_history",
]


# ── Cutoff query builder ─────────────────────────────────────────


def _cutoff_dt(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _build_cutoff_query(rule: RetentionRule) -> dict:
    """Build a filter that matches expired rows.

    Handles both ISO-string timestamps (``predictions.timestamp``) and
    BSON datetimes. We ``$or`` the two comparison forms so mixed
    collections still match cleanly. If the caller has a
    ``preserve_filter``, matching rows are excluded with ``$nor``.
    """
    cutoff_dt = _cutoff_dt(rule.ttl_hours)
    cutoff_iso = cutoff_dt.isoformat()
    ts = rule.timestamp_field
    expired_clause: dict[str, Any] = {
        "$or": [
            {ts: {"$lt": cutoff_dt}},
            {ts: {"$lt": cutoff_iso}},
        ],
    }
    if rule.preserve_filter:
        expired_clause["$nor"] = [rule.preserve_filter]
    return expired_clause


# ── Status ───────────────────────────────────────────────────────


async def get_retention_status(db: Any) -> dict:
    """Report per-collection totals + expired backlog + last purge.

    Cheap enough for a UI-polled endpoint: uses ``estimated_document_count``
    for totals and a bounded ``count_documents`` (with cap+1 limit) for
    backlog so we return ``>= batch_cap`` when a collection is deeply
    saturated without paying for a full count.
    """
    cap = purge_batch_cap()
    breakdown = []
    total_backlog = 0
    for rule in RETENTION_RULES:
        try:
            total = await db[rule.collection].estimated_document_count()
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[retention] estimated_count {rule.collection}: {exc}")
            total = 0
        try:
            # cap+1 so the UI can render ">= cap" vs an exact tail count.
            backlog = await db[rule.collection].count_documents(
                _build_cutoff_query(rule), limit=cap + 1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[retention] count expired {rule.collection}: {exc}")
            backlog = 0
        total_backlog += backlog
        breakdown.append({
            "collection": rule.collection,
            "label": rule.label,
            "ttl_hours": rule.ttl_hours,
            "total": int(total),
            "expired_backlog": int(backlog),
            "backlog_capped_at": bool(backlog > cap),
            "timestamp_field": rule.timestamp_field,
            "preserves_active": bool(rule.preserve_filter),
        })

    last_log = await db.retention_purge_log.find_one(
        sort=[("finished_at", -1)],
        projection={"_id": 0},
    )
    lifetime_agg = await db.retention_purge_log.aggregate([
        {"$group": {"_id": None, "total": {"$sum": "$purged_total"}}},
    ]).to_list(length=1)
    lifetime_total = int(lifetime_agg[0]["total"]) if lifetime_agg else 0

    return {
        "retention_hours_default": default_retention_hours(),
        "retention_hours_paper": paper_retention_hours(),
        "purge_batch_cap": cap,
        "total_backlog": int(total_backlog),
        "more_remains": bool(total_backlog > cap),
        "breakdown": breakdown,
        "preserved_collections": PRESERVED_COLLECTIONS,
        "last_purge": last_log,
        "total_purged_lifetime": lifetime_total,
    }


# ── Purge ────────────────────────────────────────────────────────


async def purge_backlog(db: Any, *, triggered_by: str = "manual") -> dict:
    """Delete up to ``purge_batch_cap`` expired rows across all rules.

    Iterates each rule in table order, deletes up to the remaining
    budget, and stops when the budget is exhausted or every rule
    reports no backlog. Emits one log line per collection touched.

    Args:
        triggered_by: "manual" | "scheduler" — recorded in the audit
            log so the operator can see whether the last drain was
            operator-driven or the hourly sweeper.
    """
    cap = purge_batch_cap()
    budget = cap
    started_at = datetime.now(timezone.utc)
    per_collection: list[dict] = []
    more_remains = False

    for rule in RETENTION_RULES:
        if budget <= 0:
            more_remains = True
            per_collection.append({
                "collection": rule.collection,
                "deleted": 0,
                "skipped_reason": "batch_budget_exhausted",
            })
            continue
        query = _build_cutoff_query(rule)
        try:
            # Motor doesn't support delete_many with a limit, so we
            # collect a bounded batch of _ids first, then delete by id.
            cursor = db[rule.collection].find(query, projection={"_id": 1}).limit(budget)
            ids = [doc["_id"] async for doc in cursor]
            if not ids:
                per_collection.append({
                    "collection": rule.collection,
                    "deleted": 0,
                })
                continue
            res = await db[rule.collection].delete_many({"_id": {"$in": ids}})
            deleted = int(res.deleted_count)
            budget -= deleted
            per_collection.append({
                "collection": rule.collection,
                "deleted": deleted,
                "ttl_hours": rule.ttl_hours,
            })
            logger.info(
                f"[retention] purged {deleted} from {rule.collection} "
                f"(ttl={rule.ttl_hours}h, budget_left={budget})",
            )
            # If we hit the requested limit exactly, more may remain
            # in this collection.
            if len(ids) >= budget + deleted and deleted > 0:
                more_remains = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[retention] purge {rule.collection} failed: {exc}")
            per_collection.append({
                "collection": rule.collection,
                "deleted": 0,
                "error": str(exc)[:200],
            })

    # After the budgeted pass, do one cheap check: is there still
    # any expired row anywhere?  If so, flag more_remains=True so the
    # UI shows the "click again" affordance even when the budget
    # wasn't fully consumed (e.g., an error skipped a big collection).
    if not more_remains:
        for rule in RETENTION_RULES:
            try:
                probe = await db[rule.collection].find_one(
                    _build_cutoff_query(rule), projection={"_id": 1},
                )
                if probe is not None:
                    more_remains = True
                    break
            except Exception:  # noqa: BLE001
                continue

    finished_at = datetime.now(timezone.utc)
    purged_total = sum(entry.get("deleted", 0) for entry in per_collection)
    log_doc = {
        "triggered_by": triggered_by,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "purged_total": purged_total,
        "more_remains": more_remains,
        "batch_cap": cap,
        "per_collection": per_collection,
    }
    try:
        await db.retention_purge_log.insert_one(log_doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[retention] audit log insert failed: {exc}")

    return {
        "purged_total": purged_total,
        "more_remains": more_remains,
        "batch_cap": cap,
        "duration_ms": log_doc["duration_ms"],
        "per_collection": per_collection,
        "triggered_by": triggered_by,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }


# ── Scheduler entrypoint ─────────────────────────────────────────


async def run_hourly_purge(db: Any) -> dict:
    """Scheduler hook — one budgeted sweep per hour."""
    return await purge_backlog(db, triggered_by="scheduler")
