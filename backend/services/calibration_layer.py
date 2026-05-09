"""Confidence Calibration Layer (Option A — IsotonicRegression).

────────────────────────────────────────────────────────────────────
AUTHORITY-BOUNDARY CONTRACT
────────────────────────────────────────────────────────────────────

This module is a PURE OBSERVATIONAL CALIBRATOR. It:

  * Reads firewall-trainable rows (gated through
    ``services.chevelle_memory_labeler.trainable_only``) and fits an
    ``sklearn.isotonic.IsotonicRegression`` mapping
    ``raw_confidence → calibrated_confidence``.
  * Persists fitted calibrators as versioned ``.joblib`` artifacts
    in ``models/calibrators/``.
  * Loads the active calibrator on import and exposes ``apply()``
    so call sites can attach calibration metadata to served
    predictions WITHOUT mutating execution-authority gates.
  * NEVER trains on quarantined rows.
  * NEVER imports broker / executor / Strategist / Auditor /
    Commander / RoadGuard / FastVeto / kill-switch.
  * NEVER emits a BUY / SELL / NO_TRADE verdict.
  * NEVER rewrites historical audit payloads (CDO keeps raw
    confidence; calibration metadata is APPENDED, not substituted).
  * On ANY failure (model missing, stale, raises) the call site
    MUST fall back to raw confidence and mark
    ``calibration_applied=False``.

Hard rules (pinned by tests)
----------------------------
  * Only firewall-trainable rows are used to fit
    (``trust_weight > 0`` AND ``trainable=True``).
  * Quarantined rows MUST NEVER appear in the training set.
  * The version string is monotonic + timestamped; old artifacts
    are kept on disk for audit.
  * If sample count < ``MIN_SAMPLES``, refit is rejected (returns
    ``CalibrationFitResult(success=False)``).
  * Apply is total — every call returns a populated metadata dict;
    no exceptions escape ``apply()``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression

from services.chevelle_memory_labeler import (
    OBSERVATION_POLICY_ALL,
    label_memories,
    trainable_only,
)

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────


CALIBRATION_METHOD: str = "isotonic"

# On-disk artifact location. ``models/calibrators/`` is a sibling
# of the existing ``models/`` tree — joblib artifacts are written
# next to ``signal_model_v*.joblib`` so the operator's existing
# artifact-inventory tile can surface them later.
_DEFAULT_ARTIFACT_DIR = Path(
    os.environ.get(
        "CALIBRATION_ARTIFACT_DIR",
        "/app/backend/models/calibrators",
    )
)

# Refuse to fit (or apply) on starvation samples — isotonic on
# fewer than 50 binary outcomes overfits trivially. Operator-tunable
# via env so a dev environment can lower the bar for manual smoke
# tests.
MIN_SAMPLES: int = int(os.environ.get("CALIBRATION_MIN_SAMPLES", "50"))

# Calibrator artifacts older than this are flagged "stale" — apply
# falls back to raw confidence with ``calibration_applied=False``.
# Default 72h gives the daily-cadence refit job a 3x grace window
# before fallback fires.
STALE_AFTER_HOURS: float = float(
    os.environ.get("CALIBRATION_STALE_AFTER_HOURS", "72.0")
)


# ── Data classes ────────────────────────────────────────────────────


@dataclass
class CalibrationApplyResult:
    """Returned by ``apply(raw)``. Always populated — even on
    fallback the dict has every metadata field."""
    raw_confidence: float
    calibrated_confidence: float
    calibration_method: str
    calibration_model_version: Optional[str]
    calibration_sample_count: int
    calibration_applied: bool
    fallback_reason: Optional[str] = None

    def as_metadata(self) -> dict:
        """Project into the dict shape ``trade_doc`` and ADL
        receipts append. Sorted for deterministic JSON output."""
        return {
            "raw_confidence": self.raw_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "calibration_method": self.calibration_method,
            "calibration_model_version": self.calibration_model_version,
            "calibration_sample_count": self.calibration_sample_count,
            "calibration_applied": self.calibration_applied,
            "fallback_reason": self.fallback_reason,
        }


@dataclass
class CalibrationFitResult:
    """Returned by ``fit_and_persist``. ``success=True`` means a
    new artifact was written and the in-memory active calibrator
    was reloaded."""
    success: bool
    sample_count: int
    model_version: Optional[str]
    artifact_path: Optional[str]
    rejected_reason: Optional[str] = None
    metrics: dict = field(default_factory=dict)


# ── Reliability bins (for Patent J card) ────────────────────────────


@dataclass
class ReliabilityBin:
    bin_lo: float
    bin_hi: float
    count: int
    mean_predicted: float
    fraction_positive: float


def reliability_bins(
    raw: list[float], outcomes: list[int], n_bins: int = 10,
) -> list[ReliabilityBin]:
    """Compute equal-width reliability bins for the Patent J card.

    Pure function — no I/O. Returns one ``ReliabilityBin`` per
    non-empty bucket so the UI never renders an empty bar."""
    if not raw or len(raw) != len(outcomes):
        return []
    raw_arr = np.asarray(raw, dtype=np.float64)
    out_arr = np.asarray(outcomes, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[ReliabilityBin] = []
    for i in range(n_bins):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i == n_bins - 1:
            mask = (raw_arr >= lo) & (raw_arr <= hi)
        else:
            mask = (raw_arr >= lo) & (raw_arr < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        bins.append(ReliabilityBin(
            bin_lo=lo, bin_hi=hi, count=n,
            mean_predicted=float(raw_arr[mask].mean()),
            fraction_positive=float(out_arr[mask].mean()),
        ))
    return bins


def expected_calibration_error(
    raw: list[float], outcomes: list[int], n_bins: int = 10,
) -> float:
    """ECE — sum over bins of ``(n_bin/N) * |mean_pred - frac_pos|``.

    Returns 0.0 on empty input. Matches the Patent J card's
    headline metric."""
    bins = reliability_bins(raw, outcomes, n_bins=n_bins)
    if not bins:
        return 0.0
    total = sum(b.count for b in bins) or 1
    return float(sum(
        (b.count / total) * abs(b.mean_predicted - b.fraction_positive)
        for b in bins
    ))


def brier_score(raw: list[float], outcomes: list[int]) -> float:
    """Brier score — mean squared difference between predicted
    probabilities and binary outcomes. Lower is better."""
    if not raw or len(raw) != len(outcomes):
        return 0.0
    raw_arr = np.asarray(raw, dtype=np.float64)
    out_arr = np.asarray(outcomes, dtype=np.float64)
    return float(np.mean((raw_arr - out_arr) ** 2))


# ── Pure helpers ────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_version() -> str:
    """Monotonic, sortable version string. ``2026-05-09T07-45-12Z``
    style — sorts lexicographically by recency."""
    return _now_utc().strftime("%Y-%m-%dT%H-%M-%SZ")


def extract_training_pairs(
    rows: Iterable[Any],
) -> tuple[list[float], list[int], int]:
    """Extract ``(raw_confidence, outcome)`` pairs from candidate
    rows AFTER the firewall labeler has gated them.

    Hard contract:
      * Only ``trainable_only(...)``-passing rows contribute.
      * ``raw_confidence`` is read from
        ``row.get("confidence")`` or ``row.get("raw_confidence")``.
      * ``outcome`` is read from
        ``record.outcome_label`` (WIN=1 / LOSS=0; everything else
        is filtered out so isotonic sees a clean binary target).

    Returns ``(raw_list, outcome_list, total_seen)``. ``total_seen``
    is the count BEFORE filtering — useful for the operator to
    diagnose "why were so few samples accepted?".
    """
    raws: list[float] = []
    outs: list[int] = []
    total = 0

    materialised = list(rows)
    total = len(materialised)
    labeled = list(label_memories(
        materialised, observation_policy=OBSERVATION_POLICY_ALL,
    ))
    trainable = list(trainable_only(labeled))

    for rec, raw_row in zip(labeled, materialised):
        if not isinstance(raw_row, dict):
            continue
        if rec not in trainable:
            continue
        # Outcome must be win/loss to fit binary isotonic.
        if rec.outcome_label.value not in {"win", "loss"}:
            continue
        # Read raw confidence — prefer explicit ``raw_confidence``,
        # fall back to legacy ``confidence``.
        raw = raw_row.get("raw_confidence")
        if raw is None:
            raw = raw_row.get("confidence")
        if raw is None:
            continue
        try:
            raw_f = float(raw)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= raw_f <= 1.0):
            continue
        raws.append(raw_f)
        outs.append(1 if rec.outcome_label.value == "win" else 0)

    return raws, outs, total


# ── Persistence ─────────────────────────────────────────────────────


def _artifact_dir() -> Path:
    _DEFAULT_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_ARTIFACT_DIR


def _artifact_path(version: str) -> Path:
    return _artifact_dir() / f"calibrator_{version}.joblib"


def _active_pointer_path() -> Path:
    return _artifact_dir() / "active.txt"


def _set_active_version(version: str) -> None:
    _active_pointer_path().write_text(version, encoding="utf-8")


def _get_active_version() -> Optional[str]:
    p = _active_pointer_path()
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        return None


# ── Public fit API ──────────────────────────────────────────────────


def fit_and_persist(
    rows: Iterable[Any],
    *,
    artifact_dir: Optional[Path] = None,
) -> CalibrationFitResult:
    """Fit an isotonic calibrator from ``rows`` and persist it as a
    versioned joblib artifact.

    ``rows`` are passed through the Chevelle firewall —
    quarantined / unlabeled / non-binary-outcome rows are dropped
    before fitting.

    Returns ``CalibrationFitResult`` with ``success=False`` and a
    ``rejected_reason`` when the sample count is below
    ``MIN_SAMPLES`` — the active calibrator is NOT touched in that
    case.
    """
    raws, outs, total_seen = extract_training_pairs(rows)
    n = len(raws)

    if n < MIN_SAMPLES:
        return CalibrationFitResult(
            success=False,
            sample_count=n,
            model_version=None,
            artifact_path=None,
            rejected_reason=(
                f"insufficient_samples: have {n}, need >= {MIN_SAMPLES}"
            ),
            metrics={"total_seen": total_seen},
        )

    raw_arr = np.asarray(raws, dtype=np.float64)
    out_arr = np.asarray(outs, dtype=np.float64)

    # Pre-fit metrics — what does the raw distribution look like?
    pre_ece = expected_calibration_error(raws, outs)
    pre_brier = brier_score(raws, outs)

    iso = IsotonicRegression(
        y_min=0.0, y_max=1.0, out_of_bounds="clip",
    )
    iso.fit(raw_arr, out_arr)

    # Post-fit metrics — does the calibrator actually improve ECE?
    calibrated = iso.predict(raw_arr)
    post_ece = expected_calibration_error(
        list(calibrated), outs,
    )
    post_brier = brier_score(list(calibrated), outs)

    version = _make_version()
    payload = {
        "model": iso,
        "method": CALIBRATION_METHOD,
        "version": version,
        "fit_at": _now_utc().isoformat(),
        "sample_count": n,
        "metrics": {
            "pre_ece": pre_ece,
            "post_ece": post_ece,
            "pre_brier": pre_brier,
            "post_brier": post_brier,
            "total_seen": total_seen,
        },
    }
    target_dir = artifact_dir or _artifact_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact = target_dir / f"calibrator_{version}.joblib"
    joblib.dump(payload, artifact)
    # Update the active pointer atomically (write-then-rename).
    pointer = target_dir / "active.txt"
    pointer.write_text(version, encoding="utf-8")
    # Force the in-memory active calibrator to reload on next apply.
    _reset_active_cache()

    # Sidecar mirror — best-effort, never raises.
    try:
        from services.risedual_monorepo_client import (
            mirror_calibration_artifact,
        )
        mirror_calibration_artifact(
            artifact_path=artifact,
            version=version,
            method=CALIBRATION_METHOD,
            fit_at=payload.get("fit_at"),
        )
    except Exception:  # noqa: BLE001
        pass

    return CalibrationFitResult(
        success=True,
        sample_count=n,
        model_version=version,
        artifact_path=str(artifact),
        metrics=payload["metrics"],
    )


# ── Active calibrator (lazy-loaded singleton) ───────────────────────


_active_payload_cache: Optional[dict] = None
_active_cache_loaded_at: Optional[datetime] = None


def _reset_active_cache() -> None:
    global _active_payload_cache, _active_cache_loaded_at
    _active_payload_cache = None
    _active_cache_loaded_at = None


def _load_active_payload() -> Optional[dict]:
    """Resolve + cache the active calibrator artifact in memory."""
    global _active_payload_cache, _active_cache_loaded_at
    if _active_payload_cache is not None:
        return _active_payload_cache
    version = _get_active_version()
    if not version:
        return None
    path = _artifact_path(version)
    if not path.exists():
        logger.warning(
            "[calibration] active version %s missing at %s",
            version, path,
        )
        return None
    try:
        payload = joblib.load(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[calibration] failed to load %s: %s", path, exc,
        )
        return None
    _active_payload_cache = payload
    _active_cache_loaded_at = _now_utc()
    return payload


def _is_stale(payload: dict) -> bool:
    """Calibrator is stale if its ``fit_at`` is older than
    ``STALE_AFTER_HOURS``. Stale calibrators trigger fallback."""
    fit_at = payload.get("fit_at")
    if not fit_at:
        return True
    try:
        fit_dt = datetime.fromisoformat(str(fit_at))
        if fit_dt.tzinfo is None:
            fit_dt = fit_dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return True
    age_hours = (_now_utc() - fit_dt).total_seconds() / 3600.0
    return age_hours > STALE_AFTER_HOURS


# ── Public apply API ────────────────────────────────────────────────


def apply(raw_confidence: float) -> CalibrationApplyResult:
    """Map raw → calibrated confidence. Total — never raises.

    Fallback to raw confidence (with ``calibration_applied=False``)
    when:
      * the active calibrator is missing,
      * the artifact is older than ``STALE_AFTER_HOURS``,
      * predict() raises for any reason,
      * raw_confidence is non-finite or out of [0.0, 1.0].
    """
    # Defensive normalisation — never let bad input crash the call
    # site or feed the isotonic model garbage.
    try:
        raw = float(raw_confidence)
    except (TypeError, ValueError):
        return _fallback(raw_confidence, reason="raw_unparseable")
    if not np.isfinite(raw):
        return _fallback(raw, reason="raw_non_finite")
    if raw < 0.0 or raw > 1.0:
        return _fallback(raw, reason="raw_out_of_range")

    payload = _load_active_payload()
    if payload is None:
        return _fallback(raw, reason="no_active_calibrator")
    if _is_stale(payload):
        return _fallback(
            raw, reason="calibrator_stale",
            version=payload.get("version"),
            sample_count=int(payload.get("sample_count") or 0),
        )

    try:
        model = payload["model"]
        calibrated = float(model.predict(np.array([raw]))[0])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] apply failed: %s", exc)
        return _fallback(
            raw, reason="apply_exception",
            version=payload.get("version"),
            sample_count=int(payload.get("sample_count") or 0),
        )

    # Clip just-in-case — IsotonicRegression with out_of_bounds=clip
    # already does this but we guard against any future model swap.
    calibrated = max(0.0, min(1.0, calibrated))

    return CalibrationApplyResult(
        raw_confidence=raw,
        calibrated_confidence=calibrated,
        calibration_method=str(
            payload.get("method") or CALIBRATION_METHOD
        ),
        calibration_model_version=str(
            payload.get("version") or ""
        ) or None,
        calibration_sample_count=int(
            payload.get("sample_count") or 0
        ),
        calibration_applied=True,
        fallback_reason=None,
    )


def _fallback(
    raw: Any,
    *,
    reason: str,
    version: Optional[str] = None,
    sample_count: int = 0,
) -> CalibrationApplyResult:
    """Build a fallback result. ``calibrated_confidence`` MIRRORS
    raw so downstream consumers see no shift when the calibrator is
    inactive — they can still rely on the field being present."""
    try:
        raw_f = float(raw)
        if not (0.0 <= raw_f <= 1.0) or not np.isfinite(raw_f):
            raw_f = 0.5  # neutral fallback for unusable input
    except (TypeError, ValueError):
        raw_f = 0.5
    return CalibrationApplyResult(
        raw_confidence=raw_f,
        calibrated_confidence=raw_f,
        calibration_method=CALIBRATION_METHOD,
        calibration_model_version=version,
        calibration_sample_count=sample_count,
        calibration_applied=False,
        fallback_reason=reason,
    )


# ── Reliability snapshot for the Patent J card ──────────────────────


def reliability_snapshot(
    rows: Iterable[Any], n_bins: int = 10,
) -> dict:
    """Build the read-only payload the Patent J card consumes.

    Returns ``{ active_version, sample_count, ece, brier, bins[],
    apply_health }``. NEVER raises — empty corpora return a
    fully-populated zero-state dict.
    """
    raws, outs, total_seen = extract_training_pairs(rows)
    payload = _load_active_payload()
    bins = reliability_bins(raws, outs, n_bins=n_bins)
    return {
        "active_version": (
            payload.get("version") if payload else None
        ),
        "sample_count": len(raws),
        "total_seen": total_seen,
        "ece": expected_calibration_error(raws, outs),
        "brier": brier_score(raws, outs),
        "bins": [
            {
                "bin_lo": b.bin_lo,
                "bin_hi": b.bin_hi,
                "count": b.count,
                "mean_predicted": b.mean_predicted,
                "fraction_positive": b.fraction_positive,
            }
            for b in bins
        ],
        "apply_health": {
            "calibrator_loaded": payload is not None,
            "stale": (
                _is_stale(payload) if payload is not None else True
            ),
            "min_samples_required": MIN_SAMPLES,
            "stale_after_hours": STALE_AFTER_HOURS,
        },
    }
