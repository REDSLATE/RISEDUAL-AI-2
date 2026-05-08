"""Reliability-diagram calibration service.

Classic "decile reliability" analysis: bucket verified predictions
by confidence (0, 10, 20, …, 100), measure the hit-rate within each
bucket, and compare to the bucket label. A well-calibrated model has
hit_rate ≈ bucket_label across the full range.

This is the complement to the existing `/api/admin/conviction/calibration`
endpoint. That one buckets by conviction TIER (Weak/Moderate/Strong)
to audit sizing logic. This one buckets by raw confidence DECILE to
audit the underlying probability output — the upstream signal.

Design rules
------------
* Bucket rule: `round(confidence / 10) * 10` — produces 11 buckets
  (0, 10, 20, …, 100). Matches the pattern the user specified.
* Confidence is normalised to 0-100 before bucketing so rows stored
  on either the 0-1 fractional or 0-100 percent scale both land in
  the right place (same contract as `conviction_service`).
* Only rows with explicit `verified_24h.correct ∈ {True, False}` are
  counted. NEUTRAL outcomes (stored as `None`) are excluded — they
  don't carry a signal for calibration.
* ECE (Expected Calibration Error) is the standard summary metric.
  We compute the bucket-weighted gap between mean confidence and
  observed accuracy within each bucket.
* Fail-safe: any DB/parsing error returns an empty snapshot rather
  than raising — safe to call from hot admin paths.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def normalise_confidence(raw: Any) -> float | None:
    """Coerce a stored confidence value to a 0-100 float.

    Accepts `None`, ints, floats, or numeric strings. Returns `None`
    when the value is missing or unparseable.
    """
    if raw is None:
        return None
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return None
    if c <= 1.0:
        c *= 100.0
    return max(0.0, min(100.0, c))


def bucket_for(confidence: float) -> int:
    """Map a 0-100 confidence to its decile bucket label.

    Returns one of {0, 10, 20, …, 100}. This is the `round(c/10)*10`
    rule surfaced explicitly so callers can stay consistent.
    """
    return int(round(confidence / 10.0) * 10)


def compute_reliability(
    rows: list[dict],
    *,
    ece_threshold: float = 0.10,
) -> dict:
    """Pure-function core: build the reliability diagram from an
    already-fetched list of prediction dicts.

    Each row is expected to expose `confidence` (any numeric scale)
    and a truthy/falsy `correct`. Rows without either are skipped.

    Extracted so unit tests can drive the bucket logic without a DB.
    """
    # bucket → [total, correct, sum_conf]
    buckets: dict[int, list[float]] = {b: [0.0, 0.0, 0.0] for b in range(0, 101, 10)}
    total = 0
    total_correct = 0

    for row in rows:
        correct = row.get("correct")
        if correct is None:
            correct = (row.get("verified_24h") or {}).get("correct")
        if correct not in (True, False):
            continue
        conf = normalise_confidence(row.get("confidence"))
        if conf is None:
            continue
        b = bucket_for(conf)
        slot = buckets[b]
        slot[0] += 1
        slot[1] += 1.0 if correct else 0.0
        slot[2] += conf
        total += 1
        if correct:
            total_correct += 1

    output_buckets: list[dict] = []
    ece_num = 0.0
    for b in range(0, 101, 10):
        tot, corr, sconf = buckets[b]
        if tot == 0:
            output_buckets.append(
                {
                    "bucket": b,
                    "total": 0,
                    "correct": 0,
                    "accuracy": None,
                    "avg_confidence": None,
                    "gap": None,
                }
            )
            continue
        acc = corr / tot
        avg_conf = sconf / tot
        # Gap is expressed on the same scale as accuracy (0-1) so the
        # visual matches textbooks.
        gap = acc - (avg_conf / 100.0)
        ece_num += tot * abs(gap)
        output_buckets.append(
            {
                "bucket": b,
                "total": int(tot),
                "correct": int(corr),
                "accuracy": round(acc, 4),
                "avg_confidence": round(avg_conf, 2),
                "gap": round(gap, 4),
            }
        )

    ece = round(ece_num / total, 4) if total > 0 else None
    overall_accuracy = (total_correct / total) if total else None
    return {
        "total_verified": total,
        "total_correct": total_correct,
        "overall_accuracy": round(overall_accuracy, 4) if overall_accuracy is not None else None,
        "ece": ece,
        "well_calibrated": (ece is not None and ece < ece_threshold),
        "ece_threshold": ece_threshold,
        "buckets": output_buckets,
    }


async def reliability_snapshot(db: Any, days: int = 30) -> dict:
    """Full admin-endpoint payload. Fetches from `predictions`, runs
    :func:`compute_reliability`, and wraps with a timestamp + window.
    """
    snapshot: dict[str, Any] = {
        "lookback_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if db is None:
        snapshot.update(compute_reliability([]))
        return snapshot
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.predictions.find(
            {
                "verified_24h.correct": {"$in": [True, False]},
                "timestamp": {"$gte": since.isoformat()},
            },
            {
                "_id": 0,
                "confidence": 1,
                "verified_24h.correct": 1,
            },
        ).limit(10000)
        rows: list[dict] = []
        async for row in cursor:
            rows.append(row)
        snapshot.update(compute_reliability(rows))
    except Exception as exc:
        logger.warning("[reliability] snapshot failed: %s", exc)
        snapshot.update(compute_reliability([]))
    return snapshot
