"""Conviction clamp canary — counts how many prediction outcomes hit
the `score_prediction_outcome` boundary (±2.5) in the lookback window.

Current `GRADE_WEIGHTS` top out at ±2.0, so the natural clamp rate
should be **exactly zero**. Any non-zero count means either:

  * `GRADE_WEIGHTS` was edited to exceed the boundary (intentional —
    but someone forgot to bump `MAX_PENALTY`/`MIN_REWARD` in tandem).
  * A confidence-scale bug is pushing values beyond the bounds.

Either way it's a loud canary — cheaper than grepping server logs
and more reliable than hoping the regression test caught it.

Runs read-only against `predictions` — safe to call from hot paths.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services.conviction_service import (
    MAX_PENALTY,
    MIN_REWARD,
    score_prediction_outcome,
)

logger = logging.getLogger(__name__)

# Floating-point drift slack: treat anything within 1e-6 of the bound
# as "at the clamp". Avoids spurious zeros from rounding noise.
_EPSILON: float = 1e-6


async def conviction_clamp_counter(db: Any, days: int = 30) -> dict:
    """Count predictions whose weighted learning signal hit the clamp.

    Returns a JSON-safe dict with:
      * `lookback_days`        — window size
      * `total_graded`         — prediction rows with a grade in window
      * `clamp_high`           — count at +MIN_REWARD
      * `clamp_low`            — count at MAX_PENALTY
      * `clamp_total`          — sum of high + low
      * `clamp_rate_pct`       — clamp_total / total_graded * 100
      * `status`               — "ok" when clamp_total == 0,
                                 "warn" otherwise
      * `max_penalty`, `min_reward` — active bounds for UI display
      * `generated_at`         — ISO timestamp

    Fails to an "ok" zero-count snapshot on any error (we never want
    a DB blip to trigger a false canary).
    """
    snapshot: dict[str, Any] = {
        "lookback_days": days,
        "total_graded": 0,
        "clamp_high": 0,
        "clamp_low": 0,
        "clamp_total": 0,
        "clamp_rate_pct": 0.0,
        "status": "ok",
        "max_penalty": MAX_PENALTY,
        "min_reward": MIN_REWARD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if db is None:
        return snapshot
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.predictions.find(
            {
                "verified_24h.grade": {"$exists": True},
                "timestamp": {"$gte": since.isoformat()},
            },
            {"_id": 0, "verified_24h.grade": 1, "confidence": 1},
        ).limit(5000)

        total = 0
        hi = 0
        lo = 0
        async for row in cursor:
            grade = (row.get("verified_24h") or {}).get("grade")
            if not grade:
                continue
            conf = float(row.get("confidence") or 0)
            if conf <= 1.0:
                conf *= 100.0
            score = score_prediction_outcome(grade, conf)
            total += 1
            if score >= MIN_REWARD - _EPSILON:
                hi += 1
            elif score <= MAX_PENALTY + _EPSILON:
                lo += 1

        clamp_total = hi + lo
        rate = (100.0 * clamp_total / total) if total else 0.0
        snapshot.update(
            {
                "total_graded": total,
                "clamp_high": hi,
                "clamp_low": lo,
                "clamp_total": clamp_total,
                "clamp_rate_pct": round(rate, 3),
                "status": "ok" if clamp_total == 0 else "warn",
            }
        )
    except Exception as exc:
        logger.warning("[clamp-canary] lookup failed: %s", exc)

    return snapshot
