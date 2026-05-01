"""
Data-integrity alert rules — threshold-based notifications for the
metrics already surfaced on the admin dashboard.

Model
-----
Each row of ``data_integrity_alert_rules`` looks like::

    {
      "_id": <uuid>,
      "rule_id": "unknown-dir-immediate",
      "metric": "unknown_direction_tokens",   # or "grade_backfills" / "brute_force_lockouts"
      "window_hours": 24,
      "threshold": 1,                          # breach when count >= threshold
      "comparator": "gte",                     # gte | gt | eq
      "channels": ["email:admin@risedual.ai", "slack"],
      "enabled": True,
      "throttle_hours": 12,                    # don't re-fire within this window
      "last_fired_at": "2026-05-01T03:17:08Z",
      "last_fired_value": 3,
      "notes": "free-form operator context",
      "created_at": "2026-05-01T03:17:08Z",
      "updated_at": "2026-05-01T03:17:08Z",
    }

The scheduled evaluator (``evaluate_rules_once``) runs every 15
minutes. For each enabled rule it:

    1. Reads the live count from the same backing collection the
       dashboard API reads from — guaranteeing "what you saw on the
       dashboard triggered the alert" transparency.
    2. Compares against the threshold.
    3. If breached AND the rule is outside its throttle window,
       dispatches to every listed channel in parallel.
    4. Stamps ``last_fired_at`` / ``last_fired_value`` on the rule
       document so the throttle is durable across restarts.

Every fire writes a permanent record to ``data_integrity_alert_events``
(regardless of throttle / enable state) so the admin UI can render a
timeline of what tripped, when, and why.

Design philosophy
-----------------
* **Same source of truth as the dashboard.** If the dashboard shows
  3 unknown-direction events, the rule sees 3. No cached / derived
  state.
* **Channels are orthogonal.** Email and Slack dispatch in parallel;
  one channel's outage never blocks another.
* **Best-effort on the notify path.** A provider failure logs a
  warning and continues. The rule STILL fires (throttle + event
  row persist) so retries can happen on the next tick.
* **PRD domain only.** Observability over already-completed events —
  never gates or mutates a trading decision.
"""

from __future__ import annotations

__domain__ = "PRD"

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)


# ── Supported metrics ──────────────────────────────────────────────
#
# New metric? Add an entry here with a pure-async counter lambda.
# The lambda receives (db, since_iso) and MUST return an int. Keeping
# the dispatch table narrow means operators can only point rules at
# metrics we actually support — typos fail the create call, not the
# next scheduler tick.

MetricName = Literal[
    "unknown_direction_tokens",
    "grade_backfills",
    "le_trade_repairs",
    "brute_force_lockouts",
]


async def _count_unknown_direction_tokens(db, since_iso: str) -> int:
    return await db.data_integrity_metrics.count_documents({
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since_iso},
    })


async def _count_grade_backfills(db, since_iso: str) -> int:
    return await db.prediction_grade_backfill_log.count_documents({
        "applied_at": {"$gte": since_iso},
    })


async def _count_le_trade_repairs(db, since_iso: str) -> int:
    return await db.learning_engine_trade_repair_log.count_documents({
        "applied_at": {"$gte": since_iso},
    })


async def _count_brute_force_lockouts(db, since_iso: str) -> int:
    return await db.brute_force_events.count_documents({
        "fired_at": {"$gte": since_iso},
    })


METRIC_COUNTERS = {
    "unknown_direction_tokens": _count_unknown_direction_tokens,
    "grade_backfills": _count_grade_backfills,
    "le_trade_repairs": _count_le_trade_repairs,
    "brute_force_lockouts": _count_brute_force_lockouts,
}


# ── Comparators ────────────────────────────────────────────────────


def _compare(count: int, threshold: int, comparator: str) -> bool:
    if comparator == "gte":
        return count >= threshold
    if comparator == "gt":
        return count > threshold
    if comparator == "eq":
        return count == threshold
    raise ValueError(f"unknown comparator: {comparator!r}")


# ── Dispatch ───────────────────────────────────────────────────────


async def _dispatch_email(recipient: str, title: str, body_html: str) -> bool:
    try:
        from services.email_service import _routed_send, _base_html
        sent = await _routed_send(
            [recipient],
            f"[RISEDUAL] {title}",
            _base_html(body_html, preheader=title),
        )
        return bool(sent)
    except Exception as exc:
        logger.warning(
            "[integrity_alert] email dispatch failed to=%s: %s",
            recipient, exc,
        )
        return False


async def _dispatch_slack(title: str, message: str, metadata: dict) -> bool:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return False
    try:
        import httpx
        fields = [
            {"type": "mrkdwn", "text": f"*{k}*\n`{v}`"}
            for k, v in (metadata or {}).items()
        ][:10]
        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": f"RISEDUAL · {title}"[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
        ]
        if fields:
            blocks.append({"type": "section", "fields": fields})
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                webhook_url,
                json={"text": f"RISEDUAL · {title}", "blocks": blocks},
            )
            return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("[integrity_alert] slack dispatch failed: %s", exc)
        return False


async def _dispatch_channel(
    channel: str, title: str, message: str, metadata: dict,
) -> dict:
    """Route a single channel spec to the right dispatcher.

    Supported prefixes:
        "email:<addr>" — send via the existing email pipeline
        "slack"        — fire a Slack webhook if SLACK_WEBHOOK_URL set
    """
    if channel.startswith("email:"):
        addr = channel.split(":", 1)[1].strip()
        if not addr:
            return {"channel": channel, "sent": False, "reason": "empty_addr"}
        rows = "\n".join(
            f"<tr><td style='padding:4px 8px;color:#94a3b8;font-family:monospace;"
            f"font-size:12px;'>{k}</td>"
            f"<td style='padding:4px 8px;color:#e2e8f0;font-family:monospace;"
            f"font-size:12px;'>{v}</td></tr>"
            for k, v in (metadata or {}).items()
        )
        body = (
            f"<p style='color:#e2e8f0;font-size:14px;line-height:1.5;'>{message}</p>"
            f"<table style='border-collapse:collapse;margin-top:12px;"
            f"background:#0f172a;border:1px solid #334155;border-radius:6px;'>"
            f"{rows}</table>"
        )
        ok = await _dispatch_email(addr, title, body)
        return {"channel": channel, "sent": ok}
    if channel == "slack":
        ok = await _dispatch_slack(title, message, metadata)
        return {"channel": channel, "sent": ok}
    return {"channel": channel, "sent": False, "reason": "unsupported"}


# ── Evaluator ──────────────────────────────────────────────────────


async def evaluate_rules_once(db: Any) -> dict:
    """Run every enabled rule, dispatch breach notifications, return summary."""
    now = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "evaluated": 0,
        "skipped_disabled": 0,
        "breached": 0,
        "fired": 0,
        "throttled": 0,
        "errors": 0,
        "details": [],
    }

    cursor = db.data_integrity_alert_rules.find({})
    async for rule in cursor:
        summary["evaluated"] += 1
        if not rule.get("enabled", True):
            summary["skipped_disabled"] += 1
            continue
        try:
            metric = rule.get("metric")
            counter = METRIC_COUNTERS.get(metric)
            if counter is None:
                summary["errors"] += 1
                summary["details"].append(
                    {"rule_id": rule.get("rule_id"), "reason": f"unknown metric {metric!r}"}
                )
                continue

            window_hours = int(rule.get("window_hours", 24))
            since_iso = (now - timedelta(hours=window_hours)).isoformat()
            count = await counter(db, since_iso)

            threshold = int(rule.get("threshold", 1))
            comparator = rule.get("comparator", "gte")
            breached = _compare(count, threshold, comparator)
            if not breached:
                continue
            summary["breached"] += 1

            # Throttle check — durable across restarts.
            throttle_hours = int(rule.get("throttle_hours", 12))
            last = rule.get("last_fired_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if (now - last_dt) < timedelta(hours=throttle_hours):
                        summary["throttled"] += 1
                        summary["details"].append({
                            "rule_id": rule.get("rule_id"),
                            "status": "throttled",
                            "count": count,
                        })
                        continue
                except Exception:
                    pass  # malformed timestamp → fall through and fire

            title = (
                f"Data integrity: {metric} = {count} in last {window_hours}h "
                f"(threshold {comparator} {threshold})"
            )
            message = rule.get("notes") or (
                "This is an automated RISEDUAL data-integrity tripwire. "
                "Open the Data Integrity panel in Admin Tools for the "
                "per-context breakdown and the latest nightly audit."
            )
            metadata = {
                "metric": metric,
                "window_hours": window_hours,
                "threshold": f"{comparator} {threshold}",
                "current_count": count,
                "rule_id": rule.get("rule_id"),
                "fired_at": now.isoformat(),
            }
            channels = rule.get("channels") or []
            channel_results: list[dict] = []
            if channels:
                import asyncio as _aio
                channel_results = list(await _aio.gather(*[
                    _dispatch_channel(c, title, message, metadata)
                    for c in channels
                ], return_exceptions=False))

            # Persist the event and update the rule's throttle state
            # regardless of whether channels actually delivered —
            # the breach happened, the event log must reflect it.
            await db.data_integrity_alert_events.insert_one({
                "_id": str(uuid4()),
                "rule_id": rule.get("rule_id"),
                "metric": metric,
                "window_hours": window_hours,
                "threshold": threshold,
                "comparator": comparator,
                "count": count,
                "fired_at": now.isoformat(),
                "channels": channels,
                "dispatch_results": channel_results,
            })

            # ── Self-defense activation ──────────────────────────
            # If the rule carries a ``mitigation`` spec, activate it
            # now. The mitigation record has its own TTL so even if
            # a follow-up rule evaluation is throttled, the degrade
            # mode still auto-expires on schedule. We refresh the
            # sync cache immediately so the very next position-
            # sizing call honours the new multiplier without waiting
            # for the next 15-min evaluator tick.
            mitigation_spec = rule.get("mitigation")
            if mitigation_spec:
                from services.integrity_mitigation_service import (
                    activate_integrity_mitigation,
                    refresh_sync_cache,
                )
                try:
                    mid = await activate_integrity_mitigation(
                        db,
                        source_rule_id=str(rule.get("rule_id") or rule.get("_id")),
                        mitigation=mitigation_spec,
                        ttl_minutes=int(rule.get("mitigation_ttl_minutes", 60)),
                    )
                    if mid:
                        # Backfill the event row with the mitigation
                        # pointer so the dashboard timeline joins them
                        # visually (breach → degrade).
                        await db.data_integrity_alert_events.update_one(
                            {"rule_id": rule.get("rule_id"),
                             "fired_at": now.isoformat()},
                            {"$set": {
                                "mitigation_id": mid,
                                "mitigation_action": mitigation_spec.get("action"),
                                "mitigation_params": mitigation_spec.get("params") or {},
                            }},
                        )
                        await refresh_sync_cache(db)
                except Exception as exc:
                    logger.exception(
                        "[integrity_alert] mitigation activation crashed for "
                        "rule %s: %s", rule.get("rule_id"), exc,
                    )

            await db.data_integrity_alert_rules.update_one(
                {"_id": rule["_id"]},
                {"$set": {
                    "last_fired_at": now.isoformat(),
                    "last_fired_value": count,
                    "updated_at": now.isoformat(),
                }},
            )
            summary["fired"] += 1
            summary["details"].append({
                "rule_id": rule.get("rule_id"),
                "status": "fired",
                "count": count,
                "dispatch": channel_results,
            })
        except Exception as exc:
            logger.exception(
                "[integrity_alert] rule %s crashed: %s",
                rule.get("rule_id"), exc,
            )
            summary["errors"] += 1
            summary["details"].append({
                "rule_id": rule.get("rule_id"), "reason": str(exc)[:200],
            })

    return summary
