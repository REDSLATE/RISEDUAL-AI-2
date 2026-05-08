"""Promotion-gate observability admin routes.

Extracted from ``routes/admin.py`` — surfaces three independent
promotion-gate / observability domains that share the same shape
(read-only, owner-gated, cold-start-is-normal envelope):

  * ``GET /api/admin/kraken-shadow/today``
  * ``GET /api/admin/adversarial-cores/24h``
  * ``GET /api/admin/adversarial-cores/promotion``
  * ``GET /api/admin/calibration/status``

URLs unchanged — no frontend / test edits required.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-promotion-gates"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/kraken-shadow/today")
async def kraken_shadow_today(request: Request):
    """Today's Kraken xStock shadow-compare summary for the burn-in card.

    Returns the same shape as ``summarize_today`` — rows count, max
    bps, p95 bps, divergent count vs threshold, alerts fired, last
    run, session counts, and a 5-row sample. Cheap single aggregation
    pass; safe to poll every 60 s alongside the rest of the burn-in.
    """
    await _require_owner(request)
    from services.kraken_equity_shadow_service import summarize_today
    return await summarize_today(db)


@router.get("/adversarial-cores/24h")
async def adversarial_cores_24h(request: Request):
    """At-a-glance read of the Bull/Bear/Commander core activity over
    the last 24 hours — decision counts, win rates (once closed rows
    exist), avg edge_gap and confidences, plus enabled + phase.

    Feeds the compact "Adversarial Cores" chip on the admin Terminal
    tab. Designed for the same cold-start-is-normal flow as the
    Kraken xStock shadow chip: zero rows returns a valid empty
    envelope rather than erroring.
    """
    await _require_owner(request)
    from services.adversarial_monitor import summarize_24h
    return await summarize_24h(db)


@router.get("/adversarial-cores/promotion")
async def adversarial_cores_promotion(request: Request):
    """Lifetime readiness for ``shadow → risk_only → veto → full``.

    Inform-only — never flips a phase env on its own. Returns the
    Commander-correct rate vs the next-transition floor, the
    Bull/Bear lifetime win-rate spread, and a copy-pastable
    ``promote_env_line`` for the operator to paste into
    ``backend/.env`` when ``ready_to_promote=true``.
    """
    await _require_owner(request)
    from services.adversarial_promotion_gate import compute_promotion_status
    return await compute_promotion_status(db)


@router.get("/calibration/status")
async def calibration_status(request: Request):
    """Active confidence-calibration model + last-fit telemetry.

    Inform-only — never refits on its own. Use the one-off script
    ``backend/scripts/fit_calibration_from_history.py`` to refit;
    the result is picked up automatically on the next request via
    ``services.calibration_service.get_active_calibration``.

    Returned shape::

        {
          "active": bool,
          "version": str | None,
          "fitted_at": ISO datetime | None,
          "n_rows": int,
          "ece_before_pp": float | None,
          "ece_after_pp": float | None,
          "max_calibrated_confidence": float,
          "calibration_applies_to": ["tier3_readiness_only"],
          "knots": [{"x": float, "y": float}, ...]
        }
    """
    await _require_owner(request)
    from services.calibration_service import (
        MAX_CALIBRATED_CONFIDENCE, get_active_calibration,
    )
    model = await get_active_calibration(db)
    if not model:
        return {
            "active": False,
            "version": None,
            "fitted_at": None,
            "n_rows": 0,
            "ece_before_pp": None,
            "ece_after_pp": None,
            "max_calibrated_confidence": MAX_CALIBRATED_CONFIDENCE,
            "calibration_applies_to": ["tier3_readiness_only"],
            "knots": [],
        }
    fitted_at = model.get("fitted_at")
    return {
        "active": bool(model.get("active")),
        "version": model.get("version"),
        "fitted_at": fitted_at.isoformat() if fitted_at else None,
        "n_rows": int(model.get("n_rows") or 0),
        "ece_before_pp": model.get("ece_before_pp"),
        "ece_after_pp": model.get("ece_after_pp"),
        "max_calibrated_confidence": float(
            model.get("max_calibrated_confidence") or MAX_CALIBRATED_CONFIDENCE,
        ),
        "calibration_applies_to": list(
            model.get("calibration_applies_to", ["tier3_readiness_only"]),
        ),
        "knots": list(model.get("knots") or []),
    }
