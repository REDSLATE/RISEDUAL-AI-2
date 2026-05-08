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

    Canonical call site:

        from ai_core.learning_upgrade import compute_weighted_learning_update

        learning_signal = compute_weighted_learning_update(
            grade=grade,
            trade_regime=trade_regime,
            current_regime=current_regime,
        )

    Behavior summary (pinned by tests):
      * same-regime STRONG_MISS → -2.0
      * cross-regime STRONG_MISS → -1.0
      * None on either regime side → treat as match (full weight)
      * unknown grade → 0.0 (never raises)

    The sign propagates naturally: a cross-regime WEAK_HIT returns
    `+1.0 × 0.5 = +0.5`, which is LESS positive than a matched-regime
    WEAK_HIT (+1.0). Aggregation code can sum these directly.
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

    Canonical accumulation pattern:

        from ai_core.learning_upgrade import compute_conviction_penalty

        penalty = compute_conviction_penalty(grade)
        conviction_score += penalty

    Why negative-only (not the full signed weight): conviction is a
    one-way ratchet in most deployments — hits confirm the existing
    score, they don't boost it beyond the original confidence. A
    STRONG_HIT doesn't mean "increase conviction by 2.0" because
    the model was already asserting high confidence; it just
    means "the penalty side didn't trigger this round".

    If you want SYMMETRIC scoring (hits also reward), use
    `score_prediction_outcome(grade)` directly — the signed
    version. Keep this helper for the `+=` accumulation pattern
    above where HIT = no-op is the correct semantic.

    Unknown grades return 0.0 — never raises on a DB enum mismatch.
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


# ══════════════════════════════════════════════════════════════════
# SIGN-AWARE SEVERITY (loss amplification)
# ══════════════════════════════════════════════════════════════════
#
# Core insight: markets punish mistakes asymmetrically. A 3% loss
# hurts more than a 3% gain helps (drawdown math + behavioural bias
# on the operator side). Teach the model the same asymmetry by
# weighting losses higher on the gradient.
#
# `_LOSS_AMPLIFIER` = 1.25 matches the retail "avoid losses more than
# you chase gains" intuition without being so large it tips the
# training distribution into paranoid behavior. Combined with the
# regime-weight 0.5× mismatch and the severity 2.0× cap, the largest
# single-row training weight is 2.0 × 1.25 = 2.5. Still well inside
# the XGBoost 10× anti-explosion clip in `SignalModel.fit`.

_LOSS_AMPLIFIER: float = 1.25


# Severity tier thresholds (abs return_1d). Kept in sync with
# `_WEAK_THRESHOLD` / `_STRONG_THRESHOLD` in `ml_retrain_service` so
# the scalar and DataFrame paths agree to the cent. Any drift
# surfaces immediately in `test_severity_weight_scalar_matches_df`.
_SEVERITY_NOISE_THRESHOLD: float = 0.01
_SEVERITY_STRONG_THRESHOLD: float = 0.03
_SEVERITY_NOISE_WEIGHT: float = 0.5
_SEVERITY_STRONG_WEIGHT: float = 2.0
_SEVERITY_MIN_WEIGHT: float = 1.0  # ramp base


def severity_weight_for_return(return_1d: float) -> float:
    """Scalar version of the DataFrame `_severity_weights` helper.

    Use this for single-row scoring (e.g. streaming inference
    scoring, one-off regrades). For bulk training weights, the
    DataFrame path in `ml_retrain_service._severity_weights` is
    faster thanks to vectorization.

    Piecewise:
      * |r| < 1% → 0.5 (noise)
      * 1-3% → linear ramp 1.0 → 2.0
      * ≥3% → 2.0 (capped)
    NaN / None / non-numeric → 1.0 (neutral fallback, matches
    DataFrame path).
    """
    import math

    try:
        mag = abs(float(return_1d))
    except (TypeError, ValueError):
        return 1.0
    # NaN is a valid float but yields nonsense comparisons —
    # short-circuit to neutral before the tier checks.
    if math.isnan(mag):
        return 1.0
    if mag < _SEVERITY_NOISE_THRESHOLD:
        return _SEVERITY_NOISE_WEIGHT
    if mag >= _SEVERITY_STRONG_THRESHOLD:
        return _SEVERITY_STRONG_WEIGHT
    # Linear ramp across the [1%, 3%] band.
    ramp_position = (mag - _SEVERITY_NOISE_THRESHOLD) / (
        _SEVERITY_STRONG_THRESHOLD - _SEVERITY_NOISE_THRESHOLD
    )
    return _SEVERITY_MIN_WEIGHT + ramp_position * (
        _SEVERITY_STRONG_WEIGHT - _SEVERITY_MIN_WEIGHT
    )


def compute_signed_weight(return_1d: float) -> float:
    """Sign-aware severity: base magnitude weight amplified 1.25×
    when the return is negative.

    Canonical call site:

        from ai_core.learning_upgrade import compute_signed_weight

        sample_weight = compute_signed_weight(row["return_1d"])

    Rationale — markets punish mistakes asymmetrically:
      * avoiding losses > capturing gains (drawdown math)
      * behavioural bias: humans weight losses ~2× gains (Kahneman)
    1.25× sits deliberately below the full prospect-theory 2× so
    the model learns loss-aversion WITHOUT flipping into paranoia
    (which would kill the HIT rate).

    NaN / None → 1.0 (same as scalar severity — no direction to
    amplify). Positive returns and zero are unaffected (amplifier
    stays at 1.0×).
    """
    import math

    base = severity_weight_for_return(return_1d)
    try:
        r = float(return_1d)
    except (TypeError, ValueError):
        return base
    if math.isnan(r):
        return base
    if r < 0:
        return base * _LOSS_AMPLIFIER
    return base


# ══════════════════════════════════════════════════════════════════
# VOLATILITY REGIME CLASSIFIER
# ══════════════════════════════════════════════════════════════════
#
# Data-driven regime signal derived from `severity_strong_frac` —
# the fraction of the training set at the 2.0× severity cap (≥3%
# moves). Complementary to the HMM-based `regime_label` in the
# features pipeline; this one runs on whatever slice of rows you
# just loaded and needs no historical context.

_HIGH_VOL_THRESHOLD: float = 0.30
_LOW_VOL_THRESHOLD: float = 0.10


def classify_vol_regime(severity_strong_frac: float) -> str:
    """Map `severity_strong_frac` → data-driven vol regime label.

    Returns one of ``"high_vol" | "normal" | "low_vol"``.

    Thresholds:
      * ≥ 30% of rows at severity cap → ``"high_vol"``
      * ≤ 10% of rows at severity cap → ``"low_vol"``
      * otherwise → ``"normal"``

    Use the output as a drift canary, a retrain-gating signal, or a
    cross-check against the HMM's regime_label. A disagreement is
    usually informative — HMM might call "bull" while this returns
    "high_vol", meaning a volatile bull regime, which deserves a
    conviction dampener.
    """
    f = float(severity_strong_frac)
    if f >= _HIGH_VOL_THRESHOLD:
        return "high_vol"
    if f <= _LOW_VOL_THRESHOLD:
        return "low_vol"
    return "normal"


# ══════════════════════════════════════════════════════════════════
# RETRAIN QUALITY GATE
# ══════════════════════════════════════════════════════════════════
#
# `mean_sample_weight < 0.8` means the training set is dominated by
# noise-band rows (< 1% moves, weight 0.5). Retraining on that data
# pushes the model toward flat-tape predictions — exactly the failure
# mode that caused v0.1.3 → v0.1.4 to regress last cycle.

_MIN_SIGNAL_QUALITY: float = 0.8


def should_skip_retrain_low_signal(mean_sample_weight: float) -> bool:
    """Return True when the training set is too noise-heavy to be
    worth retraining on. Used upstream of `SignalModel.fit` to
    abort cleanly with a logged reason, rather than fit a worse
    model than the one already in production.

    Threshold (0.8) sits between the severity-noise weight (0.5)
    and the base weight (1.0). A set that averages below 0.8 is
    majority-noise; above 0.8 has enough directional rows to justify
    the retrain cost.

    NaN / None / non-numeric → False (don't silently block retraining
    on a stats-logging bug). The gate's job is to catch real
    low-signal data, not to block on telemetry failures.
    """
    import math

    try:
        v = float(mean_sample_weight)
    except (TypeError, ValueError):
        return False
    if math.isnan(v):
        return False
    return v < _MIN_SIGNAL_QUALITY
