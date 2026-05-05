"""Data-integrity dashboard, alert rules, and integrity audit triggers.

Extracted from ``routes/admin.py`` (~4050 lines, on the size allowlist)
to keep the admin surface domain-modular. URLs unchanged so no frontend
or test edits are needed:

  * ``GET    /api/admin/data-integrity/summary``
  * ``POST   /api/admin/data-integrity/run-audit``
  * ``GET    /api/admin/data-integrity/alert-rules``
  * ``POST   /api/admin/data-integrity/alert-rules``
  * ``DELETE /api/admin/data-integrity/alert-rules/{rule_id}``
  * ``POST   /api/admin/data-integrity/alert-rules/evaluate-now``
  * ``GET    /api/admin/data-integrity/alert-events``
  * ``GET    /api/admin/data-integrity/timeseries``

Read/owner-gated via the same ``_require_owner`` pattern used by other
extracted admin modules (e.g. ``routes/admin_conviction.py``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/admin", tags=["admin-data-integrity"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    """Stricter gate: only owner role passes. Mirrors the helper in
    ``routes/admin.py`` — duplicated rather than imported so this
    module has no inbound dependency on admin.py."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── Dashboard summary ─────────────────────────────────────────────


@router.get("/data-integrity/summary")
async def data_integrity_summary(request: Request):
    """Return the current data-integrity health snapshot.

    Fields:

      * ``unknown_direction_tokens`` — metric counts (24h / 7d) and
        per-context breakdown. Target: 0 in every window.
      * ``backfills`` — prediction grade backfills + LE trade repairs
        performed by the cleanup scripts; counts all-time + 7d.
      * ``toxic_lessons`` — created in the last 7d vs. superseded
        (i.e. flipped away from toxic_lesson by the corrections).
      * ``latest_audit`` — the most recent
        ``data_integrity_audits`` row (nightly invariant run).
      * ``brute_force_events`` — lockout triggers in the last 24h
        / 7d, with the top offending (client_ip, email) pairs.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    # 1) unknown-direction metric
    udt_24h = await db.data_integrity_metrics.count_documents({
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since_24h},
    })
    udt_7d = await db.data_integrity_metrics.count_documents({
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since_7d},
    })
    udt_contexts = [
        {"context": r["_id"], "count": r["n"]}
        async for r in db.data_integrity_metrics.aggregate([
            {"$match": {"metric": "unknown_direction_token",
                        "fired_at": {"$gte": since_7d}}},
            {"$group": {"_id": "$context", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ])
    ]

    # 2) backfills
    grade_backfills_all = await db.prediction_grade_backfill_log.count_documents({})
    grade_backfills_7d = await db.prediction_grade_backfill_log.count_documents({
        "applied_at": {"$gte": since_7d},
    })
    le_repairs_all = await db.learning_engine_trade_repair_log.count_documents({})
    le_repairs_7d = await db.learning_engine_trade_repair_log.count_documents({
        "applied_at": {"$gte": since_7d},
    })

    # 3) toxic-lesson count in Chroma — best-effort, never fail the dashboard.
    toxic_created_7d = None
    toxic_superseded = None
    try:
        import services.market_memory_service as mms
        coll = getattr(mms, "_collection", None)
        if coll is not None:
            import asyncio as _aio
            active = await _aio.to_thread(
                coll.get, where={"outcome": "toxic_lesson"}, include=["metadatas"]
            )
            metas = active.get("metadatas") or []
            toxic_created_7d = sum(
                1 for m in metas
                if m and str((m.get("created_at") or m.get("logged_at") or "")) >= since_7d
            )
            superseded = await _aio.to_thread(
                coll.get, where={"lesson_status": "superseded"}, include=["metadatas"]
            )
            toxic_superseded = len(superseded.get("ids") or [])
    except Exception as e:
        logger.warning("data_integrity: chroma probe failed: %s", e)

    # 4) latest audit summary
    latest_audit = await db.data_integrity_audits.find_one(
        {}, sort=[("run_id", -1)],
    )
    if latest_audit:
        latest_audit.pop("_id", None)

    # 5) brute-force events
    bf_24h = await db.brute_force_events.count_documents({
        "fired_at": {"$gte": since_24h},
    })
    bf_7d = await db.brute_force_events.count_documents({
        "fired_at": {"$gte": since_7d},
    })
    bf_top = [
        {"identifier": r["_id"], "count": r["n"]}
        async for r in db.brute_force_events.aggregate([
            {"$match": {"fired_at": {"$gte": since_7d}}},
            {"$group": {
                "_id": {"$concat": ["$client_ip", " → ", "$triggered_by_user_email"]},
                "n": {"$sum": 1},
            }},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ])
    ]

    # 6) integrity mitigation state — the self-defense layer surfaced
    # so the dashboard banner can render "degraded trading active"
    # without needing a separate endpoint.
    mitigation_state = {"active": False, "active_count": 0}
    try:
        from services.integrity_mitigation_service import (
            summarize_integrity_mitigation_state,
        )
        mitigation_state = await summarize_integrity_mitigation_state(db)
    except Exception as e:
        logger.warning("data_integrity: mitigation probe failed: %s", e)

    return {
        "as_of": now.isoformat(),
        "unknown_direction_tokens": {
            "last_24h": udt_24h,
            "last_7d": udt_7d,
            "top_contexts_7d": udt_contexts,
        },
        "backfills": {
            "grade_all_time": grade_backfills_all,
            "grade_last_7d": grade_backfills_7d,
            "le_trade_all_time": le_repairs_all,
            "le_trade_last_7d": le_repairs_7d,
        },
        "toxic_lessons": {
            "created_last_7d": toxic_created_7d,
            "superseded_total": toxic_superseded,
        },
        "brute_force": {
            "lockouts_last_24h": bf_24h,
            "lockouts_last_7d": bf_7d,
            "top_offenders_7d": bf_top,
        },
        "latest_nightly_audit": latest_audit,
        "mitigation": mitigation_state,
    }


@router.post("/data-integrity/run-audit")
async def run_integrity_audit_now(request: Request):
    """Manually trigger the nightly invariant audit.

    Useful for verifying a cleanup just landed. The scheduled job
    runs once a day at 03:15 UTC.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    from services.data_integrity_auditor import run_nightly_integrity_audit
    summary = await run_nightly_integrity_audit(db)
    summary.pop("_id", None)
    return summary


# ═══════════════════════════════════════════════════════════════════
# DATA INTEGRITY ALERT RULES
# ═══════════════════════════════════════════════════════════════════
#
# Operator-defined threshold alerts on top of the dashboard metrics.
# Each rule is independently editable from the admin UI; breaches
# dispatch to email + Slack in parallel subject to per-rule throttle.
# The rule documents are the same shape the scheduler evaluator
# reads; see services/data_integrity_alerts.py for the contract.


class AlertRuleUpsert(BaseModel):
    rule_id: str
    metric: str  # Validated against METRIC_COUNTERS below.
    window_hours: int = 24
    threshold: int = 1
    comparator: str = "gte"  # gte | gt | eq
    channels: list[str] = []  # "email:<addr>" | "slack"
    enabled: bool = True
    throttle_hours: int = 12
    notes: str = ""
    # Self-defense spec (optional). When present and the rule fires,
    # `activate_integrity_mitigation` is called with this dict — e.g.
    #   {"action": "DEGRADE_TRADING",
    #    "params": {"position_multiplier": 0.5,
    #               "disable_strong_signals": true}}
    mitigation: dict | None = None
    mitigation_ttl_minutes: int = 60


@router.get("/data-integrity/alert-rules")
async def list_alert_rules(request: Request):
    """Return every alert rule. Sorted most-recently-updated first."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    cursor = db.data_integrity_alert_rules.find({}).sort("updated_at", -1)
    rules = []
    async for r in cursor:
        r.pop("_id", None)
        rules.append(r)
    return {"rules": rules}


@router.post("/data-integrity/alert-rules")
async def upsert_alert_rule(payload: AlertRuleUpsert, request: Request):
    """Create or update an alert rule, keyed by ``rule_id``."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    from services.data_integrity_alerts import METRIC_COUNTERS
    if payload.metric not in METRIC_COUNTERS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "unsupported_metric",
                "supported": sorted(METRIC_COUNTERS.keys()),
            },
        )
    if payload.comparator not in {"gte", "gt", "eq"}:
        raise HTTPException(status_code=400, detail="comparator must be gte|gt|eq")
    if payload.window_hours <= 0 or payload.threshold < 0 or payload.throttle_hours < 0:
        raise HTTPException(status_code=400, detail="window/threshold/throttle must be non-negative")
    # Channel format sanity — each entry must be "email:<addr>" or
    # exactly "slack". Unknown channels would silently fail at dispatch.
    for c in payload.channels:
        if c == "slack":
            continue
        if c.startswith("email:") and "@" in c.split(":", 1)[1]:
            continue
        raise HTTPException(
            status_code=400,
            detail=f"unsupported channel {c!r}; use 'email:<addr>' or 'slack'",
        )

    # Mitigation sanity — if the operator attached a mitigation spec,
    # the `action` must be in the whitelist. Catching it here instead
    # of silently no-opping at evaluator time prevents rules that
    # "look like" they'll degrade trading but never actually fire.
    if payload.mitigation:
        from services.integrity_mitigation_service import SUPPORTED_ACTIONS
        mit_action = (payload.mitigation.get("action") or "").strip()
        if mit_action not in SUPPORTED_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "unsupported_mitigation_action",
                    "supported": sorted(SUPPORTED_ACTIONS),
                    "received": mit_action,
                },
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        **payload.dict(),
        "updated_at": now_iso,
    }
    existing = await db.data_integrity_alert_rules.find_one({"rule_id": payload.rule_id})
    if not existing:
        doc["created_at"] = now_iso
    await db.data_integrity_alert_rules.update_one(
        {"rule_id": payload.rule_id},
        {"$set": doc},
        upsert=True,
    )
    saved = await db.data_integrity_alert_rules.find_one({"rule_id": payload.rule_id})
    saved.pop("_id", None)
    return saved


@router.delete("/data-integrity/alert-rules/{rule_id}")
async def delete_alert_rule(rule_id: str, request: Request):
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    res = await db.data_integrity_alert_rules.delete_one({"rule_id": rule_id})
    return {"deleted": res.deleted_count > 0, "rule_id": rule_id}


@router.post("/data-integrity/alert-rules/evaluate-now")
async def evaluate_alert_rules_now(request: Request):
    """Manually run the rule evaluator once. Returns the summary."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    from services.data_integrity_alerts import evaluate_rules_once
    return await evaluate_rules_once(db)


@router.get("/data-integrity/alert-events")
async def list_alert_events(request: Request, limit: int = 50):
    """Recent alert breaches with dispatch results."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    limit = max(1, min(int(limit or 50), 500))
    cursor = (
        db.data_integrity_alert_events
        .find({})
        .sort("fired_at", -1)
        .limit(limit)
    )
    events = []
    async for e in cursor:
        e.pop("_id", None)
        events.append(e)
    return {"events": events, "limit": limit}


@router.get("/data-integrity/timeseries")
async def data_integrity_timeseries(request: Request, days: int = 14):
    """Daily bucket counts for the data-integrity sparklines.

    Returns three parallel arrays (unknown-direction tokens, grade
    backfills, brute-force lockouts) bucketed by UTC calendar day.
    14 days is the default — plenty of width to catch a slow drift
    toward the bug class without overwhelming the admin card.

    Shape:
        {
          "days": 14,
          "buckets": ["2026-04-17", "2026-04-18", ..., "2026-04-30"],
          "unknown_direction_tokens": [0, 0, ..., 0],
          "grade_backfills":          [0, ..., 24, 0, ...],
          "brute_force_lockouts":     [0, ..., 0],
        }

    A rising curve on ``unknown_direction_tokens`` is the earliest
    tripwire for a new engine emitting a non-enum verdict; the
    nightly invariant audit will eventually catch it, but the
    sparkline surfaces the trend hours earlier.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    days = max(1, min(int(days or 14), 90))
    today = datetime.now(timezone.utc).date()
    buckets = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    bucket_isos = [d.isoformat() for d in buckets]
    since = (datetime.combine(buckets[0], datetime.min.time(), tzinfo=timezone.utc)).isoformat()

    async def _daily_counts(coll, time_field: str, extra: dict | None = None) -> dict[str, int]:
        q: dict[str, Any] = {time_field: {"$gte": since}}
        if extra:
            q.update(extra)
        cursor = coll.find(q, {"_id": 0, time_field: 1})
        out: dict[str, int] = {d: 0 for d in bucket_isos}
        async for r in cursor:
            ts = str(r.get(time_field, ""))
            if len(ts) >= 10:
                day = ts[:10]
                if day in out:
                    out[day] += 1
        return out

    udt = await _daily_counts(
        db.data_integrity_metrics, "fired_at",
        {"metric": "unknown_direction_token"},
    )
    backfills = await _daily_counts(
        db.prediction_grade_backfill_log, "applied_at",
    )
    bf = await _daily_counts(db.brute_force_events, "fired_at")

    return {
        "days": days,
        "buckets": bucket_isos,
        "unknown_direction_tokens": [udt[d] for d in bucket_isos],
        "grade_backfills": [backfills[d] for d in bucket_isos],
        "brute_force_lockouts": [bf[d] for d in bucket_isos],
    }
