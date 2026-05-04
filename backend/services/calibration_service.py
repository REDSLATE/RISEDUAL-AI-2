"""
Confidence calibration service — fits + applies isotonic calibration to
the model's raw confidence so the Tier 3 readiness gate measures *true*
model edge instead of the model's chronic under-confidence.

Hard scope (pinned by the operator on 2026-05-04)
─────────────────────────────────────────────────
* The active model is read by ``services.tier3_readiness`` ONLY.
  Sizing (``ai_core/sizing.compute_*``), the trade execution gate
  (``MIN_CONFIDENCE_TO_TRADE``), and the signal-bot quantity scaling
  in ``trading_bot_service.py`` keep reading the RAW ``confidence``
  field. Lowering the readiness gate by recalibrating must NOT
  silently widen the trading universe — that decision is reserved
  for an explicit operator review.
* Every persisted prediction carries the raw ``confidence`` AND a
  ``calibrated_confidence`` (when an active model exists) plus the
  audit fields below. Raw confidence is never overwritten.
* The persisted calibration doc carries
  ``calibration_applies_to=["tier3_readiness_only"]`` to make the
  intent explicit at the data layer — any future caller that reads
  the field has to acknowledge the scope or the audit fails.

Schema (per prediction row)
───────────────────────────
::

    {
      "confidence": 0.564,                     # raw, never overwritten
      "calibrated_confidence": 0.913,          # written when model active
      "calibration_version": "isotonic_2026_05_04",
      "calibration_source": "prediction_24h_verified_rows",
      "calibration_n": 147,
      "calibration_applies_to": ["tier3_readiness_only"]
    }

Schema (per ``calibration_models`` doc)
───────────────────────────────────────
::

    {
      "_id": ObjectId(...),
      "version": "isotonic_2026_05_04T03_15_00",
      "active": true,                          # only one row at a time
      "fitted_at": datetime,
      "lookback_days": 90,
      "n_rows": 147,
      "ece_before_pp": 35.35,                  # weighted absolute gap, pp
      "ece_after_pp": 4.21,
      "max_calibrated_confidence": 0.95,
      "calibration_applies_to": ["tier3_readiness_only"],
      "knots": [
        {"x": 0.41, "y": 0.88},                # raw → calibrated
        {"x": 0.56, "y": 0.93},
        ...
      ]
    }

Pure-function discipline
────────────────────────
``apply_calibration`` is a pure function. ``fit_isotonic_calibration``
and ``get_active_calibration`` touch Mongo but never raise into the
caller — a calibration outage degrades to "no calibration applied",
which means the Tier 3 readiness reverts to reading raw confidence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sklearn.isotonic import IsotonicRegression

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────

# How far back the fitter pulls verified rows. 90 days strikes the
# balance between stability (more data → less noisy fit) and
# reactivity (model improves over time, ancient calibration drifts).
LOOKBACK_DAYS: int = 90

# Below this row count the fitter refuses — the resulting curve
# would be too noisy to trust against a 30-row gate. The fitter
# returns ``None``; the existing model (if any) keeps serving.
MIN_CALIBRATION_ROWS: int = 100

# Hard ceiling on calibrated confidence. Even if the historical
# bucket showed 100% wins, we never emit calibrated confidence
# above this. Two reasons:
#   1. The Tier 3 unlock check + every downstream display assumes
#      confidence ∈ [0, 1]; saturating at exactly 1.0 is a footgun.
#   2. Headroom: leaves a real gap between "the model is well-
#      calibrated" and "the model is overconfident", which the
#      next retrain can earn.
MAX_CALIBRATED_CONFIDENCE: float = 0.95

# Persisted-doc constants. The ``calibration_applies_to`` list is
# the explicit boundary — anyone who later wants to use this signal
# for sizing/execution must change this list, which is a code review
# trigger, not a silent behaviour change.
CALIBRATION_APPLIES_TO: list[str] = ["tier3_readiness_only"]
CALIBRATION_SOURCE: str = "prediction_24h_verified_rows"

# Mongo collection name.
COLLECTION = "calibration_models"


# ── Pure helpers ───────────────────────────────────────────────────


def _normalise_to_unit(conf: Any) -> float | None:
    """Fold raw confidence (0-1 OR 0-100) onto [0.0, 1.0].

    Returns ``None`` for missing / non-numeric inputs so the caller
    can decide whether to drop the row or use a sentinel.
    """
    if conf is None:
        return None
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return None
    if c <= 0.0:
        return 0.0
    # Heuristic: anything > 1.01 is on the 0-100 scale.
    if c > 1.01:
        c = c / 100.0
    if c > 1.0:
        c = 1.0
    return c


def _weighted_ece_pp(raw: list[float], correct: list[bool]) -> float:
    """Weighted Expected Calibration Error in **percentage points**.

    Uses 5pp-wide buckets across [0, 1]; aggregates |win_rate − mean_conf|
    weighted by per-bucket count. Returns ``0.0`` on empty input rather
    than raising — the caller surfaces it as "n/a".
    """
    if not raw:
        return 0.0
    total = len(raw)
    edges = np.arange(0.0, 1.05, 0.05)
    ece = 0.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        idxs = [j for j, r in enumerate(raw) if lo <= r < hi or (hi >= 1.0 and r == 1.0)]
        if not idxs:
            continue
        bucket_n = len(idxs)
        wr = sum(1 for j in idxs if correct[j]) / bucket_n
        mc = sum(raw[j] for j in idxs) / bucket_n
        ece += (bucket_n / total) * abs(wr - mc)
    return round(ece * 100.0, 2)


def apply_calibration(raw_conf: Any, model: Optional[dict[str, Any]]) -> Optional[float]:
    """Map raw confidence → calibrated confidence using the active model.

    Pure function. Returns ``None`` when the model is absent / unfittable
    or the raw input is malformed; callers (including the prediction
    write path) treat ``None`` as "do not write a calibrated_confidence
    field on this row" and the Tier 3 reader falls back to raw.
    """
    if not model:
        return None
    knots = model.get("knots") or []
    if not knots:
        return None
    x = _normalise_to_unit(raw_conf)
    if x is None:
        return None

    xs = np.array([float(k["x"]) for k in knots], dtype=float)
    ys = np.array([float(k["y"]) for k in knots], dtype=float)
    if len(xs) < 2:
        return None

    # ``np.interp`` clamps to the endpoints — exactly the behaviour
    # we want at the edges (raw < smallest knot.x → smallest knot.y,
    # raw > largest knot.x → largest knot.y).
    y = float(np.interp(x, xs, ys))
    cap = float(model.get("max_calibrated_confidence") or MAX_CALIBRATED_CONFIDENCE)
    return round(min(max(y, 0.0), cap), 4)


# ── Mongo I/O ──────────────────────────────────────────────────────


async def get_active_calibration(db: Any) -> Optional[dict[str, Any]]:
    """Return the currently-active calibration model, or ``None``.

    Never raises — Mongo errors degrade to "no calibration".
    """
    if db is None:
        return None
    try:
        doc = await db[COLLECTION].find_one(
            {"active": True}, {"_id": 0},
        )
        return doc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] active read failed: %s", exc)
        return None


async def fit_isotonic_calibration(
    db: Any,
    *,
    lookback_days: int = LOOKBACK_DAYS,
    min_rows: int = MIN_CALIBRATION_ROWS,
    max_calibrated: float = MAX_CALIBRATED_CONFIDENCE,
) -> Optional[dict[str, Any]]:
    """Fit a fresh isotonic calibrator on verified predictions and
    persist it as the new active model.

    Returns the persisted doc on success, or ``None`` when there
    aren't enough rows to fit (the prior active model — if any —
    keeps serving). Never raises into the caller.
    """
    if db is None:
        return None

    since_iso = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).isoformat()

    try:
        cursor = db.predictions.find(
            {
                "verified_24h.correct": {"$in": [True, False]},
                "timestamp": {"$gte": since_iso},
            },
            {"_id": 0, "confidence": 1, "verified_24h.correct": 1},
        ).limit(20_000)
        rows = await cursor.to_list(length=20_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] read failed: %s", exc)
        return None

    raw: list[float] = []
    correct: list[bool] = []
    for r in rows:
        x = _normalise_to_unit(r.get("confidence"))
        if x is None:
            continue
        c = (r.get("verified_24h") or {}).get("correct")
        if c not in (True, False):
            continue
        raw.append(x)
        correct.append(bool(c))

    if len(raw) < min_rows:
        logger.info(
            "[calibration] insufficient rows: %d < %d",
            len(raw), min_rows,
        )
        return None

    raw_arr = np.asarray(raw, dtype=float)
    y_arr = np.asarray([1.0 if c else 0.0 for c in correct], dtype=float)

    iso = IsotonicRegression(
        out_of_bounds="clip",
        y_min=0.0,
        y_max=max_calibrated,
    ).fit(raw_arr, y_arr)

    # Knot extraction. Sort by x, dedupe duplicates, cap y.
    xs = np.sort(np.unique(raw_arr))
    ys = iso.predict(xs)
    knots: list[dict[str, float]] = []
    last_y: float | None = None
    for xv, yv in zip(xs, ys):
        capped = min(max(float(yv), 0.0), max_calibrated)
        # Skip flat repeats so the knot list stays compact (np.interp
        # handles plateau bridging via endpoint clamping).
        if last_y is not None and abs(capped - last_y) < 1e-6:
            continue
        knots.append({"x": round(float(xv), 4), "y": round(capped, 4)})
        last_y = capped

    # Always include the endpoints so np.interp clamps cleanly.
    if not knots or knots[0]["x"] > 0.0:
        first_y = min(max(float(iso.predict([0.0])[0]), 0.0), max_calibrated)
        knots.insert(0, {"x": 0.0, "y": round(first_y, 4)})
    if knots[-1]["x"] < 1.0:
        last_y_pred = min(max(float(iso.predict([1.0])[0]), 0.0), max_calibrated)
        knots.append({"x": 1.0, "y": round(last_y_pred, 4)})

    ece_before = _weighted_ece_pp(raw, correct)

    calibrated_predictions = [
        apply_calibration(
            x,
            {
                "knots": knots,
                "max_calibrated_confidence": max_calibrated,
            },
        )
        or 0.0
        for x in raw
    ]
    ece_after = _weighted_ece_pp(calibrated_predictions, correct)

    version = "isotonic_" + datetime.now(timezone.utc).strftime("%Y_%m_%dT%H_%M_%S")
    doc = {
        "version": version,
        "active": True,
        "fitted_at": datetime.now(timezone.utc),
        "lookback_days": lookback_days,
        "n_rows": len(raw),
        "ece_before_pp": ece_before,
        "ece_after_pp": ece_after,
        "max_calibrated_confidence": max_calibrated,
        "calibration_applies_to": list(CALIBRATION_APPLIES_TO),
        "calibration_source": CALIBRATION_SOURCE,
        "knots": knots,
    }

    try:
        # Mark all prior models inactive, then insert the new active one.
        # Two writes is fine — if the second fails we just have no
        # active model, which the reader treats as "no calibration".
        await db[COLLECTION].update_many(
            {"active": True}, {"$set": {"active": False}},
        )
        await db[COLLECTION].insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] persist failed: %s", exc)
        return None

    return doc


def calibration_audit_fields(
    raw_conf: Any, model: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Build the per-row audit fields stamped onto a prediction row.

    Returns an empty dict when no model is active OR the raw input
    is malformed, so callers can ``doc.update(...)`` it
    unconditionally without a None-guard. Pure function.
    """
    calibrated = apply_calibration(raw_conf, model)
    if calibrated is None or not model:
        return {}
    return {
        "calibrated_confidence": calibrated,
        "calibration_version": model.get("version"),
        "calibration_source": model.get("calibration_source", CALIBRATION_SOURCE),
        "calibration_n": int(model.get("n_rows") or 0),
        "calibration_applies_to": list(
            model.get("calibration_applies_to", CALIBRATION_APPLIES_TO),
        ),
    }
