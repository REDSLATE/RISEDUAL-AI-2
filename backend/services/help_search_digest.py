"""Weekly admin digest for Help Center search telemetry.

Surfaces the top zero-result queries from the last 7 days so feature gaps
become proactive pushes instead of pull-to-check surfaces.

Scheduled: Mondays at 7:00 UTC via APScheduler.
Manual trigger: ``POST /api/analytics/help-search/send-digest`` (admin-only).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.email_service import (
    _is_configured,
    _routed_send,
    _help_search_digest_html,
    APP_NAME,
)

logger = logging.getLogger(__name__)

# Only send the email if there are at least this many zero-result events.
_MIN_ZERO_EVENTS = 3


async def _collect(db) -> dict:
    """Aggregate the last 7 days of help_search_events."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    total = await db.help_search_events.count_documents({"ts": {"$gte": since}})
    zero_total = await db.help_search_events.count_documents(
        {"ts": {"$gte": since}, "results_count": 0}
    )

    zero_pipeline = [
        {"$match": {"ts": {"$gte": since}, "results_count": 0}},
        {"$group": {
            "_id": "$q", "count": {"$sum": 1},
            "hubs": {"$addToSet": "$context_hub"},
            "users": {"$addToSet": "$user_id"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    zero_top = []
    async for row in db.help_search_events.aggregate(zero_pipeline):
        zero_top.append({
            "q": row["_id"],
            "count": row["count"],
            "unique_users": len([u for u in (row.get("users") or []) if u]),
            "hubs": [h for h in (row.get("hubs") or []) if h],
        })

    return {
        "total": total,
        "zero_total": zero_total,
        "zero_rate": round((zero_total / total) if total else 0, 3),
        "zero_top": zero_top,
        "window_start": since.isoformat(),
        "window_end": datetime.now(timezone.utc).isoformat(),
    }


async def send_help_search_digest(db) -> dict:
    """Render and email the weekly digest to all admins/owners."""
    if not _is_configured():
        return {"sent": 0, "skipped": True, "reason": "no_email_provider"}

    data = await _collect(db)

    # Don't spam admins if nothing meaningful happened this week.
    if data["zero_total"] < _MIN_ZERO_EVENTS:
        logger.info(
            f"Help search digest skipped: only {data['zero_total']} zero-result events in 7d "
            f"(threshold {_MIN_ZERO_EVENTS})"
        )
        return {"sent": 0, "skipped": True, "reason": "below_threshold",
                "zero_events": data["zero_total"]}

    # Recipients: all active admin/owner accounts with an email.
    recipients = []
    cursor = db.users.find(
        {"role": {"$in": ["admin", "owner"]}, "is_active": {"$ne": False}},
        {"email": 1, "name": 1, "_id": 0},
    )
    async for u in cursor:
        email = (u.get("email") or "").strip().lower()
        if email and "@" in email:
            recipients.append({"email": email, "name": u.get("name") or email.split("@")[0]})

    if not recipients:
        return {"sent": 0, "skipped": True, "reason": "no_admins"}

    subject = f"[{APP_NAME}] Feature-Gap Radar — {data['zero_total']} zero-result searches this week"
    sent_count = 0
    error_count = 0
    for rec in recipients:
        try:
            html = _help_search_digest_html(rec["name"], data)
            ok = await _routed_send([rec["email"]], subject, html)
            if ok:
                sent_count += 1
                logger.info(f"Help search digest sent to {rec['email']}")
            else:
                error_count += 1
        except Exception as e:
            error_count += 1
            logger.error(f"Help search digest send failed for {rec['email']}: {e}")

    return {
        "sent": sent_count,
        "errors": error_count,
        "skipped": False,
        "zero_events": data["zero_total"],
        "top_gap": data["zero_top"][0]["q"] if data["zero_top"] else None,
    }
