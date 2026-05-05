"""Compression CI Gate — admin endpoint surface.

Exposes the read-only IP guardrail (``scripts/compression_ci_gate``)
to the admin UI so operators can preview a candidate model's
regime-aware calibration drift directly from the dashboard before
promoting a quantized / pruned variant.

Owner-gated. Read-only. Calls the same ``evaluate_gate`` pure
function used by the Makefile + pytest entry points — single source
of truth for the gate logic.

Endpoints:
  * ``GET /api/admin/compression-ci-gate``
    Query params:
      - ``baseline_tag`` (required) — ``predictions.model_version``
        of the baseline model
      - ``candidate_tag`` (required) — ``predictions.model_version``
        of the candidate (compressed) model
      - ``window_days`` (default 30, max 180)

The endpoint never performs writes or affects sizing / direction /
memory promotion. Pure analytics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter(prefix="/api/admin", tags=["admin-compression-gate"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    """Owner-only — gate verdict reveals model version names + raw
    sample counts which are business-sensitive."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/compression-ci-gate")
async def evaluate_compression_gate(
    request: Request,
    baseline_tag: str = Query(
        ..., description="model_version of the baseline model "
        "(matches predictions.model_version)"
    ),
    candidate_tag: str = Query(
        ..., description="model_version of the candidate (compressed) model"
    ),
    window_days: int = Query(30, ge=1, le=180),
):
    """Run the read-only compression CI gate and return the verdict.

    Mirrors ``make compression-baseline`` / ``pytest`` entry points —
    same pure ``evaluate_gate`` function under the hood. Surfaces:
      * ``verdict``: ``"PASS"`` / ``"FAIL"`` / ``"INCONCLUSIVE"``
      * ``ok``, ``inconclusive``, ``breaches`` (raw GateVerdict shape)
      * ``baseline_summary``, ``candidate_summary``
      * ``baseline_tag``, ``candidate_tag``, ``window_days``
        (echoed for UI rendering)
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    if baseline_tag == candidate_tag:
        raise HTTPException(
            status_code=400,
            detail="baseline_tag and candidate_tag must differ",
        )

    # Lazy import — keeps the gate's heavy dependency surface out of
    # the cold-start path; this endpoint is admin-only and rarely hit.
    from scripts.compression_ci_gate import (
        _load_resolved,
        _to_resolved_rows,
        evaluate_gate,
    )

    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    try:
        baseline_raw = await _load_resolved(db, baseline_tag, since)
        candidate_raw = await _load_resolved(db, candidate_tag, since)
    except Exception as exc:
        logger.exception("compression_ci_gate: load failed")
        raise HTTPException(
            status_code=500,
            detail=f"failed to load predictions: {exc}",
        ) from exc

    verdict = evaluate_gate(
        _to_resolved_rows(baseline_raw),
        _to_resolved_rows(candidate_raw),
    )

    if verdict.inconclusive:
        verdict_label = "INCONCLUSIVE"
    elif verdict.ok:
        verdict_label = "PASS"
    else:
        verdict_label = "FAIL"

    return {
        "baseline_tag": baseline_tag,
        "candidate_tag": candidate_tag,
        "window_days": window_days,
        "verdict": verdict_label,
        "ok": verdict.ok,
        "inconclusive": verdict.inconclusive,
        "breaches": verdict.breaches,
        "baseline_summary": verdict.baseline_summary,
        "candidate_summary": verdict.candidate_summary,
    }
