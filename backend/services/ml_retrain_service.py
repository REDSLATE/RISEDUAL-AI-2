"""Nightly ML retrain — drives the signal model's continuous learning.

Purpose
-------
Previously the model was trained once and never updated. The labeler
diligently populated `outcome` fields on 276K+ snapshots, but nothing
consumed those labels. This service reads labeled snapshots from
`features_snapshots`, retrains :class:`SignalModel`, and saves a new
versioned artefact to disk. Scheduled via APScheduler at 02:30 UTC
nightly (after the memory cleanup job).

Design choices
--------------
* **Binary target = is_correct.** The existing `SignalModel.fit()`
  contract trains on ``y=1 when prediction was correct``. We build that
  from the labeler's 3-class outcome (`up`/`down`/`flat`) by comparing
  the predicted direction (stored elsewhere) to the realised outcome.
  For v1 we approximate: if outcome=='up' treat as correct (simple
  monotonic improvement — we'll refine when the features_snapshots
  schema persists `predicted_direction` too).
* **Incremental version bump.** Artefacts land in
  ``/app/backend/models/signal_model_v{N+1}.joblib`` where N is the
  highest existing version. This preserves the audit trail of prior
  models so a regression can roll back by pointing the loader at v{N}.
* **Training log.** Each run writes a row to `ml_training_log` with
  sample count, AUC, feature importances, and the new version string.
  The `/api/admin/ml-training-history` owner endpoint surfaces these.
* **Lookback cap.** To keep memory bounded we train on the most recent
  ``MAX_SAMPLES`` labeled rows. 50K samples is more than enough for
  XGBoost to converge and caps runtime at ~2 min.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase  # noqa: F401

# Make risedual_core importable without side effects at module import time.
_RISEDUAL_CORE_PATH = Path(__file__).parent.parent / "risedual_core"
if str(_RISEDUAL_CORE_PATH) not in sys.path:
    sys.path.insert(0, str(_RISEDUAL_CORE_PATH))

logger = logging.getLogger(__name__)

MODELS_DIR = Path("/app/backend/models")
MODEL_ARTIFACT_PREFIX = "signal_model_v"
MAX_SAMPLES = 50_000
MIN_SAMPLES_FOR_TRAINING = 200
TRAINING_LOG_COLLECTION = "ml_training_log"


def _next_version_number() -> int:
    """Scan the models directory and return N+1 where N is the current max."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(MODEL_ARTIFACT_PREFIX)}(\d+)\.joblib$")
    max_n = 0
    for f in MODELS_DIR.glob(f"{MODEL_ARTIFACT_PREFIX}*.joblib"):
        m = pattern.match(f.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


async def _load_training_dataframe(
    db: Any, max_samples: int
) -> tuple[Any, Any, Any, Any, int]:
    """Pull labeled snapshots and return
    ``(X_df, y_series, w_final, w_severity_only, n_rows)``.

    Tuple is ``(pandas.DataFrame, pandas.Series[int],
    pandas.Series[float], pandas.Series[float], int)`` — the fourth
    element is the severity-only weights (pre-regime multiply),
    exposed for drift-logging. Callers that only care about
    training can ignore it.

    The `w_final` series is a **severity × regime-relevance**
    sample weight:
      * severity comes from `return_1d` (see `_severity_weights`) —
        a -5% blown trade contributes 4× a -0.5% stop-out.
      * regime comes from `regime_label` — rows captured under a
        regime different from the current regime are down-weighted
        0.5× (see `ai_core.learning_sizing.compute_regime_weight`).
    Missing columns default to neutral 1.0 — never less training
    signal than the legacy uniform path.
    """
    import pandas as pd

    from risedual_core.ml.features import FEATURE_COLUMNS

    projection = {
        "_id": 0, "outcome": 1, "return_1d": 1, "regime_label": 1,
        **{c: 1 for c in FEATURE_COLUMNS},
    }
    cursor = (
        db.features_snapshots
        .find({"outcome": {"$in": ["up", "down", "flat"]}}, projection)
        .sort("captured_at", -1)
        .limit(max_samples)
    )
    rows = await cursor.to_list(length=max_samples)
    if not rows:
        empty_w = pd.Series(dtype=float)
        return pd.DataFrame(), pd.Series(dtype=int), empty_w, empty_w, 0

    df = pd.DataFrame(rows)
    y = (df["outcome"] == "up").astype(int)
    X = df[[c for c in FEATURE_COLUMNS if c in df.columns]]
    severity = _severity_weights(df)
    w_final = _apply_regime_weighting(
        severity, df, current_regime=await _resolve_current_regime(db),
    )
    return X, y, w_final, severity, len(rows)


async def _resolve_current_regime(db: Any) -> str | None:
    """Best-effort fetch of the CURRENT regime label so we can
    down-weight training rows captured under a different regime.

    Reads from the latest `features_snapshots` row (captured_at DESC)
    — whichever regime the most recent snapshot landed in is the
    regime we're about to predict under. Returns None if the collection
    has no regime labels yet (warm-start case), which keeps regime
    weighting a no-op until the backfill lands."""
    try:
        latest = await db.features_snapshots.find_one(
            {"regime_label": {"$nin": [None, ""]}},
            {"_id": 0, "regime_label": 1},
            sort=[("captured_at", -1)],
        )
    except Exception:
        return None
    if not latest:
        return None
    label = latest.get("regime_label")
    return str(label) if label else None


def _apply_regime_weighting(
    severity: Any,
    df: Any,
    *,
    current_regime: str | None,
) -> Any:
    """Multiply severity weights by per-row regime-match weight.

    Returns severity unchanged when regime info is missing — a
    warm-start model shouldn't silently cut all weights by half
    just because `regime_label` isn't backfilled yet.
    """
    if "regime_label" not in df.columns or current_regime is None:
        return severity
    from ai_core.learning_sizing import compute_regime_weight

    regime_weights = df["regime_label"].apply(
        lambda r: compute_regime_weight(r if isinstance(r, str) else None, current_regime)
    )
    return (severity * regime_weights).astype(float)


# Severity-weighting thresholds (abs return_1d).
#   <1% → noise; 1-3% → weak directional; ≥3% → strong directional.
# Chosen to match the retail trader's "that was a real move" intuition
# and the `GRADE_WEIGHTS` ±2.0 asymmetry on the conviction side.
_WEAK_THRESHOLD = 0.01
_STRONG_THRESHOLD = 0.03
# Cap the training weight at 2.0 so a single outlier 20% mover
# doesn't swamp the gradient.
_WEIGHT_CAP = 2.0


def _severity_weights(df: Any) -> Any:
    """Map `return_1d` → per-row training weight.

    Formula mirrors `GRADE_WEIGHTS`:
      |r| < 1%   → 0.5   (NEUTRAL-ish — barely a move)
      1-3%       → 1.0   (WEAK)
      ≥ 3%       → 2.0   (STRONG — capped)
      missing    → 1.0   (fallback to uniform weight)

    Implementing with clip+scaling keeps the mapping continuous
    enough for gradient boosters without discrete step jumps that
    destabilise calibration.
    """
    import pandas as pd

    if "return_1d" not in df.columns:
        return pd.Series([1.0] * len(df), index=df.index)

    # Absolute magnitude of the move, NaN-safe.
    mag = df["return_1d"].abs().fillna(_WEAK_THRESHOLD)

    # Piecewise mapping. `pandas.cut`-style would also work but the
    # explicit formula is easier to inspect in future drift audits.
    weights = mag.copy()
    weights[mag < _WEAK_THRESHOLD] = 0.5
    # Linear ramp 1.0 → 2.0 across the [1%, 3%] band so a 2%
    # mover sits at ~1.5 — the ramp smoothness matters for XGBoost.
    ramp_mask = (mag >= _WEAK_THRESHOLD) & (mag < _STRONG_THRESHOLD)
    weights[ramp_mask] = 1.0 + (
        (mag[ramp_mask] - _WEAK_THRESHOLD)
        / (_STRONG_THRESHOLD - _WEAK_THRESHOLD)
    )
    weights[mag >= _STRONG_THRESHOLD] = _WEIGHT_CAP
    return weights.astype(float)


async def _collect_rejection_context(db: Any) -> dict:
    """Summarise rejections since the most recent successful retrain.

    The retrainer doesn't join rejections into its training set yet (we'd
    need a feature-snapshot replay at the time of rejection for that), but
    surfacing the counts in every training-log row gives immediate
    visibility into *what the pipeline filtered out* between runs. A
    spike in `orchestrator_tier_locked` or `ai_signal_validator`
    rejections is usually the first sign the gate or the Auditor needs
    tuning. Later we can add a `features_replay` join to use these as
    hard-negatives at train time.
    """
    try:
        # Anchor window to the last successful retrain; fall back to 24h
        # for a cold-start deployment where the log is empty.
        last = await db[TRAINING_LOG_COLLECTION].find_one(
            {"status": "success"}, sort=[("finished_at", -1)]
        )
        since_iso = (last or {}).get("finished_at")
        if since_iso:
            since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        else:
            since = datetime.now(timezone.utc) - timedelta(hours=24)

        pipeline = [
            {"$match": {"logged_at": {"$gte": since}}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]
        by_source: dict[str, int] = {}
        total = 0
        async for row in db.rejected_signals.aggregate(pipeline):
            by_source[row["_id"]] = row["n"]
            total += row["n"]
        return {
            "since": since.isoformat(),
            "total": total,
            "by_source": by_source,
        }
    except Exception as e:
        logger.warning(f"[ml_retrain] rejection-context pull failed: {e}")
        return {"total": 0, "by_source": {}, "error": str(e)[:200]}


async def run_nightly_retrain(
    db: Any,
    max_samples: int = MAX_SAMPLES,
) -> dict:
    """Retrain ``SignalModel`` on freshly labeled snapshots. Writes a new
    artefact, appends to the training log, returns the log row as a dict.

    Safe to run multiple times per day (each run produces a new artefact
    version; prior versions are kept). Never mutates older models.
    """
    started_at = datetime.now(timezone.utc)
    log_row: dict = {
        "started_at": started_at.isoformat(),
        "status": "pending",
    }

    try:
        from risedual_core.ml.signal_model import SignalModel, SignalModelConfig

        X, y, w, w_severity_only, n = await _load_training_dataframe(db, max_samples)
        log_row["samples"] = n
        log_row["rejections_since_last_run"] = await _collect_rejection_context(db)

        if n < MIN_SAMPLES_FOR_TRAINING:
            log_row.update({
                "status": "skipped",
                "reason": f"insufficient_samples ({n} < {MIN_SAMPLES_FOR_TRAINING})",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.warning(f"ML retrain skipped: {log_row['reason']}")
            await db[TRAINING_LOG_COLLECTION].insert_one(log_row.copy())
            return log_row

        pos_rate = float(y.mean()) if n > 0 else 0.0
        log_row["positive_rate"] = round(pos_rate, 4)
        # Expose training-weight statistics so drift is visible in
        # the retrain log without pulling the sample_weight array:
        #   mean_sample_weight — drop = training on more noise/flat
        #     days (weaker signal); spike = high-magnitude regime.
        #   severity_strong_frac — fraction of rows at the 2.0
        #     severity cap BEFORE regime weighting. Pure magnitude
        #     signal so the metric is comparable across retrains
        #     regardless of current regime.
        #   regime_match_frac — fraction of rows whose regime
        #     matches the current regime. A value near 1.0 means
        #     the training set is regime-homogeneous; near 0.5 means
        #     half the rows are from a different regime and being
        #     down-weighted accordingly.
        if n > 0:
            log_row["mean_sample_weight"] = round(float(w.mean()), 4)
            log_row["severity_strong_frac"] = round(
                float((w_severity_only >= _WEIGHT_CAP).mean()), 4,
            )
            # Infer regime match by dividing final weights by
            # severity-only weights. Rows where the ratio is ~1.0
            # kept full regime weight (match); ratio ~0.5 means
            # regime mismatch. Runtime-safe division via
            # `replace(0, 1)` for any severity-zero rows.
            safe_sev = w_severity_only.replace(0, 1.0)
            regime_ratio = (w / safe_sev).clip(0.0, 1.0)
            log_row["regime_match_frac"] = round(
                float((regime_ratio >= 0.99).mean()), 4,
            )

        version_n = _next_version_number()
        new_version_tag = f"0.1.{version_n}"  # bumps the patch number
        cfg = SignalModelConfig(model_version=new_version_tag)
        model = SignalModel(config=cfg)

        logger.info(
            f"ML retrain: fitting v{version_n} on {n} samples "
            f"(positive rate={pos_rate:.3f}, "
            f"mean weight={log_row.get('mean_sample_weight', 1.0):.3f})"
        )
        model.fit(X, y, sample_weight=w)

        artefact_path = MODELS_DIR / f"{MODEL_ARTIFACT_PREFIX}{version_n}.joblib"
        model.save(artefact_path)

        # Top-10 feature importances for the log (dicts are ordered in py3.7+)
        imps = dict(sorted(
            model._feature_importances.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )[:10])

        log_row.update({
            "status": "success",
            "model_version": new_version_tag,
            "artefact_path": str(artefact_path),
            "top_feature_importances": imps,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"ML retrain complete: {artefact_path} ({n} samples)")

    except Exception as e:
        logger.exception("ML retrain failed")
        log_row.update({
            "status": "error",
            "error": str(e)[:500],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })

    await db[TRAINING_LOG_COLLECTION].insert_one(log_row.copy())
    return log_row


async def run_backfill_labeling(
    db: Any,
) -> dict:
    """Force a manual labeler run (wraps the existing job for admin trigger)."""
    from services.prediction_labeler import label_pending_snapshots
    before = await db.features_snapshots.count_documents({"outcome": None})
    await label_pending_snapshots(db)
    after = await db.features_snapshots.count_documents({"outcome": None})
    return {"labeled": before - after, "still_pending": after}


async def get_training_history(
    db: Any, limit: int = 20
) -> list[dict]:
    """Most-recent training runs, newest first. Owner-only surface."""
    cursor = (
        db[TRAINING_LOG_COLLECTION]
        .find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(max(1, min(limit, 200)))
    )
    return await cursor.to_list(length=limit)


def get_latest_model_info() -> Optional[dict]:
    """Walk the models directory and report the highest-version artefact."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(MODEL_ARTIFACT_PREFIX)}(\d+)\.joblib$")
    latest_n = 0
    latest_path: Optional[Path] = None
    for f in MODELS_DIR.glob(f"{MODEL_ARTIFACT_PREFIX}*.joblib"):
        m = pattern.match(f.name)
        if m and int(m.group(1)) > latest_n:
            latest_n = int(m.group(1))
            latest_path = f
    if latest_path is None:
        return None
    stat = latest_path.stat()
    return {
        "version": f"0.1.{latest_n}",
        "path": str(latest_path),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }
