"""Risk-aware (R-multiple) weighting for ML training.

Replaces raw return-based weighting with:

    R = pnl / risk

Where:

    pnl  = exit_price - entry_price (direction aware)
    risk = abs(entry_price - stop_loss)

This aligns learning with real trading outcomes:

    • +2R winner matters more than +0.5R
    • -3R loss hurts more than -0.5R

Relationship to `ai_core.learning_upgrade.compute_signed_weight`:
  * `compute_signed_weight(return_1d)` — magnitude-based; used when
    only the bar-over-bar return is available on the feature
    snapshot (current production path).
  * `compute_sample_weight_from_trade(...)` — R-based; used when
    full trade economics (entry/exit/stop/direction) are on the
    snapshot. **Currently no live consumer** — waiting on the
    feature-pipeline extension that propagates these fields from
    the execution layer.

Both paths share `_LOSS_AMPLIFIER = 1.25` via import from
`learning_upgrade`, pinned by `test_loss_amplifier_cross_module`.
If you tune one, both change together.

Canonical call site once the schema lands:

    from ai_core.risk_weighting import compute_sample_weight_from_trade

    sample_weight = compute_sample_weight_from_trade(
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        stop_loss=row["stop_loss"],
        direction=row["direction"],
    )
"""
from __future__ import annotations

import math
from typing import Optional

# Single source of truth — any tweak on learning_upgrade's side
# propagates here without code duplication.
from ai_core.learning_upgrade import _LOSS_AMPLIFIER


# ══════════════════════════════════════════════════════════════════
# CORE R-MULTIPLE CALCULATION
# ══════════════════════════════════════════════════════════════════

def compute_r_multiple(
    entry_price: Optional[float],
    exit_price: Optional[float],
    stop_loss: Optional[float],
    direction: str,
) -> float:
    """Return R-multiple = pnl / risk for a closed trade.

    Direction semantics:
      * LONG:  pnl = exit - entry (you profit when price goes up)
      * SHORT: pnl = entry - exit (you profit when price goes down)

    Risk = abs(entry - stop). If a trader set no stop, risk is 0
    and this function returns 0.0 — the caller should fall back
    to magnitude-based weighting (`compute_signed_weight`) for
    no-stop trades rather than pass them through R-weighting at
    weight 0.

    None inputs / NaN / missing direction → 0.0. Never raises —
    feeds from MongoDB rows that may have partial data.

    Sign convention mirrors P&L sign:
      * Winner → positive R (exit > entry for LONG)
      * Loser  → negative R
    """
    # Any None input is a data-quality miss; fall through with 0.
    if entry_price is None or exit_price is None or stop_loss is None:
        return 0.0

    try:
        entry = float(entry_price)
        exit_p = float(exit_price)
        stop = float(stop_loss)
    except (TypeError, ValueError):
        return 0.0

    # NaN inputs propagate to nonsense comparisons; short-circuit.
    if any(math.isnan(v) for v in (entry, exit_p, stop)):
        return 0.0

    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0  # no stop or invalid stop → defer to fallback layer

    # Direction defaults to LONG for unknown strings — matches the
    # dominant retail use case. Callers with validated data should
    # pass uppercase "LONG"/"SHORT" to make the intent explicit.
    if str(direction).upper() == "SHORT":
        pnl = entry - exit_p
    else:
        pnl = exit_p - entry

    return pnl / risk


# ══════════════════════════════════════════════════════════════════
# R-MULTIPLE → SAMPLE WEIGHT
# ══════════════════════════════════════════════════════════════════
#
# Tier thresholds:
#   |R| < 0.5 → 0.5   (stop-out or trivial exit — noise band)
#   0.5-1.0   → 1.0   (weak but directional)
#   1.0-2.0   → ramp 1.0 → 2.0
#   ≥ 2.0     → 2.0   (cap — prevents one 10R outlier from dominating)
#
# Mirrors the severity_weight_for_return shape so both paths produce
# values in the same [0.5, 2.0] band pre-amplifier. That keeps the
# XGBoost gradient well-behaved regardless of which weighting
# primitive fed the training row.

_R_NOISE_THRESHOLD: float = 0.5
_R_STRONG_THRESHOLD: float = 2.0
_R_NOISE_WEIGHT: float = 0.5
_R_STRONG_WEIGHT: float = 2.0
_R_BASE_WEIGHT: float = 1.0


def r_multiple_to_weight(r: float) -> float:
    """Map R-multiple magnitude → training weight in ``[0.5, 2.0]``.

    Piecewise (abs value):
      * < 0.5 → 0.5 (noise — stop-out, trivial exits)
      * 0.5-1.0 → 1.0 (weak but directional)
      * 1.0-2.0 → linear ramp 1.0 → 2.0
      * ≥ 2.0 → 2.0 (capped so a 10R outlier doesn't dominate)

    NaN / non-numeric → 1.0 (neutral fallback).
    Zero R-multiple → 0.5 (noise band).
    """
    try:
        abs_r = abs(float(r))
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(abs_r):
        return 1.0

    if abs_r < _R_NOISE_THRESHOLD:
        return _R_NOISE_WEIGHT
    if abs_r < _R_BASE_WEIGHT:
        return _R_BASE_WEIGHT
    if abs_r < _R_STRONG_THRESHOLD:
        # Linear ramp 1.0 → 2.0 across [1R, 2R].
        return _R_BASE_WEIGHT + (abs_r - _R_BASE_WEIGHT)
    return _R_STRONG_WEIGHT


# ══════════════════════════════════════════════════════════════════
# SIGN-AWARE ADJUSTMENT (LOSSES MATTER MORE)
# ══════════════════════════════════════════════════════════════════

def apply_loss_penalty(r: float, base_weight: float) -> float:
    """Amplify losses by `_LOSS_AMPLIFIER` (1.25×, shared with
    `learning_upgrade`). Positive R and zero are unaffected.

    Rationale: markets punish mistakes asymmetrically. The model
    should learn "avoiding losses > capturing gains" without
    flipping into paranoia (which would kill HIT rate). Same
    1.25× the scaffolded severity weighting uses.

    NaN R → no amplification (treat as neutral — we don't know
    the sign).
    """
    try:
        r_val = float(r)
    except (TypeError, ValueError):
        return float(base_weight)
    if math.isnan(r_val):
        return float(base_weight)
    if r_val < 0:
        return float(base_weight) * _LOSS_AMPLIFIER
    return float(base_weight)


# ══════════════════════════════════════════════════════════════════
# END-TO-END PIPELINE HELPER
# ══════════════════════════════════════════════════════════════════

def compute_sample_weight_from_trade(
    entry_price: Optional[float],
    exit_price: Optional[float],
    stop_loss: Optional[float],
    direction: str,
) -> float:
    """End-to-end weight computation from trade data.

    This is what you pass to `model.fit(sample_weight=...)` once
    the feature-pipeline extension lands. Composes:

        R = pnl / risk  →  base_weight = r_to_weight(R)  →
        final_weight = apply_loss_penalty(R, base_weight)

    Maximum possible weight: 2.0 × 1.25 = 2.5 (severe loss at or
    past the R cap). Same max as `compute_signed_weight` on the
    magnitude side, so switching between the two paths leaves
    the `SignalModel.fit` 10× anti-explosion clip un-triggered.

    Missing/bad inputs → 0.5 (the R=0 tier). Callers that prefer
    the magnitude-based fallback should check `compute_r_multiple
    == 0` upstream and route to `compute_signed_weight` instead.
    """
    r = compute_r_multiple(
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss=stop_loss,
        direction=direction,
    )
    base = r_multiple_to_weight(r)
    return apply_loss_penalty(r, base)


# ══════════════════════════════════════════════════════════════════
# LOGGING / DRIFT METRICS
# ══════════════════════════════════════════════════════════════════

def summarize_r_distribution(r_values: list[float]) -> dict:
    """Return distribution-level stats for a batch of R-multiples.

    Used by the retrain orchestrator to log training-set quality:
      * mean_r: drift signal (negative drift = training-dominated
        by losses → regime shift)
      * strong_r_frac: fraction of rows with |R| ≥ 1.5. Comparable
        to the severity pipeline's `severity_strong_frac` — both
        measure "what fraction of training is high-magnitude
        signal".

    Empty input → zero stats (no crash in cold-start paths).
    """
    if not r_values:
        return {"mean_r": 0.0, "strong_r_frac": 0.0}

    # Drop NaN defensively so a single bad row doesn't poison the
    # aggregate.
    clean = [float(r) for r in r_values if not (isinstance(r, float) and math.isnan(r))]
    if not clean:
        return {"mean_r": 0.0, "strong_r_frac": 0.0}

    strong = sum(1 for r in clean if abs(r) >= 1.5)
    return {
        "mean_r": sum(clean) / len(clean),
        "strong_r_frac": strong / len(clean),
    }
