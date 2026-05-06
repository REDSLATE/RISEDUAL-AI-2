"""Compression CI Gate — admin endpoint surface.

Exposes the read-only IP guardrail (``scripts/compression_ci_gate``)
to the admin UI so operators can preview a candidate model's
regime-aware calibration drift directly from the dashboard before
promoting a quantized / pruned variant.

Owner-gated. Read-only on ``predictions``; the gate's *own* run
log is the only write path (``compression_gate_runs`` — pure audit
trail, never read by the live decision stack).

Endpoints:
  * ``GET  /api/admin/compression-ci-gate``           — evaluate
  * ``GET  /api/admin/compression-ci-gate/history``   — last-N runs
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter(prefix="/api/admin", tags=["admin-compression-gate"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


# Mongo collection name — kept as a module-level constant so a future
# admin UI can render it read-only and so any rename leaves a visible
# diff in code review.
COMPRESSION_GATE_RUNS = "compression_gate_runs"


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


def _recommended_action(
    verdict_label: str,
    breaches: list[str],
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
) -> str:
    """Derive a one-line operator recommendation from the verdict.

    Pure function — no IO, no side effects, easily unit-testable.
    Lives in the route module (not in ``scripts/compression_ci_gate``)
    so the IP-guardrail script stays untouched.

    Recommendation taxonomy:
      * PASS         — promote after manual sanity check
      * FAIL         — hold candidate; surface the worst breach
      * INCONCLUSIVE — keep collecting samples; quote the deficit
    """
    if verdict_label == "PASS":
        # Highlight the calibration win when both summaries
        # have weighted_avg_calibration_gap data.
        b_gap = baseline_summary.get("weighted_avg_calibration_gap")
        c_gap = candidate_summary.get("weighted_avg_calibration_gap")
        if b_gap is not None and c_gap is not None:
            delta = c_gap - b_gap
            verb = "improved" if delta < 0 else "held flat"
            return (
                f"Promote candidate after manual sanity check — "
                f"weighted gap {verb} ({b_gap:.4f} → {c_gap:.4f})."
            )
        return "Promote candidate after manual sanity check."

    if verdict_label == "INCONCLUSIVE":
        # The breach text already includes the deficit number; just
        # quote the first reason verbatim.
        first = breaches[0] if breaches else "insufficient samples"
        return (
            f"Hold the verdict — keep collecting samples. Reason: {first}"
        )

    # FAIL.
    if not breaches:
        # Defensive — FAIL with no breach text shouldn't happen, but
        # don't surface an empty recommendation if it does.
        return "Hold candidate — gate failed (no breach text returned)."
    worst = breaches[0]
    return (
        f"Hold candidate; do NOT promote. Worst breach: {worst}"
    )


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

    Persists every run into ``compression_gate_runs`` (audit trail
    only — never read by the live decision stack) so the operator
    has a historical view of gate verdicts over time.
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

    recommended_action = _recommended_action(
        verdict_label=verdict_label,
        breaches=verdict.breaches,
        baseline_summary=verdict.baseline_summary,
        candidate_summary=verdict.candidate_summary,
    )

    user = await _require_owner(request)
    run_id = str(uuid.uuid4())
    run_doc = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "operator": user.get("email") or user.get("user_id") or "unknown",
        "baseline_tag": baseline_tag,
        "candidate_tag": candidate_tag,
        "window_days": window_days,
        "verdict": verdict_label,
        "ok": verdict.ok,
        "inconclusive": verdict.inconclusive,
        "breaches": list(verdict.breaches),
        "baseline_summary": dict(verdict.baseline_summary),
        "candidate_summary": dict(verdict.candidate_summary),
        "recommended_action": recommended_action,
    }
    try:
        # Mongo mutates the dict in place to append _id; copy first
        # so the response we return below stays clean.
        await db[COMPRESSION_GATE_RUNS].insert_one(dict(run_doc))
    except Exception as exc:
        # Audit-trail write must never break the response. A logged
        # warning is enough — the verdict itself is the operator's
        # immediate concern.
        logger.warning("compression_ci_gate: history write failed: %s", exc)

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
        "recommended_action": recommended_action,
        "run_id": run_id,
    }


@router.get("/compression-ci-gate/history")
async def list_compression_gate_runs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
):
    """Last-N gate runs, newest first.

    Read-only — pure audit trail. ``_id`` excluded from the
    projection per the project-wide MongoDB-serialisation rule.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    cursor = db[COMPRESSION_GATE_RUNS].find(
        {},
        {"_id": 0},
    ).sort("ts", -1).limit(limit)
    rows = await cursor.to_list(length=limit)

    by_verdict: dict[str, int] = {}
    for r in rows:
        v = r.get("verdict", "?")
        by_verdict[v] = by_verdict.get(v, 0) + 1

    return {
        "total": len(rows),
        "by_verdict": by_verdict,
        "runs": rows,
    }
