"""Learning + sizing upgrades.

Pure-function surface exposing three independent primitives that
sharpen the feedback loop:

1. **Outcome-weighted learning** — `score_prediction_outcome(grade)`
   returns the raw severity weight. For the confidence-scaled
   variant used by conviction calibration, import the 2-arg
   `conviction_service.score_prediction_outcome(grade, confidence)`
   directly (it's a different primitive).

2. **Calibration-aware sizing** — `compute_calibration_multiplier(ece)`
   dampens position size when the model's Expected Calibration Error
   is wide. Intuition: if the model says "70% confidence" but that
   actually means 50% right half the time (ECE=0.2), you shouldn't
   scale position size linearly with the confidence the way
   `compute_confidence_multiplier` does. This multiplies on top of
   confidence sizing to re-anchor the output to reality.

3. **Regime-aware learning** — `compute_regime_weight(trade_regime,
   current_regime)` down-weights training rows captured under a
   different regime from the one the model will next predict under.
   Prevents "bull-market-only" training data from polluting a
   chop-regime prediction, and vice versa.

All functions are side-effect-free and import nothing beyond
stdlib + `conviction_service.GRADE_WEIGHTS`, so they're safe to
import from any hot path. The actual wiring into
`ml_retrain_service` and `ai_core/sizing` is done at the call
sites.

Canonical import:

    from ai_core.learning_upgrade import score_prediction_outcome

    score_delta = score_prediction_outcome(grade)
"""
from __future__ import annotations

from typing import Optional

# Single source of truth for the grade → weight table. We import
# rather than redefine so any future edit to `GRADE_WEIGHTS`
# propagates to both the conviction-service scoring and this
# learning-upgrade surface automatically.
from services.conviction_service import GRADE_WEIGHTS  # noqa: F401


def score_prediction_outcome(grade: str) -> float:
    """Raw grade → signed severity weight.

    Single-argument; no confidence scaling. Returns the canonical
    per-grade value from `GRADE_WEIGHTS`:

        STRONG_HIT   → +2.0
        WEAK_HIT     → +1.0
        NEUTRAL      →  0.0
        WEAK_MISS    → -1.0
        STRONG_MISS  → -2.0

    This is the primitive the learning layer uses when it wants the
    raw asymmetry: "how much should this outcome move stats". For
    the confidence-scaled version (used for per-trade conviction
    calibration), import from `conviction_service` directly —
    that one takes `(grade, confidence)` and returns
    `base_weight × confidence/100`.

    Unknown grades return 0.0 rather than raising. Downstream
    callers frequently pass raw DB strings, and an enum addition
    shouldn't crash aggregation jobs.
    """
    return float(GRADE_WEIGHTS.get(grade, 0.0))


# ══════════════════════════════════════════════════════════════════
# CALIBRATION-AWARE SIZING
# ══════════════════════════════════════════════════════════════════
#
# ECE thresholds matched to the retail-desk intuition:
#   < 5%   → "model knows what it doesn't know"     full size
#   5-10%  → "small but visible drift"              0.8×
#   10-20% → "notable overconfidence"               0.6×
#   ≥ 20%  → "regime moved out from under us"       0.4×
# The floor at 0.4 (not 0.0) is deliberate — even a badly-calibrated
# model still carries SOME directional signal, so we degrade rather
# than shut off entirely. The global kill switch is the right lever
# for zero-sizing, not a calibration dampener.

_ECE_TIERS: tuple[tuple[float, float], ...] = (
    (0.05, 1.0),
    (0.10, 0.8),
    (0.20, 0.6),
)
_ECE_FLOOR_MULT: float = 0.4


def compute_calibration_multiplier(ece: float) -> float:
    """Map Expected Calibration Error → sizing multiplier.

    Returns a value in ``[0.4, 1.0]`` — lower ECE → higher multiplier.
    Inputs outside ``[0, 1]`` are not rejected (we've seen ECE > 1 on
    severely miscalibrated cold-start models), they just land on the
    floor.

    Pure; no dependencies on model state.
    """
    e = float(ece)
    for threshold, mult in _ECE_TIERS:
        if e < threshold:
            return mult
    return _ECE_FLOOR_MULT


def apply_calibration_to_size(
    base_size: float,
    confidence_multiplier: float,
    ece: float,
) -> float:
    """Final position size after calibration adjustment.

    The three multipliers compose cleanly:
      ``base × confidence × calibration``

    Caller is still responsible for readiness/regime/kill-switch
    clamps; this function is purely about adding the ECE dimension.

    Canonical call site:

        from ai_core.learning_upgrade import apply_calibration_to_size

        final_size = apply_calibration_to_size(
            base_size=base_size,
            confidence_multiplier=confidence_multiplier,
            ece=calibration_error,
        )

    All three parameters are positional-or-keyword so you can mix
    styles. Kwargs are the recommended form at call sites — it's
    obvious which number is the ECE vs which is the multiplier,
    and a future signature extension (e.g. adding `cap`) won't
    silently misalign existing calls.
    """
    cal_mult = compute_calibration_multiplier(ece)
    return float(base_size) * float(confidence_multiplier) * cal_mult


# ══════════════════════════════════════════════════════════════════
# REGIME-AWARE LEARNING
# ══════════════════════════════════════════════════════════════════
#
# Regime mismatch isn't a discard — it's a dampener. A bull-regime
# row still carries information about chop-regime dynamics (risk
# management, momentum persistence, etc.) so we weight it at 0.5
# instead of 0. The conviction_service already uses 0.5 for
# mismatched regime penalties, so we stay consistent.

_REGIME_MATCH_WEIGHT: float = 1.0
_REGIME_MISMATCH_WEIGHT: float = 0.5


def compute_regime_weight(
    trade_regime: Optional[str],
    current_regime: Optional[str],
) -> float:
    """Return 1.0 if the trade was made under the same regime as
    the model is currently predicting under, else 0.5.

    None-safe on both sides — rows with missing `regime_label` fall
    into the match bucket (we'd rather keep them at full weight than
    silently cut them in half, since the user's primary complaint
    would be "my model never trained"). Same applies when the
    current regime is unknown — we trust the row.
    """
    if not trade_regime or not current_regime:
        return _REGIME_MATCH_WEIGHT
    return (
        _REGIME_MATCH_WEIGHT
        if trade_regime == current_regime
        else _REGIME_MISMATCH_WEIGHT
    )


def build_regime_key(signal_type: str, regime: str) -> str:
    """Build a regime-scoped bucket key for per-regime statistics.

    Use when aggregating stats like win-rate / expectancy by
    (signal, regime) — `breakout_bull`, `mean_reversion_chop`, etc.
    Pure string composition; no validation. Caller owns the
    vocabulary of signal_type + regime.
    """
    return f"{signal_type}_{regime}"


# ══════════════════════════════════════════════════════════════════
# COMBINED LEARNING UPDATE
# ══════════════════════════════════════════════════════════════════

def compute_weighted_learning_update(
    grade: str,
    trade_regime: Optional[str],
    current_regime: Optional[str],
) -> float:
    """Full scalar learning signal = raw grade weight × regime
    relevance.

    Note this uses the 1-arg `score_prediction_outcome` (raw
    severity) rather than the conviction-service variant
    (confidence-weighted). The combined scalar is usually fed to
    stats aggregation where confidence has already been factored
    in upstream. Callers that want confidence baked in should
    multiply at the call site.
    """
    return score_prediction_outcome(grade) * compute_regime_weight(
        trade_regime, current_regime,
    )


# ══════════════════════════════════════════════════════════════════
# CONVICTION PENALTY (negative-only projection)
# ══════════════════════════════════════════════════════════════════

def compute_conviction_penalty(grade: str) -> float:
    """Negative-only projection of `score_prediction_outcome` — the
    penalty side only. HITS return 0 (not a penalty). Use when you
    want "how much should this HURT conviction" and HIT grades
    should be a no-op rather than a positive bonus.
    """
    return min(score_prediction_outcome(grade), 0.0)


# ══════════════════════════════════════════════════════════════════
# EXPECTANCY (future Kelly sizing)
# ══════════════════════════════════════════════════════════════════

def compute_expectancy(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """Classic expectancy: E = p·W - (1-p)·|L|.

    Pure math. Use `abs(avg_loss)` so callers don't have to pre-
    negate losses. Degenerate inputs (win_rate outside [0,1]) are
    not clamped — if you pass nonsense in, you get nonsense out.
    Kelly-sizing callers should clamp themselves.
    """
    p = float(win_rate)
    return p * float(avg_win) - (1.0 - p) * abs(float(avg_loss))
