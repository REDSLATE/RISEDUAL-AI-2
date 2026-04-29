"""Ops alerter — passive transition watcher for the Health snapshot.

What this is
------------
The AdminPanel → Health tab surfaces heuristic notes (calibration
drift, scheduler stalled, missing API keys, etc.) computed by
:mod:`services.ops_snapshot`. That dashboard is pull-only — an
operator has to remember to look at it.

This module flips the model: a scheduled tick reads the same
snapshot, diffs the current ``notes`` set against the previous
run's notes, and posts a single-line webhook message the *first*
time a note appears (or the moment one resolves back to nominal).

Behavior
--------
- ``OPS_ALERT_WEBHOOK_URL`` unset → entire pipeline no-ops.
  The job logs a single info line per tick and returns.
- New note appears → post to webhook, record alert timestamp.
- Same note still present on next tick → suppressed (de-dup).
- Same note appears again >``OPS_ALERT_DEDUP_HOURS`` later
  (default 4h) → re-post (so a flapping condition doesn't go
  silent forever).
- Note disappears → post a "resolved" message.
- ``"All gauges nominal."`` is filtered both ways — it's the
  *absence* of alerts, never an alert itself.

Webhook compatibility
---------------------
We POST a JSON body with ``text`` (Slack-compatible) AND ``content``
(Discord-compatible) AND a structured ``alerts`` array. Slack/Discord
each ignore the field they don't recognise; generic webhook
receivers (Zapier, n8n, custom) get the structured payload.

State storage
-------------
One Mongo doc in ``ops_alerter_state`` keyed by ``_id="state"``.
Holds the last-seen notes set + per-note alert timestamps. Tier 3
firewall: this writes to its own collection only, never to
``research_shadow_decisions`` or any production trade collection.
"""
from __future__ import annotations

__domain__ = "PRD"

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from services.ops_snapshot import collect_ops_snapshot

logger = logging.getLogger(__name__)


_WEBHOOK_URL = (os.environ.get("OPS_ALERT_WEBHOOK_URL") or "").strip()
_DEDUP_HOURS = float(os.environ.get("OPS_ALERT_DEDUP_HOURS", "4"))
_HTTP_TIMEOUT_S = float(os.environ.get("OPS_ALERT_TIMEOUT_S", "8"))
_INSTANCE_LABEL = (os.environ.get("OPS_ALERT_INSTANCE_LABEL") or "RISEDUAL").strip()

_NOMINAL_NOTE = "All gauges nominal."
_STATE_COLL = "ops_alerter_state"
_STATE_ID = "state"


def is_configured() -> bool:
    """Cheap config check for the admin UI banner. Re-reads env each
    call so flipping the flag without restart works for tests."""
    return bool((os.environ.get("OPS_ALERT_WEBHOOK_URL") or "").strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


async def _load_state(db: Any) -> dict[str, Any]:
    """Returns ``{"last_notes": list[str], "alert_history": dict[str, iso]}``
    or empty defaults on first run / db unavailable."""
    if db is None:
        return {"last_notes": [], "alert_history": {}}
    try:
        doc = await db[_STATE_COLL].find_one(
            {"_id": _STATE_ID}, {"_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ops-alerter] load_state failed: %s", exc)
        return {"last_notes": [], "alert_history": {}}
    return {
        "last_notes": (doc or {}).get("last_notes") or [],
        "alert_history": (doc or {}).get("alert_history") or {},
    }


async def _save_state(
    db: Any,
    last_notes: list[str],
    alert_history: dict[str, str],
) -> None:
    if db is None:
        return
    try:
        await db[_STATE_COLL].update_one(
            {"_id": _STATE_ID},
            {"$set": {
                "last_notes": list(last_notes),
                "alert_history": dict(alert_history),
                "updated_at": _iso(),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ops-alerter] save_state failed: %s", exc)


def _format_message(*, kind: str, notes: list[str]) -> str:
    """Build a single human-readable string that renders cleanly in
    Slack, Discord, and plain email previews."""
    if kind == "alert":
        header = f":rotating_light: {_INSTANCE_LABEL} ops alert(s) detected:"
    else:
        header = f":white_check_mark: {_INSTANCE_LABEL} ops alert(s) resolved:"
    return header + "\n" + "\n".join(f"  • {n}" for n in notes)


async def _post_webhook(message: str, *, alerts: list[str], kind: str) -> bool:
    """Returns True on 2xx, False on any failure (timeout, non-2xx,
    network error). Never raises out — the alerter is a best-effort
    side effect; an outbound failure must not crash the scheduler."""
    url = (os.environ.get("OPS_ALERT_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    payload = {
        "text": message,        # Slack
        "content": message,     # Discord
        "kind": kind,           # generic webhook receivers
        "alerts": alerts,
        "instance": _INSTANCE_LABEL,
        "timestamp": _iso(),
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "[ops-alerter] webhook returned %s: %s",
                resp.status_code, resp.text[:160],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ops-alerter] webhook post failed: %s", exc)
        return False


def _partition_dedup(
    new_notes: list[str],
    alert_history: dict[str, str],
    *,
    now: datetime,
    dedup_hours: float,
) -> tuple[list[str], list[str]]:
    """Split new alerts into ``(fresh, suppressed)`` based on whether
    the same note was alerted within the dedup window. Pure function —
    returned lists preserve input ordering for stable webhook output."""
    cutoff = now - timedelta(hours=dedup_hours)
    fresh: list[str] = []
    suppressed: list[str] = []
    for n in new_notes:
        last = alert_history.get(n)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if last_dt > cutoff:
                    suppressed.append(n)
                    continue
            except ValueError:
                pass
        fresh.append(n)
    return fresh, suppressed


async def run_tick(db: Any) -> dict[str, Any]:
    """One pass of the alerter. Scheduler entry point.

    Always returns a structured dict (never raises). Caller can
    ignore the result on the cron path; the manual-trigger admin
    endpoint surfaces it for debugging.
    """
    snap = await collect_ops_snapshot(db)
    current_notes = list(snap.get("notes") or [])

    # Strip the nominal sentinel before any comparison — it's the
    # absence-of-alerts marker, not a real condition.
    current_real = [n for n in current_notes if n != _NOMINAL_NOTE]

    state = await _load_state(db)
    last_real = [n for n in state["last_notes"] if n != _NOMINAL_NOTE]
    alert_history: dict[str, str] = dict(state["alert_history"])

    new_notes = [n for n in current_real if n not in last_real]
    resolved_notes = [n for n in last_real if n not in current_real]

    fresh, suppressed = _partition_dedup(
        new_notes, alert_history,
        now=_now(), dedup_hours=_DEDUP_HOURS,
    )

    posted_alert = False
    posted_resolved = False
    configured = is_configured()

    if fresh and configured:
        if await _post_webhook(
            _format_message(kind="alert", notes=fresh),
            alerts=fresh, kind="alert",
        ):
            posted_alert = True
            stamp = _iso()
            for n in fresh:
                alert_history[n] = stamp

    if resolved_notes and configured:
        if await _post_webhook(
            _format_message(kind="resolved", notes=resolved_notes),
            alerts=resolved_notes, kind="resolved",
        ):
            posted_resolved = True
            for n in resolved_notes:
                alert_history.pop(n, None)

    # Persist the new "last seen" set even when no webhook fires —
    # otherwise a flapping condition would re-fire on every tick
    # because last_notes never advances past the first run.
    await _save_state(db, current_notes, alert_history)

    return {
        "configured": configured,
        "snapshot_at": snap.get("generated_at"),
        "current_notes": current_notes,
        "fresh_alerts": fresh,
        "resolved_alerts": resolved_notes,
        "suppressed_dedup": suppressed,
        "posted_alert": posted_alert,
        "posted_resolved": posted_resolved,
    }
