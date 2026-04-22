"""Conviction scoring service — extracted from routes/risk_calculator.py so
every prediction-generation path (AI hypothesis, War Room, intelligence hub,
scanner, …) can tag rows with the same composite score the risk layer
uses for sizing. That way the Conviction Calibration admin panel becomes
a live closed-loop dashboard instead of a purely manual integration.

Design rules:
  * The score is `[0.0, 1.0]`. It MODULATES an already-approved risk
    budget (strong=100%, moderate=50%, weak=0%) — never bypasses a gate.
  * Every input is optional: missing `confidence` → neutral 0.5, missing
    calibration data → neutral 0.5, missing regime/streak → 0 (no credit
    and no penalty). This lets prediction-logging call-sites tag rows
    without needing full risk-manager context.
  * Weights are hand-tuned starting values. They're persisted on every
    prediction alongside the score, so once we have enough closed-trade
    outcomes a logistic regression can replace them without schema churn.
  * Fail-safe: any DB failure during computation degrades to a neutral
    score rather than crashing the caller. This is called from the hot
    prediction-logging path — it MUST NOT raise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Hand-tuned starting weights — every positive term must sum to 1.0 so a
# perfect-signal, perfectly-calibrated, regime-aligned prediction lands at
# exactly 1.0 before penalties. Penalties are additive and only subtract
# when tripped. Keep the sum-to-1 invariant when tuning.
CONVICTION_WEIGHTS = {
    "signal_confidence": 0.40,
    "calibration":       0.20,
    "regime_match":      0.15,
    "rejection_bias":    0.20,  # penalty
    "loss_streak":       0.15,  # penalty
}

# Tiers must be sorted high-to-low so the first match in _tier_from_score wins.
CONVICTION_TIERS = [
    (0.60, "strong",   1.00),
    (0.40, "moderate", 0.50),
    (0.00, "weak",     0.00),
]


def _tier_from_score(score: float) -> tuple[str, float]:
    for threshold, label, mult in CONVICTION_TIERS:
        if score >= threshold:
            return label, mult
    return "weak", 0.0


# Grade-aware outcome weights. STRONG_MISS carries 2× the penalty of
# WEAK_MISS so the learning signal reflects the actual cost asymmetry:
# a confident -5% blown trade matters more than a barely-wrong stop-out.
# Symmetric on the upside — STRONG_HIT earns 2× the reward so high-
# conviction winners dominate the signal. NEUTRAL is intentionally 0;
# noise-day drifts don't move conviction either way.
GRADE_WEIGHTS = {
    "STRONG_HIT":  +2.0,
    "WEAK_HIT":    +1.0,
    "NEUTRAL":      0.0,
    "WEAK_MISS":   -1.0,
    "STRONG_MISS": -2.0,
}


def score_prediction_outcome(grade: str, confidence: float) -> float:
    """Convert a graded outcome + confidence into a weighted learning signal.

    Output range: [-2.0, +2.0].
      * `grade` — one of the 5 prediction_tracker grades.
      * `confidence` — the ORIGINAL prediction confidence on 0-100
        scale. Callers that hold 0-1 fractions should scale to 100
        before calling (matches the `normalize_confidence()` contract
        used everywhere else).

    High-conviction wins and losses dominate. Low-conviction trades
    barely move the signal — which is correct: they shouldn't drive
    calibration in either direction.
    """
    base_weight = GRADE_WEIGHTS.get(grade, 0.0)
    conf_factor = max(0.0, min(1.0, float(confidence) / 100.0))
    return base_weight * conf_factor


async def _calibration_expectancy(
    db, user_id: Optional[str], lookback_days: int = 30
) -> float:
    """Trailing expectancy score normalised to [0, 1] for the conviction
    calibration component.

    Replaces the old "win-rate fraction" metric with a grade-weighted
    expectancy. A trader who is RIGHT AT HIGH CONFIDENCE scores above
    the neutral 0.5 anchor; a trader who is WRONG AT HIGH CONFIDENCE
    scores below. Neutral trades (noise-day drifts) contribute 0 —
    exactly the "stop punishing drift" behaviour we locked in upstream.

    Returns 0.5 when we lack data (neutral anchor — no whiplash). We
    require at least 10 graded rows; below that the mean is too noisy
    to drive sizing decisions.

    Mapping from raw expectancy (range [-2, +2]) to [0, 1]:
        mapped = (mean_score + 2) / 4
    So:
        mean=+2 (all STRONG_HIT at 100% conf) → 1.0
        mean= 0 (neutral / no data)            → 0.5
        mean=-2 (all STRONG_MISS at 100% conf) → 0.0
    """
    if db is None or not user_id:
        return 0.5
    try:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cursor = db.predictions.find(
            {
                "user_id": user_id,
                "verified_24h.grade": {"$exists": True},
                "timestamp": {"$gte": since.isoformat()},
            },
            {"_id": 0, "verified_24h.grade": 1, "confidence": 1},
        ).limit(500)

        total_score = 0.0
        total_rows = 0
        async for row in cursor:
            grade = (row.get("verified_24h") or {}).get("grade")
            if not grade:
                continue
            # `predictions.confidence` is stored on 0-1 scale — scale
            # up to 0-100 so `score_prediction_outcome` does the right
            # thing.
            conf = float(row.get("confidence") or 0)
            if conf <= 1.0:
                conf *= 100.0
            total_score += score_prediction_outcome(grade, conf)
            total_rows += 1

        if total_rows < 10:
            return 0.5
        mean_score = total_score / total_rows
        return max(0.0, min(1.0, (mean_score + 2.0) / 4.0))
    except Exception as e:
        logger.warning(f"[conviction] expectancy lookup failed: {e}")
        return 0.5


async def _calibration_win_rate(db, user_id: Optional[str], lookback_days: int = 30) -> float:
    """Thin backwards-compat wrapper. Delegates to the new
    grade-aware expectancy scorer. Kept under the old name so any
    direct imports from tests/scripts don't break."""
    return await _calibration_expectancy(db, user_id, lookback_days)


async def _is_flagged(db, asset: str, direction: str) -> bool:
    """Returns True if the (asset, direction) pair is currently on the
    rejection-bias blacklist. Any DB error degrades to False (no penalty)
    — better to under-penalise than to crash the prediction logger.
    """
    if db is None or not asset or not direction:
        return False
    try:
        from services.rejection_log import get_flagged_pairs
        flagged = await get_flagged_pairs()
        return (asset.upper(), direction.upper()) in flagged
    except Exception as e:
        logger.warning(f"[conviction] bias lookup failed: {e}")
        return False


async def compute_conviction(
    db,
    *,
    user_id: Optional[str],
    asset: Optional[str],
    direction: Optional[str],
    confidence: Optional[float],
    regime_match: Optional[bool] = None,
    risk_ctx: Optional[dict] = None,
) -> dict:
    """Compute the composite conviction score + tier + size multiplier.

    Returns a dict with the final score, tier label, size multiplier, the
    active weights, and a per-component breakdown for UI/debug transparency.
    Safe to call from any async context — fails gracefully to a neutral
    score when DB lookups error out. Never raises.
    """
    try:
        w = CONVICTION_WEIGHTS
        components: dict[str, float] = {}

        # 1. Signal confidence — normalise both 0-1 floats and 0-100 percentages.
        if confidence is None:
            conf = 0.5
        else:
            c = float(confidence)
            conf = max(0.0, min(1.0, c / 100.0 if c > 1.0 else c))
        components["signal_confidence"] = round(conf * w["signal_confidence"], 4)

        # 2. Calibration — grade-weighted expectancy. Replaces the old
        #    naive win-rate so a single high-conf STRONG_MISS counts
        #    more than 4 low-conf WEAK_MISSes, aligning training
        #    signal with actual trading economics. Falls back to
        #    0.5 neutral when data is sparse.
        calibration = await _calibration_expectancy(db, user_id)
        components["calibration"] = round(calibration * w["calibration"], 4)

        # 3. Regime match — positive only when explicit True. Unknown or
        #    mismatch contributes 0 (no credit, no penalty).
        components["regime_match"] = round(
            w["regime_match"] if regime_match is True else 0.0, 4
        )

        # 4. Rejection-bias penalty.
        penalty_bias = 0.0
        if direction and asset:
            if await _is_flagged(db, asset, direction):
                penalty_bias = w["rejection_bias"]
        components["rejection_bias_penalty"] = round(-penalty_bias, 4)

        # 5. Loss-streak penalty (mirrors the risk-calc circuit-breaker
        #    threshold). When called from prediction-logging there's no
        #    `risk_ctx`, so this contributes 0.
        penalty_streak = 0.0
        if (risk_ctx or {}).get("losing_streak", 0) >= 4:
            penalty_streak = w["loss_streak"]
        components["loss_streak_penalty"] = round(-penalty_streak, 4)

        score = (
            components["signal_confidence"]
            + components["calibration"]
            + components["regime_match"]
            + components["rejection_bias_penalty"]
            + components["loss_streak_penalty"]
        )
        score = max(0.0, min(1.0, score))
        tier_label, size_mult = _tier_from_score(score)

        return {
            "score": round(score, 3),
            "tier": tier_label,
            "size_multiplier": size_mult,
            "weights": dict(w),
            "breakdown": components,
            "inputs": {
                "confidence": conf,
                "calibration": round(calibration, 4),
                "regime_match": regime_match,
                "losing_streak": (risk_ctx or {}).get("losing_streak", 0),
            },
        }
    except Exception as e:
        # Belt-and-braces — we promised never to raise. Return a neutral
        # record so upstream callers can still persist a row.
        logger.warning(f"[conviction] compute failed, returning neutral: {e}")
        return {
            "score": 0.5,
            "tier": "moderate",
            "size_multiplier": 0.5,
            "weights": dict(CONVICTION_WEIGHTS),
            "breakdown": {"error": str(e)},
            "inputs": {},
        }
