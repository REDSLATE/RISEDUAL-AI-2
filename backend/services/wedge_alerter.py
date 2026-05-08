"""Wedge alerter — heartbeat-driven, notification-only paging.

Scope (notification-only):
  * NO promotion. NO enforcement. NO broker calls.
  * NO scheduler restart. NO automatic remediation.
  * Posts ONE alert per condition transition; cooldown suppresses
    duplicates within the window.

Trigger rules
─────────────
  T1. ``any_frozen=true`` for ``> WEDGE_FROZEN_THRESHOLD_MIN`` minutes
      (default 30). Tracked per-lane via ``frozen_started_at``.
  T2. ``top_clamp_block_reason == STRATEGIST_FEATURE_HEALTH_LOW``
      with ``top_clamp_block_count > WEDGE_FEATURE_HEALTH_THRESHOLD``
      (default 50) in the last 1h — read directly off the heartbeat
      snapshot.

Webhook
───────
Posts to ``OPS_ALERT_WEBHOOK_URL`` (Slack/Discord/generic-compatible
JSON). Missing webhook logs ``OPS_ALERT_WEBHOOK_URL_MISSING`` once
per tick and returns; NEVER raises.

State
─────
Two collections (Tier 3 firewall — never touches trade collections):
  * ``wedge_alerter_state``    — single doc id ``state``
  * ``wedge_alerter_history``  — append-only audit log

Read-only callers
─────────────────
The admin /api/admin/ml/wedge-alerter/{status,history,run-now} routes
expose ``run_tick`` and the audit history for review. Promotion paths
do NOT consult this module.
"""
from __future__ import annotations

__domain__ = "PRD"

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from services.ml import executor_heartbeat
from services.ml import boot_receipts

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────


def _frozen_threshold_min() -> float:
    try:
        return float(os.environ.get("WEDGE_FROZEN_THRESHOLD_MIN", "30"))
    except (TypeError, ValueError):
        return 30.0


def _feature_health_threshold() -> int:
    try:
        return int(os.environ.get("WEDGE_FEATURE_HEALTH_THRESHOLD", "50"))
    except (TypeError, ValueError):
        return 50


def _cooldown_hours() -> float:
    try:
        return float(os.environ.get("WEDGE_ALERT_COOLDOWN_HOURS", "4"))
    except (TypeError, ValueError):
        return 4.0


def _http_timeout_s() -> float:
    try:
        return float(os.environ.get("OPS_ALERT_TIMEOUT_S", "8"))
    except (TypeError, ValueError):
        return 8.0


def _instance_label() -> str:
    return (os.environ.get("OPS_ALERT_INSTANCE_LABEL") or "RISEDUAL").strip()


_FEATURE_HEALTH_REASON = "STRATEGIST_FEATURE_HEALTH_LOW"
_STATE_COLL = "wedge_alerter_state"
_STATE_ID = "state"
_HISTORY_COLL = "wedge_alerter_history"


# ── Helpers ──────────────────────────────────────────────────────


def is_configured() -> bool:
    """Cheap config check. Re-reads env so flag flips without
    process restart work in tests."""
    return bool((os.environ.get("OPS_ALERT_WEBHOOK_URL") or "").strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


# ── State persistence ───────────────────────────────────────────


async def _load_state(db: Any) -> Dict[str, Any]:
    if db is None:
        return {"lane_frozen_started_at": {}, "alert_history": {}}
    try:
        doc = await db[_STATE_COLL].find_one({"_id": _STATE_ID}, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[wedge-alerter] load_state failed: %s", exc)
        return {"lane_frozen_started_at": {}, "alert_history": {}}
    return {
        "lane_frozen_started_at": (doc or {}).get("lane_frozen_started_at") or {},
        "alert_history": (doc or {}).get("alert_history") or {},
    }


async def _save_state(
    db: Any,
    *,
    lane_frozen_started_at: Dict[str, str],
    alert_history: Dict[str, str],
) -> None:
    if db is None:
        return
    try:
        await db[_STATE_COLL].update_one(
            {"_id": _STATE_ID},
            {"$set": {
                "lane_frozen_started_at": dict(lane_frozen_started_at),
                "alert_history": dict(alert_history),
                "updated_at": _iso(),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[wedge-alerter] save_state failed: %s", exc)


async def _append_history(db: Any, doc: Dict[str, Any]) -> None:
    if db is None:
        return
    try:
        await db[_HISTORY_COLL].insert_one({**doc, "created_at": _now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[wedge-alerter] history append failed: %s", exc)


# ── Webhook ──────────────────────────────────────────────────────


def _format_message(*, alert_key: str, payload: Dict[str, Any]) -> str:
    instance = _instance_label()
    lane = payload.get("lane") or "—"
    rule = payload.get("rule") or alert_key
    detail = payload.get("detail") or ""
    return (
        f":rotating_light: {instance} wedge alert\n"
        f"  • lane: {lane}\n"
        f"  • rule: {rule}\n"
        f"  • detail: {detail}"
    )


async def _post_webhook(*, message: str, payload: Dict[str, Any], kind: str) -> bool:
    url = (os.environ.get("OPS_ALERT_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    body = {
        "text": message,        # Slack
        "content": message,     # Discord
        "kind": kind,
        "alerts": [payload],
        "instance": _instance_label(),
        "timestamp": _iso(),
    }
    try:
        async with httpx.AsyncClient(timeout=_http_timeout_s()) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            logger.warning(
                "[wedge-alerter] webhook returned %s: %s",
                resp.status_code, resp.text[:160],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[wedge-alerter] webhook post failed: %s", exc)
        return False


# ── Trigger logic ────────────────────────────────────────────────


def _build_alert_payload(*, lane: str, rule: str, lane_info: Dict[str, Any],
                         detail: str) -> Dict[str, Any]:
    """Snapshot of all the per-lane diagnostic fields the operator
    needs to triage the alert without opening the dashboard."""
    return {
        "rule": rule,
        "lane": lane,
        "detail": detail,
        "frozen": lane_info.get("frozen"),
        "last_pipeline_run_at": lane_info.get("last_pipeline_run_at"),
        "last_signal_at": lane_info.get("last_signal_at"),
        "signals_1h": lane_info.get("signals_1h"),
        "buy_sell_1h": lane_info.get("buy_sell_1h"),
        "holds_1h": lane_info.get("holds_1h"),
        "feature_health_avg": lane_info.get("feature_health_avg"),
        "model_age_hours": lane_info.get("model_age_hours"),
        "model_stale": lane_info.get("model_stale"),
        "top_clamp_block_reason": lane_info.get("top_clamp_block_reason"),
        "top_clamp_block_count": lane_info.get("top_clamp_block_count"),
    }


def _evaluate(
    *,
    snapshot: Dict[str, Any],
    lane_frozen_started_at: Dict[str, str],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Pure rule evaluator.

    Returns a list of candidate alerts (one per (lane, rule) pair
    that fires). Mutates ``lane_frozen_started_at`` in place to track
    when each lane first became frozen — caller persists the dict
    afterwards.
    """
    candidates: List[Dict[str, Any]] = []
    frozen_after_s = _frozen_threshold_min() * 60.0
    fh_threshold = _feature_health_threshold()

    for lane, info in (snapshot.get("lanes") or {}).items():
        # ── T1: frozen continuously for >threshold ──
        is_frozen = bool(info.get("frozen"))
        started_at_iso = lane_frozen_started_at.get(lane)
        if is_frozen:
            if started_at_iso is None:
                lane_frozen_started_at[lane] = _iso(now)
                started_at = now
            else:
                started_at = _parse_iso(started_at_iso) or now
            duration_s = (now - started_at).total_seconds()
            if duration_s > frozen_after_s:
                candidates.append({
                    "alert_key": f"frozen:{lane}",
                    "rule": "FROZEN_LANE",
                    "lane": lane,
                    "detail": (
                        f"lane frozen for {duration_s/60.0:.1f} min "
                        f"(threshold {_frozen_threshold_min():.0f} min)"
                    ),
                    "duration_seconds": int(duration_s),
                    "lane_info": info,
                })
        else:
            # Lane recovered — reset the timer.
            lane_frozen_started_at.pop(lane, None)

        # ── T2: feature-health flood ──
        top_reason = info.get("top_clamp_block_reason")
        top_count = int(info.get("top_clamp_block_count") or 0)
        if top_reason == _FEATURE_HEALTH_REASON and top_count > fh_threshold:
            candidates.append({
                "alert_key": f"feature_health_low:{lane}",
                "rule": "FEATURE_HEALTH_FLOOD",
                "lane": lane,
                "detail": (
                    f"{_FEATURE_HEALTH_REASON} repeated {top_count}× in 1h "
                    f"(threshold {fh_threshold})"
                ),
                "duration_seconds": None,
                "lane_info": info,
            })

    return candidates


def _partition_cooldown(
    candidates: List[Dict[str, Any]],
    alert_history: Dict[str, str],
    *,
    now: datetime,
    cooldown_hours: float,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cutoff = now - timedelta(hours=cooldown_hours)
    fresh: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for c in candidates:
        last_iso = alert_history.get(c["alert_key"])
        last = _parse_iso(last_iso)
        if last and last > cutoff:
            suppressed.append(c)
        else:
            fresh.append(c)
    return fresh, suppressed


# ── Public entry ────────────────────────────────────────────────


async def run_tick(db: Any) -> Dict[str, Any]:
    """One pass of the wedge alerter. Scheduler entry point.

    Always returns a structured dict (NEVER raises). Caller can
    ignore the result; admin endpoint surfaces it for debugging.
    """
    # Pull live heartbeat snapshot. Augment with model_stale flag
    # from boot receipts so the alert payload carries it.
    age_by_lane: Dict[str, float] = {}
    stale_by_lane: Dict[str, bool] = {}
    try:
        for r in boot_receipts.get_all_receipts():
            if r.layer == "executor" and r.lane:
                if r.model_age_hours is not None:
                    age_by_lane[r.lane] = r.model_age_hours
                stale_by_lane[r.lane] = bool(r.stale)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wedge-alerter] boot receipt read failed: %s", exc)

    snapshot = executor_heartbeat.get_snapshot(model_age_by_lane=age_by_lane)
    for lane, info in (snapshot.get("lanes") or {}).items():
        info.setdefault("model_stale", stale_by_lane.get(lane, False))

    state = await _load_state(db)
    lane_frozen_started_at: Dict[str, str] = dict(state["lane_frozen_started_at"])
    alert_history: Dict[str, str] = dict(state["alert_history"])
    now = _now()

    candidates = _evaluate(
        snapshot=snapshot,
        lane_frozen_started_at=lane_frozen_started_at,
        now=now,
    )

    fresh, suppressed = _partition_cooldown(
        candidates, alert_history,
        now=now, cooldown_hours=_cooldown_hours(),
    )

    configured = is_configured()
    if not configured and (fresh or suppressed):
        logger.warning("[wedge-alerter] OPS_ALERT_WEBHOOK_URL_MISSING")

    posted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for c in fresh:
        payload = _build_alert_payload(
            lane=c["lane"], rule=c["rule"], lane_info=c["lane_info"],
            detail=c["detail"],
        )
        ok = False
        if configured:
            ok = await _post_webhook(
                message=_format_message(alert_key=c["alert_key"], payload=payload),
                payload=payload,
                kind="alert",
            )
        if ok:
            alert_history[c["alert_key"]] = _iso(now)
            posted.append(c)
        else:
            failed.append(c)

        # Audit row regardless of webhook success — operators can
        # always replay history even if the webhook flakes.
        await _append_history(db, {
            "alert_key": c["alert_key"],
            "rule": c["rule"],
            "lane": c["lane"],
            "detail": c["detail"],
            "duration_seconds": c.get("duration_seconds"),
            "payload": payload,
            "webhook_posted": ok,
            "webhook_configured": configured,
        })

    await _save_state(
        db,
        lane_frozen_started_at=lane_frozen_started_at,
        alert_history=alert_history,
    )

    return {
        "configured": configured,
        "snapshot_at": snapshot.get("as_of"),
        "any_frozen": snapshot.get("any_frozen"),
        "candidates": [_summarise(c) for c in candidates],
        "fresh_alerts": [_summarise(c) for c in posted],
        "suppressed_cooldown": [_summarise(c) for c in suppressed],
        "failed_posts": [_summarise(c) for c in failed],
    }


def _summarise(c: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the bulky lane_info — the audit row keeps the full
    payload; the run_tick return is summary-only for the admin UI."""
    return {
        "alert_key": c["alert_key"],
        "rule": c["rule"],
        "lane": c["lane"],
        "detail": c["detail"],
        "duration_seconds": c.get("duration_seconds"),
    }
