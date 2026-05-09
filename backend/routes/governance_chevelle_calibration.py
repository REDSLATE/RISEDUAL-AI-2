"""Chevelle calibration governance — owner-only refit + status.

────────────────────────────────────────────────────────────────────
AUTHORITY-BOUNDARY CONTRACT
────────────────────────────────────────────────────────────────────

Three operator-facing endpoints:

  * ``POST /api/governance/chevelle/calibration/refit`` — owner-only.
    Pulls firewall-trainable rows from ``paper_trades`` (and the
    crypto / options counterparts), runs them through the Chevelle
    Memory Labeling Firewall, fits a fresh isotonic calibrator, and
    persists a versioned joblib artifact.

  * ``GET  /api/governance/chevelle/calibration/status`` —
    returns the active version, sample count, ECE/Brier metrics,
    reliability bins (for the Patent J card), and apply-health
    flags (calibrator_loaded / stale).

  * ``GET  /api/governance/chevelle/calibration/reliability`` —
    convenience accessor for the Patent J card. Same shape as
    ``status`` but scoped to the reliability bins payload.

Hard rules
----------
  * NO BUY / SELL emission.
  * NO threshold lowering.
  * NO promotion auto-unlock.
  * NO broker / executor / Strategist / Auditor / Commander imports.
  * Quarantined rows are FILTERED by
    ``services.chevelle_memory_labeler.trainable_only`` before
    fitting — no override.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from routes.auth import get_current_user
from services.calibration_layer import (
    fit_and_persist,
    reliability_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/governance/chevelle/calibration",
    tags=["governance", "chevelle", "calibration"],
)


# ── Auth helper ─────────────────────────────────────────────────────


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


# ── Internal: pull candidate rows ────────────────────────────────────


async def _pull_recent_outcomes(db, *, days: int = 30) -> list[dict]:
    """Read closed paper trades from the last ``days`` days.

    Columns we need:
      * ``opened_at`` / ``closed_at``
      * ``symbol``
      * ``confidence`` (raw, pre-calibration)
      * ``direction``
      * ``pnl_usd`` (or ``pnl``) for the WIN/LOSS label
      * ``source``
      * ``lane`` / ``asset_type``
    """
    if db is None:
        return []
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict] = []
    for collection_name, default_lane in (
        ("paper_trades", "equity"),
        ("crypto_paper_trades", "crypto"),
    ):
        try:
            cursor = db[collection_name].find(
                {
                    "closed_at": {"$ne": None, "$gte": cutoff},
                    "status": {"$in": ["closed", "filled"]},
                },
                {"_id": 0},
            ).limit(5000)
            async for doc in cursor:
                if "lane" not in doc and default_lane:
                    doc["lane"] = default_lane
                rows.append(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[calibration_refit] read %s failed: %s",
                collection_name, exc,
            )
    return rows


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/refit")
async def refit(request: Request) -> dict:
    """Refit the active isotonic calibrator from recent outcomes."""
    await _require_owner(request)
    db = _get_db()
    rows = await _pull_recent_outcomes(db, days=30)
    result = fit_and_persist(rows)
    return {
        "success": result.success,
        "sample_count": result.sample_count,
        "model_version": result.model_version,
        "artifact_path": result.artifact_path,
        "rejected_reason": result.rejected_reason,
        "metrics": result.metrics,
        "rows_pulled": len(rows),
    }


@router.get("/status")
async def status(request: Request) -> dict:
    """Snapshot for the Patent J card."""
    await _require_owner(request)
    db = _get_db()
    rows = await _pull_recent_outcomes(db, days=30)
    return reliability_snapshot(rows, n_bins=10)


@router.get("/reliability")
async def reliability(request: Request) -> dict:
    """Convenience accessor — same payload as status."""
    return await status(request)


# ── DB resolver (lazy; matches public_access pattern) ───────────────


def _get_db() -> Any:
    from server import db as _db
    return _db


# ── Scheduler hook (registered from services.scheduling.jobs) ───────


async def run_calibration_refit_job(db: Any) -> dict:
    """Daily-cadence job. Runs the same refit pipeline the admin
    endpoint runs but reads ``db`` directly (no auth — already
    inside the trusted scheduler boundary). Never raises."""
    try:
        rows = await _pull_recent_outcomes(db, days=30)
        result = fit_and_persist(rows)
        logger.info(
            "[calibration_refit_job] success=%s "
            "sample_count=%d version=%s",
            result.success, result.sample_count,
            result.model_version,
        )
        return {
            "success": result.success,
            "sample_count": result.sample_count,
            "model_version": result.model_version,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration_refit_job] failed: %s", exc)
        return {"success": False, "error": str(exc)}
