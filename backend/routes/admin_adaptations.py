"""ML adaptations admin surface — list / revert / disable / calibrate / explain.

Extracted from ``routes/admin.py`` (~3666 lines, on the size allowlist)
to keep the admin surface domain-modular. URLs unchanged so no
frontend or test edits are needed:

  * ``GET  /api/admin/adaptations``
  * ``POST /api/admin/adaptations/{adaptation_id}/revert``
  * ``POST /api/admin/adaptations/disable_all``
  * ``GET  /api/admin/adaptations/calibration``
  * ``GET  /api/admin/adaptations/why/{adaptation_id}``

Owner-gated for write paths and the calibration roll-up; admin-gated
for the read-only ``why`` explainer (mirrors ``alerts/why`` in
``admin.py``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-adaptations"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def _require_owner(request: Request):
    """Stricter gate: only owner role passes. Mirrors the helper in
    ``routes/admin.py`` — duplicated so this module has no inbound
    dependency on admin.py."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/adaptations")
async def list_adaptations(request: Request):
    """Active model adaptations — bounded row-weight adjustments
    applied at retrain time based on recent toxic-alert patterns.
    Owner-gated (touches ML behaviour).

    Also carries the most recent ``adaptation_impact`` block from
    the ml_training_log — callers surface ΔR / Δwin-rate so admins
    can tell at a glance whether the adaptation set is actually
    moving the expected outcome or just shuffling weights."""
    await _require_owner(request)
    if db is None:
        return {"items": [], "enabled": False, "total": 0,
                "last_impact": None, "recent_auto_reverts": []}
    from services.model_adaptation import (
        adaptation_enabled,
        list_active_adaptations,
    )
    items = await list_active_adaptations(db)

    # Most recent retrain row carrying an adaptation_impact block.
    # Older rows (pre-upgrade) don't have it — we silently skip.
    last_impact = None
    try:
        run = await db["ml_training_log"].find_one(
            {"adaptation_impact": {"$exists": True}},
            {"_id": 0, "adaptation_impact": 1, "adaptations_applied": 1,
             "started_at": 1, "finished_at": 1, "model_version": 1},
            sort=[("started_at", -1)],
        )
        if run:
            ts = run.get("finished_at") or run.get("started_at")
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            per_ad = []
            for row in run.get("adaptations_applied") or []:
                if "delta_mean_r" in row or "delta_win_rate" in row:
                    per_ad.append({
                        "adaptation_id": row.get("adaptation_id"),
                        "metric": row.get("metric"),
                        "direction": row.get("direction"),
                        "rows_matched": row.get("rows_matched"),
                        "delta_mean_r": row.get("delta_mean_r"),
                        "delta_win_rate": row.get("delta_win_rate"),
                    })
            last_impact = {
                "at": ts,
                "model_version": run.get("model_version"),
                "global": run["adaptation_impact"],
                "per_adaptation": per_ad,
            }
    except Exception:
        last_impact = None

    # Recent auto-revert/auto-soften — surfaces the safety-rail
    # activity in the same panel so admins see gradient
    # de-escalation (soften) AND final flips (revert) side-by-side.
    # Last 10 is plenty; older entries live in the
    # `adaptation_audit` collection for long-range queries.
    recent_auto_reverts: list[dict] = []
    try:
        cursor = (
            db["adaptation_audit"]
            .find(
                {"action": {"$in": [
                    "auto_revert", "auto_soften",
                    "shadow_revert", "shadow_soften",
                ]}},
                {"_id": 0},
            )
            .sort("at", -1)
            .limit(10)
        )
        async for row in cursor:
            recent_auto_reverts.append(row)
    except Exception:
        recent_auto_reverts = []

    return {
        "items": items,
        "enabled": adaptation_enabled(),
        "total": len(items),
        "last_impact": last_impact,
        "recent_auto_reverts": recent_auto_reverts,
    }


@router.post("/adaptations/{adaptation_id}/revert")
async def revert_adaptation_endpoint(adaptation_id: str, request: Request):
    """Flip one adaptation to inactive — the next retrain will
    ignore it. Audit trail preserved (not deleted)."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.model_adaptation import revert_adaptation
    ok = await revert_adaptation(db, adaptation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Adaptation not found or already inactive")
    return {"status": "reverted", "adaptation_id": adaptation_id}


@router.post("/adaptations/disable_all")
async def disable_all_adaptations_endpoint(request: Request):
    """Kill switch — deactivates every active adaptation at once.
    Use when the retrain-level adaptation loop is misbehaving and
    you want model weights back to pristine severity+regime-only
    on the next retrain."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.model_adaptation import disable_all_adaptations
    n = await disable_all_adaptations(db)
    return {"status": "disabled_all", "deactivated": n}


@router.get("/adaptations/calibration")
async def adaptations_calibration(request: Request, window_days: int = 30):
    """Distribution analysis of shadow-mode safety-rail observations.

    Reads the last ``window_days`` of ``adaptation_audit`` rows
    tagged ``shadow_soften`` / ``shadow_revert`` and computes
    per-metric percentile distributions of ``decision_score``,
    ``decision_ratio`` and ``delta_r_trend``. Pairs that with the
    CURRENT live thresholds (pulled from
    ``get_auto_revert_config``) so operators can see at a glance:

      * what the scanner has been seeing in shadow mode,
      * which threshold each observation cleared,
      * a recommended tuned threshold (the p25 of observed scores,
        so flipping live would act on the strongest 75% of
        observations — the rest land in noise territory).

    The recommendation is advisory, not auto-applied — set
    ``ML_AUTO_REVERT_EFFECT_SIZE`` (and siblings) in the env and
    restart supervisor to adopt it.

    Owner-gated. Runs read-only; safe to call repeatedly.
    """
    await _require_owner(request)
    if db is None:
        return {
            "window_days": window_days,
            "observations": 0,
            "current_config": {},
            "distribution": {},
            "recommendation": None,
            "note": "Database unavailable",
        }
    from services.model_adaptation import get_auto_revert_config

    cfg = get_auto_revert_config()
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, window_days))).isoformat()

    cursor = db["adaptation_audit"].find(
        {
            "action": {"$in": ["shadow_soften", "shadow_revert",
                                "auto_soften", "auto_revert"]},
            "at": {"$gte": since},
        },
        {"_id": 0},
    ).sort("at", -1).limit(1000)

    scores: list[float] = []
    ratios: list[float] = []
    trends: list[float] = []
    shadow_count = 0
    live_count = 0
    by_action: dict[str, int] = {}
    by_metric: dict[str, dict[str, Any]] = {}
    async for row in cursor:
        ds = row.get("decision_score")
        dr_ratio = row.get("decision_ratio")
        dtrend = row.get("delta_r_trend")
        try:
            if ds is not None:
                scores.append(float(ds))
            if dr_ratio is not None:
                ratios.append(float(dr_ratio))
            if dtrend is not None:
                trends.append(float(dtrend))
        except (TypeError, ValueError):
            continue
        if row.get("shadow"):
            shadow_count += 1
        else:
            live_count += 1
        action = row.get("action") or "unknown"
        by_action[action] = by_action.get(action, 0) + 1
        metric = row.get("metric") or "unknown"
        bucket = by_metric.setdefault(metric, {"n": 0, "scores": []})
        bucket["n"] += 1
        if ds is not None:
            try:
                bucket["scores"].append(float(ds))
            except (TypeError, ValueError):
                pass

    def _percentiles(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"n": 0, "min": None, "p25": None, "p50": None,
                    "p75": None, "p90": None, "max": None, "mean": None}
        ordered = sorted(values)
        n = len(ordered)

        def pick(p: float) -> float:
            idx = max(0, min(n - 1, int(round((n - 1) * p))))
            return round(ordered[idx], 6)

        return {
            "n": n,
            "min": round(ordered[0], 6),
            "p25": pick(0.25),
            "p50": pick(0.50),
            "p75": pick(0.75),
            "p90": pick(0.90),
            "max": round(ordered[-1], 6),
            "mean": round(sum(ordered) / n, 6),
        }

    score_dist = _percentiles(scores)
    ratio_dist = _percentiles(ratios)
    trend_dist = _percentiles(trends)

    # ── Per-metric roll-up ──
    # Strip the raw score list from the response so we don't ship
    # 1000 floats back, but compute a p50 per metric — the single
    # number that tells admins "this rule is consistently over
    # the threshold" vs "this rule is borderline".
    per_metric: list[dict[str, Any]] = []
    for metric, bucket in by_metric.items():
        raw = bucket["scores"]
        if raw:
            raw_sorted = sorted(raw)
            median = raw_sorted[len(raw_sorted) // 2]
        else:
            median = None
        per_metric.append({
            "metric": metric,
            "observations": bucket["n"],
            "median_score": round(median, 6) if median is not None else None,
        })
    per_metric.sort(key=lambda r: r["observations"], reverse=True)

    # ── Recommendation ──
    # Use the 25th percentile of observed decision_scores as the
    # suggested new threshold. Rationale: 75% of the shadow rail's
    # would-be actions would still fire (the high-signal ones),
    # the bottom quartile (borderline/noisy) would get filtered.
    # We cap below at 0.0001 so a degenerate shadow dataset can't
    # recommend a threshold of zero.
    MIN_OBS_FOR_RECOMMENDATION = 20
    recommendation: dict[str, Any] | None = None
    if score_dist["n"] and score_dist["n"] >= MIN_OBS_FOR_RECOMMENDATION:
        suggested = max(float(score_dist["p25"] or 0.0), 0.0001)
        delta = suggested - cfg["effect_size"]
        pct_change = (delta / cfg["effect_size"] * 100) if cfg["effect_size"] > 0 else None
        direction = "tighten" if suggested > cfg["effect_size"] else "loosen"
        recommendation = {
            "suggested_effect_size": round(suggested, 6),
            "current_effect_size": cfg["effect_size"],
            "direction": direction,
            "delta": round(delta, 6),
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
            "rationale": (
                "Tuned to the 25th percentile of observed shadow "
                "decision_scores — would act on the strongest 75% "
                "of signals and filter the noisy tail."
            ),
            "apply_via": "Set ML_AUTO_REVERT_EFFECT_SIZE in the env, then restart supervisor.",
        }
    elif score_dist["n"]:
        recommendation = {
            "suggested_effect_size": None,
            "current_effect_size": cfg["effect_size"],
            "note": (
                f"Need ≥{MIN_OBS_FOR_RECOMMENDATION} observations "
                f"before recommending; currently have {score_dist['n']}."
            ),
        }

    return {
        "window_days": window_days,
        "observations": score_dist["n"],
        "shadow_observations": shadow_count,
        "live_observations": live_count,
        "by_action": by_action,
        "current_config": cfg,
        "distribution": {
            "decision_score": score_dist,
            "decision_ratio": ratio_dist,
            "delta_r_trend": trend_dist,
        },
        "per_metric": per_metric,
        "recommendation": recommendation,
    }


# ── Metric → plain-English narrator. Keeps the "why" payload
#    self-contained so the UI doesn't need a second hop to make
#    sense of it. Lift is rendered as N.NNx so non-ML admins can
#    read it at a glance ("1.52× more often than baseline").
def _explain_adaptation_metric(metric: str, lift: float | None,
                               direction: str | None) -> str:
    suffix = ""
    if direction and direction not in ("ANY", None):
        side = "bullish" if direction == "LONG" else "bearish"
        suffix = f" on the {side} side"
    lift_s = f"{lift:.2f}×" if lift is not None else "elevated"
    if metric.startswith("volume.liquidity"):
        return f"Low-liquidity setups failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("volume.spike"):
        return f"Panic-volume setups failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("rsi.overbought"):
        return f"Overbought (RSI > 70) rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("rsi.oversold"):
        return f"Oversold (RSI < 30) rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("macd.crossover"):
        return f"Bearish-MACD rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("sector.momentum"):
        return f"Negative-sector-momentum rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("sentiment.negative"):
        return f"Negative-sentiment rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("pattern."):
        pretty = metric.replace("pattern.", "").replace("_", " ")
        return f"The {pretty} pattern failed {lift_s} more often than baseline{suffix}."
    return f"This metric showed an elevated failure rate ({lift_s} vs baseline){suffix}."


@router.get("/adaptations/why/{adaptation_id}")
async def explain_adaptation(adaptation_id: str, request: Request):
    """Explain a specific adaptation — what failed, how much more
    often than baseline, and what the retrain will do about it.

    This is the second half of the "closed-loop explainability"
    story: ``/alerts/why/{alert_id}`` explains a failure, this
    endpoint explains the adaptation that was derived from one or
    more of those failures. Admin-gated."""
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc = await db["model_adaptations"].find_one(
        {"adaptation_id": adaptation_id}, {"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Adaptation not found")

    metric = doc.get("metric", "")
    direction = doc.get("direction") or "ANY"
    factor = float(doc.get("adjustment_factor") or 1.0)
    lift = doc.get("contrast")
    try:
        lift_f: float | None = float(lift) if lift is not None else None
    except (TypeError, ValueError):
        lift_f = None
    bucket_rate = doc.get("bucket_rate")
    global_rate = doc.get("global_rate")
    severity = doc.get("severity")
    evidence = int(doc.get("evidence_count") or 0)
    active = bool(doc.get("active", False))

    pct_down = max(0, round((1.0 - factor) * 100))
    projected_effect = (
        f"Reduces influence of matching setups by ~{pct_down}% in the next retrain"
        if pct_down > 0 else
        "No weight reduction (factor at or above 1.0)"
    )

    explanation = _explain_adaptation_metric(metric, lift_f, direction)

    expires_at = doc.get("expires_at")
    if hasattr(expires_at, "isoformat"):
        expires_at = expires_at.isoformat()

    return {
        "adaptation_id": adaptation_id,
        "metric": metric,
        "direction": direction,
        "factor": round(factor, 3),
        "weight_reduction_pct": pct_down,
        "lift": round(lift_f, 3) if lift_f is not None else None,
        "bucket_rate": round(float(bucket_rate), 4) if bucket_rate is not None else None,
        "global_rate": round(float(global_rate), 4) if global_rate is not None else None,
        "severity": round(float(severity), 4) if severity is not None else None,
        "evidence_count": evidence,
        "active": active,
        "expires_at": expires_at,
        "description": doc.get("description"),
        "explanation": explanation,
        "projected_effect": projected_effect,
    }
