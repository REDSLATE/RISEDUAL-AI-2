"""Mongo→Chroma drift alert watcher.

Runs every 5 minutes alongside the other schedulers. Uses the same
drift computation as ``GET /api/admin/memory/drift`` so the
dashboard and the alert channel never disagree on what "drifting"
means.

Firing rules (keep cardinality low — this is an operator channel,
not a spam one):

* **Threshold crossing** — alert when ``drift_pct`` crosses into
  the ``rebuild`` band (≥ 10%). Dedup-bucketed by UTC day: if the
  condition persists, you get ONE alert per day, not 288.
* **Sudden jump** — alert when ``drift_pct`` jumps by > 5 points
  between consecutive checks. Dedup-bucketed by
  ``{day}:jump:{from→to}`` so a single jump fires once but a
  later jump on the same day still fires.
* **Recovery** — alert once when drift drops back below the ok
  threshold after a rebuild alert fired earlier in the day. Lets
  the operator close the incident without having to poll.

The watcher reads from the same ``run_position_reconciler`` /
``fetch_memory_drift`` surface — no duplicated logic. Failure isolation
wraps the whole tick so a bad Mongo response can't crash the scheduler.
"""
from __future__ import annotations

__domain__ = "PRD"  # Observability over historic state.

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request as _Request  # noqa: F401  (typed but unused directly)

logger = logging.getLogger(__name__)

# Thresholds (match the drift endpoint classifier).
REBUILD_PCT = 10.0   # ≥ this → rebuild band → alert if newly-crossed
OK_PCT = 1.0         # < this → back in ok band → recovery alert
JUMP_PCT = 5.0       # between-check jump magnitude that deserves an alert

# Process-local state — resets on restart, which is fine. The
# scheduled tick picks up the current drift on first run and
# starts tracking jumps from there; the threshold-crossing logic
# doesn't need history because it's dedup-bucketed by the day.
_last_pct: Optional[float] = None
_last_rebuild_alert_fired_on: Optional[str] = None


def _reset_state_for_tests() -> None:
    """Test-only hook — the watcher is stateful in-process."""
    global _last_pct, _last_rebuild_alert_fired_on
    _last_pct = None
    _last_rebuild_alert_fired_on = None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _compute_drift(days: int = 30) -> Optional[dict]:
    """Call the same helpers the HTTP endpoint uses.

    Returns the drift dict or ``None`` on failure. We deliberately
    do NOT go through the HTTP route because
    (a) that would require internal authN plumbing, and
    (b) the scheduler is already inside the process so a
    function call is cheaper and keeps the auth boundary clean.
    """
    try:
        from routes.admin_memory_drift import (
            _chroma_per_date_counts,
            _classify,
            _mongo_per_date_counts,
            _db,
        )
        if _db is None:
            return None
        mongo_per_date = await _mongo_per_date_counts(days)
        chroma_per_date = await _chroma_per_date_counts(days)
        mongo_total = sum(mongo_per_date.values())
        chroma_total = sum(chroma_per_date.values())
        drift = mongo_total - chroma_total
        drift_pct = (
            round(max(drift, 0) / mongo_total * 100, 2)
            if mongo_total > 0 else 0.0
        )
        return {
            "drift": drift,
            "drift_pct": drift_pct,
            "mongo_total": mongo_total,
            "chroma_total": chroma_total,
            "recommendation": _classify(drift_pct),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[drift_alert] compute failed: %s", exc)
        return None


async def _emit(alert_type: str, title: str, message: str,
                metadata: dict, extra_bucket: Optional[str] = None) -> None:
    """Wrap the alert emitter so the scheduler never raises."""
    try:
        from services.ai_core_alerts import emit
        bucket = _today()
        if extra_bucket:
            bucket = f"{bucket}:{extra_bucket}"
        await emit(
            alert_type,
            title=title,
            message=message,
            metadata=metadata,
            date_bucket=bucket,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[drift_alert] emit failed: %s", exc)


async def check_and_alert() -> dict:
    """Scheduler entry — run one drift check and emit alerts if needed.

    Returns a summary dict for the scheduler log. The summary
    counters are small on-purpose — this is called every 5 minutes
    and runs mostly in the quiet path.
    """
    global _last_pct, _last_rebuild_alert_fired_on

    snap = await _compute_drift()
    if snap is None:
        return {"ok": False, "reason": "drift_compute_failed"}

    current_pct = snap["drift_pct"]
    prev_pct = _last_pct
    fired: list[str] = []

    # ── 1. Threshold crossing into rebuild band ──
    if current_pct >= REBUILD_PCT and (
        prev_pct is None or prev_pct < REBUILD_PCT
    ):
        await _emit(
            "memory_drift_rebuild_recommended",
            title="Mongo → Chroma drift: rebuild recommended",
            message=(
                f"Drift has crossed into the rebuild band "
                f"({current_pct}% ≥ {REBUILD_PCT}%). "
                f"{snap['drift']} Mongo rows unaccounted for in ChromaDB. "
                f"Consider POST /api/accuracy/memory/rebuild-from-mongo."
            ),
            metadata={
                "drift_pct": current_pct,
                "drift": snap["drift"],
                "mongo_total": snap["mongo_total"],
                "chroma_total": snap["chroma_total"],
                "prev_pct": prev_pct,
            },
        )
        _last_rebuild_alert_fired_on = _today()
        fired.append("rebuild")

    # ── 2. Sudden jump (positive direction only; a drop is good
    #       news and gets handled by the recovery branch) ──
    if (
        prev_pct is not None
        and current_pct - prev_pct > JUMP_PCT
    ):
        # Bucket on the actual from→to so multiple jumps in a day
        # still each fire once, but a repeated re-compute of the
        # same jump (e.g. restart mid-day) deduplicates.
        jump_bucket = f"jump:{prev_pct:.0f}->{current_pct:.0f}"
        await _emit(
            "memory_drift_jump",
            title="Mongo → Chroma drift jumped",
            message=(
                f"Drift rose from {prev_pct}% to {current_pct}% "
                f"(+{round(current_pct - prev_pct, 2)}pts) between "
                f"consecutive 5-minute checks. Investigate the sync "
                f"pipeline for an active regression."
            ),
            metadata={
                "drift_pct": current_pct,
                "prev_pct": prev_pct,
                "delta_pct": round(current_pct - prev_pct, 2),
                "drift": snap["drift"],
            },
            extra_bucket=jump_bucket,
        )
        fired.append("jump")

    # ── 3. Recovery — drift dropped back to ok band after
    #       we alerted earlier today ──
    if (
        current_pct < OK_PCT
        and _last_rebuild_alert_fired_on == _today()
    ):
        await _emit(
            "memory_drift_recovered",
            title="Mongo → Chroma drift back in sync",
            message=(
                f"Drift is back to {current_pct}% after earlier "
                f"rebuild alert. No further action needed."
            ),
            metadata={
                "drift_pct": current_pct,
                "prev_pct": prev_pct,
            },
            extra_bucket="recovered",
        )
        # Clear the flag so a later cross would fire a fresh alert.
        _last_rebuild_alert_fired_on = None
        fired.append("recovered")

    _last_pct = current_pct
    return {
        "ok": True,
        "current_pct": current_pct,
        "prev_pct": prev_pct,
        "recommendation": snap["recommendation"],
        "fired": fired,
    }
