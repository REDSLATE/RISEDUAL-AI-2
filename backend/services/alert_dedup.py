"""Alert deduplication — prevents the same alert from firing repeatedly.

The canonical pattern:
    alert_id = sha256(type + sorted(tickers) + date_bucket)
    if already_alerted_within(48h): skip
    else: record_alert() and send it

Why sha256: deterministic, collision-resistant, no "hash changes when
Python switches to a new seed" gotcha that plain `hash()` would hit
across restarts. Previously MD5; swapped to SHA-256 for hygiene even
though collision risk at our volume is non-existent either way.

Why 48h: gives the nightly cleanup room to run twice without
alerting twice. Longer windows risk hiding genuinely-different spikes;
shorter windows let the original bug (3 alerts in 3 nights) re-emerge.

Additional feature: `persisting_runs` counter. When an alert IS a repeat
(same tickers within 7 days), we increment a counter on the original
record and use it to escalate the next eligible alert's subject to
"Persisting (N days in a row)". So the user gets 1 alert at day-1, is
silent at day-2, day-3 cleanup runs → next eligible fire after 48h
upgrades to "Persisting (3 days in a row)" instead of spamming.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_COLLECTION = "alerts_sent"

# How long before the SAME (type, tickers, date_bucket) can fire again.
SUPPRESS_WINDOW_HOURS = 48

# How long we keep counting "persistence". An alert for the same
# (type, tickers) inside this window bumps the persistence counter but
# may still be suppressed by the 48h window — the counter is read by
# callers that want to label escalations.
PERSISTENCE_WINDOW_DAYS = 7


def compute_alert_id(alert_type: str, tickers: list[str], date_bucket: str) -> str:
    """Deterministic alert id.

    `date_bucket` should be a stable-per-day string like
    `YYYY-MM-DD` for daily alerts. If the caller passes something
    finer-grained (like a timestamp) the bucket is effectively unique
    and dedup will NEVER fire — caller bug, not our bug.
    """
    key = f"{alert_type}-{sorted(t.upper() for t in tickers)}-{date_bucket}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def date_bucket_today() -> str:
    """UTC YYYY-MM-DD — use this unless you have a specific reason not to."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def already_alerted(db: Any, alert_id: str) -> bool:
    """True if this exact alert_id fired within the suppress window."""
    if db is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SUPPRESS_WINDOW_HOURS)
    doc = await db[_COLLECTION].find_one(
        {"alert_id": alert_id, "created_at": {"$gte": cutoff.isoformat()}},
        {"_id": 0, "alert_id": 1},
    )
    return doc is not None


async def persistence_run_count(
    db: Any, alert_type: str, tickers: list[str]
) -> int:
    """How many days in a row we've fired an alert of `type` for
    `tickers` within the persistence window. Returns 0 for a fresh
    alert, N for the Nth consecutive occurrence.

    "Consecutive" is approximated by day buckets — if there are N
    alerts in N distinct UTC days within the window, we call that an
    N-day streak. Gaps break the streak (we don't false-positive on
    sporadic repeats 5 days apart).
    """
    if db is None:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=PERSISTENCE_WINDOW_DAYS)
    sorted_tix = sorted(t.upper() for t in tickers)
    cursor = db[_COLLECTION].find(
        {
            "alert_type": alert_type,
            "tickers_sorted": sorted_tix,
            "created_at": {"$gte": cutoff.isoformat()},
        },
        {"_id": 0, "date_bucket": 1, "created_at": 1},
    ).sort("created_at", -1)

    # Collect distinct date buckets, walking back from today. First
    # bucket MUST be today-1 or earlier (caller is about to record
    # today's so it's not yet in the collection). We want the RUN
    # ending at today — i.e. consecutive days from the most-recent
    # backwards. A gap breaks the run.
    seen_buckets: set[str] = set()
    async for row in cursor:
        b = row.get("date_bucket")
        if b:
            seen_buckets.add(b)

    if not seen_buckets:
        return 0

    # Walk backwards from yesterday; stop when we hit a gap.
    run = 0
    today = datetime.now(timezone.utc).date()
    for days_back in range(1, PERSISTENCE_WINDOW_DAYS + 1):
        check = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        if check in seen_buckets:
            run += 1
        else:
            break
    return run


async def record_alert(
    db: Any,
    alert_id: str,
    alert_type: str,
    tickers: list[str],
    date_bucket: str,
    metadata: dict | None = None,
) -> None:
    """Persist the alert so future `already_alerted` checks see it."""
    if db is None:
        return
    try:
        await db[_COLLECTION].insert_one({
            "alert_id": alert_id,
            "alert_type": alert_type,
            "tickers": [t.upper() for t in tickers],
            "tickers_sorted": sorted(t.upper() for t in tickers),
            "date_bucket": date_bucket,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        })
    except Exception as e:
        # Non-fatal — at worst we send a duplicate alert the next
        # cycle. Crashing the alert pipeline would be worse.
        logger.warning(f"[alert-dedup] record failed for {alert_id}: {e}")


async def should_send_alert(
    db: Any,
    alert_type: str,
    tickers: list[str],
    date_bucket: str | None = None,
) -> tuple[bool, dict]:
    """All-in-one gate. Returns `(should_send, ctx)` where ctx carries
    the alert_id, persistence run, and the resolved date_bucket so the
    caller can use them in subject lines / logs without recomputing.

    Usage:
        should, ctx = await should_send_alert(db, "toxic_spike", ["NVDA","AAPL"])
        if not should:
            return
        subject = build_subject(ctx["run"])  # e.g. "Persisting (3 days)" if run>=2
        await send_email(...)
        await record_alert(db, ctx["alert_id"], ...)
    """
    bucket = date_bucket or date_bucket_today()
    aid = compute_alert_id(alert_type, tickers, bucket)
    if await already_alerted(db, aid):
        return False, {
            "alert_id": aid,
            "date_bucket": bucket,
            "suppressed": True,
        }
    run = await persistence_run_count(db, alert_type, tickers)
    return True, {
        "alert_id": aid,
        "date_bucket": bucket,
        "suppressed": False,
        "run": run,
    }


async def ensure_indexes(db: Any) -> None:
    """Idempotent index setup. Call once at startup.

    TTL index auto-purges rows after the persistence window so the
    collection doesn't grow unbounded. Callers that need longer
    retention (audit) should dump the row to a secondary collection
    before the TTL fires.
    """
    if db is None:
        return
    try:
        await db[_COLLECTION].create_index("alert_id")
        await db[_COLLECTION].create_index(
            [("alert_type", 1), ("tickers_sorted", 1), ("created_at", -1)]
        )
        # TTL: purge after ~2x persistence window (14 days by default)
        # so we have history for "Persisting 7-day streak" detection
        # without accumulating indefinitely.
        await db[_COLLECTION].create_index(
            "created_at",
            expireAfterSeconds=PERSISTENCE_WINDOW_DAYS * 2 * 86400,
        )
    except Exception as e:
        # Mongo sometimes complains about pre-existing indexes with
        # different options. Safe to log and continue.
        logger.debug(f"[alert-dedup] index ensure: {e}")
