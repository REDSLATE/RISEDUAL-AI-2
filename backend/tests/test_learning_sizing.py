"""Tests for `ai_core.learning_sizing` — pure-function upgrades
to outcome weighting, calibration-aware sizing, and regime
scoping.

These don't touch the database. Each upgrade is a scalar in,
scalar out, so every test pins a specific boundary or ratio
that matters downstream.
"""
from __future__ import annotations

import pytest

from ai_core.learning_sizing import (
    GRADE_WEIGHTS,
    _ECE_FLOOR_MULT,
    apply_calibration_to_size,
    build_regime_key,
    compute_calibration_multiplier,
    compute_conviction_penalty,
    compute_expectancy,
    compute_regime_weight,
    compute_weighted_learning_update,
    grade_to_weight,
    score_prediction_outcome,
)


# ── Outcome-weighted learning ──────────────────────────────────────

def test_grade_weights_preserves_hit_miss_asymmetry():
    """The cornerstone of the upgrade: STRONG_MISS must be exactly
    as heavy as STRONG_HIT. If a future tweak accidentally muted
    losses, the model would re-learn tail-of-book behavior
    asymmetrically."""
    assert GRADE_WEIGHTS["STRONG_HIT"] == -GRADE_WEIGHTS["STRONG_MISS"]
    assert GRADE_WEIGHTS["WEAK_HIT"] == -GRADE_WEIGHTS["WEAK_MISS"]
    assert GRADE_WEIGHTS["NEUTRAL"] == 0.0


def test_score_prediction_outcome_unknown_grade_is_neutral():
    """Unknown grades should NOT crash — downstream callers pass
    raw DB strings. Fallback to 0 is the safe default."""
    # Raw lookup (unscaled by confidence).
    assert grade_to_weight("MISSING_ENUM") == 0.0
    assert grade_to_weight("") == 0.0
    # Confidence-weighted (same fallback).
    assert score_prediction_outcome("MISSING_ENUM", 100) == 0.0


def test_score_prediction_outcome_strong_miss_is_doubled():
    """Regression-lock on the 2:1 severity ratio the whole pipeline
    assumes."""
    strong = grade_to_weight("STRONG_MISS")
    weak = grade_to_weight("WEAK_MISS")
    assert strong / weak == 2.0


def test_grade_to_weight_matches_grade_weights_dict():
    """The public helper must agree with the canonical dict —
    catches accidental divergence if someone edits one but not
    the other."""
    for grade, expected in GRADE_WEIGHTS.items():
        assert grade_to_weight(grade) == expected


def test_score_prediction_outcome_scales_by_confidence():
    """The existing confidence-weighted primitive: high-confidence
    misses hurt more than low-confidence misses at the same grade."""
    hi = score_prediction_outcome("STRONG_MISS", 100)
    lo = score_prediction_outcome("STRONG_MISS", 30)
    assert hi < lo  # both negative; hi is "more negative"
    assert hi == pytest.approx(-2.0, abs=0.01)
    assert lo == pytest.approx(-0.6, abs=0.01)


# ── Calibration-aware sizing ───────────────────────────────────────

def test_calibration_multiplier_tier_boundaries():
    """Piecewise step function. Pin the exact ECE cutoffs so a
    casual tweak doesn't silently move the threshold where the
    model stops sizing like a Pro."""
    # Well-calibrated: full size.
    assert compute_calibration_multiplier(0.0) == 1.0
    assert compute_calibration_multiplier(0.049) == 1.0
    # 5-10%: 0.8×.
    assert compute_calibration_multiplier(0.05) == 0.8
    assert compute_calibration_multiplier(0.099) == 0.8
    # 10-20%: 0.6×.
    assert compute_calibration_multiplier(0.10) == 0.6
    assert compute_calibration_multiplier(0.199) == 0.6
    # ≥20%: floor.
    assert compute_calibration_multiplier(0.20) == _ECE_FLOOR_MULT
    assert compute_calibration_multiplier(0.99) == _ECE_FLOOR_MULT


def test_calibration_multiplier_never_zero():
    """Even a catastrophically miscalibrated model should still
    size SOMETHING — the kill switch is the lever for zero-sizing,
    not this multiplier."""
    assert compute_calibration_multiplier(1.0) > 0.0
    assert compute_calibration_multiplier(10.0) > 0.0  # impossibly bad ECE


def test_apply_calibration_to_size_composes_multiplicatively():
    """base × conf × cal — order and associativity matter because
    the downstream sizing clamp applies AFTER all three."""
    # base=$1000, conf_mult=1.2, ECE=0.15 → 0.6×
    size = apply_calibration_to_size(1000.0, 1.2, 0.15)
    assert size == pytest.approx(1000.0 * 1.2 * 0.6, abs=0.01)


def test_apply_calibration_degrades_with_worse_ece():
    """A badly-calibrated model at the SAME confidence must size
    strictly smaller than a well-calibrated one."""
    good = apply_calibration_to_size(1000.0, 1.0, 0.03)
    bad = apply_calibration_to_size(1000.0, 1.0, 0.25)
    assert bad < good


# ── Regime-aware learning ──────────────────────────────────────────

def test_regime_match_full_weight():
    assert compute_regime_weight("bull", "bull") == 1.0
    assert compute_regime_weight("chop", "chop") == 1.0


def test_regime_mismatch_halves_weight():
    """The 0.5× factor matches `conviction_service`'s mismatch
    penalty — if one side ever moves, the other should too."""
    assert compute_regime_weight("bull", "bear") == 0.5
    assert compute_regime_weight("chop", "bull") == 0.5


def test_regime_weight_none_is_neutral():
    """Unknown regime on EITHER side → full weight. Rationale in
    the `compute_regime_weight` docstring: a missing label is better
    treated as "we don't know" than "silently halved"."""
    assert compute_regime_weight(None, "bull") == 1.0
    assert compute_regime_weight("bull", None) == 1.0
    assert compute_regime_weight(None, None) == 1.0
    assert compute_regime_weight("", "bull") == 1.0


def test_build_regime_key_format():
    assert build_regime_key("breakout", "bull") == "breakout_bull"
    assert build_regime_key("mean_reversion", "chop") == "mean_reversion_chop"


# ── Combined learning update ───────────────────────────────────────

def test_combined_update_severity_times_regime():
    """STRONG_MISS in matching regime → full -2.0.
    STRONG_MISS in mismatched regime → -1.0 (halved)."""
    match = compute_weighted_learning_update("STRONG_MISS", "bull", "bull")
    mismatch = compute_weighted_learning_update("STRONG_MISS", "bull", "bear")
    assert match == -2.0
    assert mismatch == -1.0
    # A mismatched-regime strong miss must NEVER be worse than a
    # matched-regime weak miss (invariant for the learning layer).
    matched_weak = compute_weighted_learning_update("WEAK_MISS", "bull", "bull")
    assert mismatch == matched_weak


def test_combined_update_hit_propagates_sign():
    """Positive on the HIT side; scaling behaves the same as misses."""
    assert compute_weighted_learning_update("STRONG_HIT", "bull", "bull") == 2.0
    assert compute_weighted_learning_update("WEAK_HIT", "bull", "bear") == 0.5


# ── Conviction penalty (shim) ──────────────────────────────────────

def test_conviction_penalty_is_negative_projection():
    """HITS should not REWARD conviction here — only negative grades
    count. This is the "penalty-only" projection the conviction
    scoring uses."""
    assert compute_conviction_penalty("STRONG_HIT") == 0.0
    assert compute_conviction_penalty("WEAK_HIT") == 0.0
    assert compute_conviction_penalty("NEUTRAL") == 0.0
    assert compute_conviction_penalty("STRONG_MISS") == -2.0
    assert compute_conviction_penalty("WEAK_MISS") == -1.0


# ── Expectancy ─────────────────────────────────────────────────────

def test_expectancy_balanced_system():
    """50% win rate, avg_win=avg_loss → expectancy exactly 0."""
    assert compute_expectancy(0.5, 100.0, 100.0) == 0.0


def test_expectancy_profitable_system():
    """60/40 with 2:1 R/R → positive expectancy."""
    e = compute_expectancy(0.6, 200.0, 100.0)
    # 0.6*200 - 0.4*100 = 120 - 40 = 80
    assert e == 80.0


def test_expectancy_handles_negative_loss_convention():
    """Caller can pass avg_loss as negative OR positive — we abs()
    internally to avoid the classic sign-error bug."""
    positive = compute_expectancy(0.5, 100.0, 50.0)
    negative = compute_expectancy(0.5, 100.0, -50.0)
    assert positive == negative == 25.0  # 0.5*100 - 0.5*50
