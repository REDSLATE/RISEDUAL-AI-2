"""Tests for `ai_core.risk_weighting` — pure-function R-multiple
weighting for ML training.

Each test pins a specific boundary, ratio, or edge case that
matters downstream. These are database-free; every test exercises
the scalar interface.

The canonical call site these tests lock in:

    from ai_core.risk_weighting import compute_sample_weight_from_trade
    w = compute_sample_weight_from_trade(
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        stop_loss=row["stop_loss"],
        direction=row["direction"],
    )
"""
from __future__ import annotations

import math

import pytest

from ai_core.learning_upgrade import _LOSS_AMPLIFIER as _LEARNING_LOSS_AMPLIFIER
from ai_core.risk_weighting import (
    _LOSS_AMPLIFIER,
    _R_BASE_WEIGHT,
    _R_NOISE_FLOOR,
    _R_NOISE_THRESHOLD,
    _R_NOISE_WEIGHT,
    _R_STRONG_THRESHOLD,
    _R_STRONG_WEIGHT,
    apply_loss_penalty,
    compute_r_multiple,
    compute_sample_weight_from_trade,
    r_multiple_to_weight,
    should_skip_row_by_r,
    summarize_r_distribution,
)


# ══════════════════════════════════════════════════════════════════
# CONSTANT PINNING — keep risk_weighting ↔ learning_upgrade in lockstep
# ══════════════════════════════════════════════════════════════════

def test_loss_amplifier_cross_module():
    """`_LOSS_AMPLIFIER` must be imported from `learning_upgrade` so
    any tuning there propagates here automatically. If the two
    modules ever drift apart, sign-aware weighting would apply
    two different penalties depending on which code path fed the
    training row."""
    assert _LOSS_AMPLIFIER is _LEARNING_LOSS_AMPLIFIER
    assert _LOSS_AMPLIFIER == 1.25


# ══════════════════════════════════════════════════════════════════
# compute_r_multiple — core R math
# ══════════════════════════════════════════════════════════════════

def test_long_winner_r_multiple_sign_and_magnitude():
    """LONG: +2R winner. entry 100, stop 95 (risk=5), exit 110
    (pnl=+10). R = 10/5 = +2."""
    r = compute_r_multiple(
        entry_price=100.0, exit_price=110.0, stop_loss=95.0, direction="LONG"
    )
    assert r == pytest.approx(2.0)


def test_long_loser_r_multiple_is_negative():
    """LONG: -1R loss. entry 100, stop 95 (risk=5), exit 95
    (pnl=-5). R = -1."""
    r = compute_r_multiple(
        entry_price=100.0, exit_price=95.0, stop_loss=95.0, direction="LONG"
    )
    assert r == pytest.approx(-1.0)


def test_short_winner_direction_flips_pnl():
    """SHORT: +2R winner. entry 100, stop 105 (risk=5), exit 90
    (pnl = entry - exit = +10). R = +2."""
    r = compute_r_multiple(
        entry_price=100.0, exit_price=90.0, stop_loss=105.0, direction="SHORT"
    )
    assert r == pytest.approx(2.0)


def test_short_loser_pnl_is_negative():
    """SHORT: -1R loss. entry 100, stop 105, exit 105
    (pnl = 100 - 105 = -5). R = -1."""
    r = compute_r_multiple(
        entry_price=100.0, exit_price=105.0, stop_loss=105.0, direction="SHORT"
    )
    assert r == pytest.approx(-1.0)


def test_direction_case_insensitive():
    """Direction comparison uppercases — lowercase/mixed must work."""
    r_lower = compute_r_multiple(100.0, 110.0, 95.0, "short")
    r_mixed = compute_r_multiple(100.0, 110.0, 95.0, "Short")
    # SHORT on a price rise = loss. pnl = 100 - 110 = -10, R = -2.
    assert r_lower == pytest.approx(-2.0)
    assert r_mixed == pytest.approx(-2.0)


def test_unknown_direction_defaults_long():
    """Unknown direction strings fall through to LONG semantics —
    documented behavior for the dominant retail use case."""
    r = compute_r_multiple(100.0, 110.0, 95.0, "sideways")
    assert r == pytest.approx(2.0)  # LONG math: (110 - 100) / 5


def test_none_inputs_return_zero():
    """Any None input is a data-quality miss — defer to the
    magnitude fallback rather than crash."""
    assert compute_r_multiple(None, 110.0, 95.0, "LONG") == 0.0
    assert compute_r_multiple(100.0, None, 95.0, "LONG") == 0.0
    assert compute_r_multiple(100.0, 110.0, None, "LONG") == 0.0


def test_nan_inputs_return_zero():
    """NaN propagates silently through arithmetic; short-circuit
    to 0.0 so downstream weight is the noise tier."""
    nan = float("nan")
    assert compute_r_multiple(nan, 110.0, 95.0, "LONG") == 0.0
    assert compute_r_multiple(100.0, nan, 95.0, "LONG") == 0.0
    assert compute_r_multiple(100.0, 110.0, nan, "LONG") == 0.0


def test_non_numeric_inputs_return_zero():
    """Strings from a bad Mongo doc don't crash — return 0."""
    assert compute_r_multiple("bad", 110.0, 95.0, "LONG") == 0.0  # type: ignore[arg-type]


def test_zero_risk_returns_zero_no_divide_by_zero():
    """entry == stop means no risk set. Return 0 (not ±inf),
    caller is expected to route to the magnitude fallback."""
    r = compute_r_multiple(100.0, 120.0, 100.0, "LONG")
    assert r == 0.0


def test_negative_risk_input_handled_as_abs():
    """Stop above entry on a LONG should never happen, but if it
    does the risk is still |entry - stop|, not a negative number.
    entry 100, stop 105 → risk = 5."""
    r = compute_r_multiple(100.0, 110.0, 105.0, "LONG")
    assert r == pytest.approx(2.0)


def test_integer_inputs_accepted():
    """Mongo occasionally stores ints; float() coercion must handle."""
    r = compute_r_multiple(100, 110, 95, "LONG")  # type: ignore[arg-type]
    assert r == pytest.approx(2.0)


# ══════════════════════════════════════════════════════════════════
# r_multiple_to_weight — piecewise tier mapping
# ══════════════════════════════════════════════════════════════════

def test_noise_tier_below_half_r():
    """|R| < 0.5 is noise — stop-outs or trivial exits."""
    assert r_multiple_to_weight(0.0) == _R_NOISE_WEIGHT
    assert r_multiple_to_weight(0.3) == _R_NOISE_WEIGHT
    assert r_multiple_to_weight(-0.4) == _R_NOISE_WEIGHT


def test_weak_tier_half_to_one_r():
    """0.5 ≤ |R| < 1.0 is the base/weak band — weight = 1.0."""
    assert r_multiple_to_weight(0.5) == _R_BASE_WEIGHT
    assert r_multiple_to_weight(0.99) == _R_BASE_WEIGHT
    assert r_multiple_to_weight(-0.75) == _R_BASE_WEIGHT


def test_ramp_tier_one_to_two_r_is_linear():
    """1.0 ≤ |R| < 2.0 ramps linearly from 1.0 to 2.0. 1.5R → 1.5."""
    assert r_multiple_to_weight(1.0) == pytest.approx(1.0)
    assert r_multiple_to_weight(1.5) == pytest.approx(1.5)
    assert r_multiple_to_weight(-1.75) == pytest.approx(1.75)


def test_strong_tier_capped_at_two():
    """|R| ≥ 2.0 is capped at 2.0 — a 10R outlier can't dominate."""
    assert r_multiple_to_weight(2.0) == _R_STRONG_WEIGHT
    assert r_multiple_to_weight(5.0) == _R_STRONG_WEIGHT
    assert r_multiple_to_weight(-10.0) == _R_STRONG_WEIGHT


def test_boundary_thresholds_match_constants():
    """Guard against accidental constant drift — the piecewise
    boundaries must equal the documented thresholds."""
    assert _R_NOISE_THRESHOLD == 0.5
    assert _R_STRONG_THRESHOLD == 2.0
    assert _R_NOISE_WEIGHT == 0.5
    assert _R_BASE_WEIGHT == 1.0
    assert _R_STRONG_WEIGHT == 2.0


def test_nan_r_maps_to_neutral_weight():
    """NaN → 1.0 (neutral) not 0 — don't silently zero-weight
    rows that had a numeric glitch."""
    assert r_multiple_to_weight(float("nan")) == 1.0


def test_non_numeric_r_maps_to_neutral_weight():
    """Type-error inputs → 1.0 neutral fallback."""
    assert r_multiple_to_weight("bad") == 1.0  # type: ignore[arg-type]
    assert r_multiple_to_weight(None) == 1.0  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# apply_loss_penalty — sign-aware amplification
# ══════════════════════════════════════════════════════════════════

def test_loss_amplified_by_fixed_constant():
    """Negative R gets `_LOSS_AMPLIFIER` (1.25×) — this is the
    asymmetry that says 'avoiding losses > capturing gains'."""
    assert apply_loss_penalty(-1.0, 1.0) == pytest.approx(1.25)
    assert apply_loss_penalty(-2.0, 2.0) == pytest.approx(2.5)


def test_winner_weight_unchanged():
    """Positive R: no amplification — the base weight passes through."""
    assert apply_loss_penalty(1.0, 1.5) == 1.5
    assert apply_loss_penalty(2.0, 2.0) == 2.0


def test_zero_r_unchanged_no_amplification():
    """R = 0 is not a loss — no penalty. Boundary pins that the
    `< 0` check is strict, not `<= 0`."""
    assert apply_loss_penalty(0.0, 1.0) == 1.0


def test_nan_r_no_amplification():
    """NaN sign is undefined; don't penalize."""
    assert apply_loss_penalty(float("nan"), 1.5) == 1.5


def test_non_numeric_r_returns_base_weight():
    """Bad R input → return base untouched."""
    assert apply_loss_penalty("bad", 1.5) == 1.5  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# compute_sample_weight_from_trade — end-to-end composition
# ══════════════════════════════════════════════════════════════════

def test_long_two_r_winner_is_cap_without_amplifier():
    """+2R LONG winner → base=2.0, no loss penalty → 2.0."""
    w = compute_sample_weight_from_trade(100.0, 110.0, 95.0, "LONG")
    assert w == pytest.approx(2.0)


def test_long_two_r_loser_hits_max_weight_ceiling():
    """-2R LONG loser → base=2.0 × 1.25 = 2.5. This is the max
    weight the pipeline can emit — matches `compute_signed_weight`'s
    max so `SignalModel.fit`'s 10× anti-explosion clip stays
    untriggered either way."""
    w = compute_sample_weight_from_trade(100.0, 90.0, 105.0, "LONG")
    assert w == pytest.approx(2.5)


def test_short_winner_and_loser_symmetric_to_long():
    """Direction must only flip the sign of R, not the weight
    math. +2R SHORT winner weighs same as +2R LONG winner."""
    long_win = compute_sample_weight_from_trade(100.0, 110.0, 95.0, "LONG")
    short_win = compute_sample_weight_from_trade(100.0, 90.0, 105.0, "SHORT")
    assert long_win == pytest.approx(short_win)

    long_loss = compute_sample_weight_from_trade(100.0, 90.0, 105.0, "LONG")
    short_loss = compute_sample_weight_from_trade(100.0, 110.0, 95.0, "SHORT")
    assert long_loss == pytest.approx(short_loss)


def test_stop_out_maps_to_noise_tier_half_weight_amplified():
    """Exit exactly at stop → R = -1.0. base=1.0, loss penalty
    applies → 1.25. Sanity-check that round-number stop-outs
    don't fall into the 0.5 noise tier."""
    w = compute_sample_weight_from_trade(100.0, 95.0, 95.0, "LONG")
    assert w == pytest.approx(1.25)


def test_trivial_exit_falls_in_noise_tier():
    """Tiny winner (0.2R) → base=0.5 noise, no amp → 0.5."""
    w = compute_sample_weight_from_trade(100.0, 101.0, 95.0, "LONG")
    assert w == pytest.approx(0.5)


def test_trivial_loser_noise_tier_amplified():
    """Tiny loser (-0.2R) → base=0.5 noise × 1.25 = 0.625."""
    w = compute_sample_weight_from_trade(100.0, 99.0, 95.0, "LONG")
    assert w == pytest.approx(0.625)


def test_missing_data_returns_noise_tier_weight():
    """None → R=0 → base=0.5, no loss penalty (R≥0) → 0.5. Callers
    should detect this upstream and route to magnitude fallback."""
    w = compute_sample_weight_from_trade(None, 110.0, 95.0, "LONG")
    assert w == pytest.approx(0.5)


def test_max_possible_weight_matches_magnitude_path():
    """Pin the invariant: `compute_sample_weight_from_trade` can
    never exceed 2.0 × _LOSS_AMPLIFIER. Must equal the magnitude
    pipeline's cap so the 10× anti-explosion clip in `SignalModel`
    is symmetric across both training paths."""
    from ai_core.learning_upgrade import compute_signed_weight

    # 10R catastrophic loss, capped.
    catastrophic = compute_sample_weight_from_trade(100.0, 0.0, 90.0, "LONG")
    magnitude_max = compute_signed_weight(-0.20)  # severe negative return
    assert catastrophic <= 2.0 * _LOSS_AMPLIFIER + 1e-9
    # And magnitude path caps at the same place.
    assert catastrophic == pytest.approx(magnitude_max)


# ══════════════════════════════════════════════════════════════════
# should_skip_row_by_r — noise-floor hard drop filter
# ══════════════════════════════════════════════════════════════════

def test_noise_floor_constant_below_noise_weight_threshold():
    """The filter floor (0.25) must stay strictly below the
    down-weight threshold (0.5) — the two layers compose: floor
    drops trash, tier down-weights weak signal."""
    assert _R_NOISE_FLOOR < _R_NOISE_THRESHOLD
    assert _R_NOISE_FLOOR == 0.25


def test_rows_below_noise_floor_are_skipped():
    """|R| < 0.25 → True (skip). These are trader fingers, slippage,
    or data glitches — not trainable signal."""
    assert should_skip_row_by_r(0.0) is True
    assert should_skip_row_by_r(0.24) is True
    assert should_skip_row_by_r(-0.1) is True
    assert should_skip_row_by_r(-0.249) is True


def test_boundary_value_not_skipped():
    """|R| == 0.25 is NOT skipped — the filter uses `<`, not `<=`.
    Pins the strict-inequality boundary so a future tweak can't
    silently drop the edge rows too."""
    assert should_skip_row_by_r(0.25) is False
    assert should_skip_row_by_r(-0.25) is False


def test_rows_at_or_above_noise_floor_kept():
    """|R| ≥ 0.25 → False (keep). Covers weak-tier, ramp, and
    strong-tier rows."""
    assert should_skip_row_by_r(0.3) is False
    assert should_skip_row_by_r(1.0) is False
    assert should_skip_row_by_r(-2.5) is False
    assert should_skip_row_by_r(10.0) is False


def test_nan_r_is_skipped_conservatively():
    """NaN magnitude is untrustworthy — skip rather than train on
    unknown signal."""
    assert should_skip_row_by_r(float("nan")) is True


def test_non_numeric_r_is_skipped():
    """Bad types (string / None from a dirty Mongo row) → skip.
    Conservative default: when in doubt, don't train on it."""
    assert should_skip_row_by_r("bad") is True  # type: ignore[arg-type]
    assert should_skip_row_by_r(None) is True  # type: ignore[arg-type]


def test_skip_floor_composes_with_tier_mapping():
    """Sanity integration: every kept row falls in a defined
    weight tier (never < _R_NOISE_WEIGHT). Every skipped row
    would have landed in the noise tier anyway — so the filter
    removes exactly the lowest-value training rows without
    disturbing the weight distribution above the floor."""
    # Below floor → would map to noise weight (0.5); now skipped.
    assert should_skip_row_by_r(0.1) is True
    assert r_multiple_to_weight(0.1) == _R_NOISE_WEIGHT
    # At/above floor → kept; weight is well-defined.
    assert should_skip_row_by_r(0.25) is False
    assert r_multiple_to_weight(0.25) == _R_NOISE_WEIGHT  # still noise tier
    # Above threshold → kept; weight ramps.
    assert should_skip_row_by_r(1.5) is False
    assert r_multiple_to_weight(1.5) == pytest.approx(1.5)


# ══════════════════════════════════════════════════════════════════
# summarize_r_distribution — drift metrics
# ══════════════════════════════════════════════════════════════════
    """Cold-start: no rows → zero stats, no crash."""
    out = summarize_r_distribution([])
    assert out == {"mean_r": 0.0, "strong_r_frac": 0.0}


def test_mean_r_matches_simple_average():
    """Drift signal: mean_r is raw arithmetic mean. -1, 0, +1
    batch averages to 0."""
    out = summarize_r_distribution([-1.0, 0.0, 1.0])
    assert out["mean_r"] == pytest.approx(0.0)


def test_strong_r_frac_uses_1_5_threshold_on_abs():
    """`strong_r_frac` is |R| ≥ 1.5 — mirrors the severity
    pipeline's `severity_strong_frac`."""
    # 5 values, 2 strong (abs ≥ 1.5): -2.0 and 1.5
    out = summarize_r_distribution([-2.0, 0.1, 1.0, 1.5, -0.2])
    assert out["strong_r_frac"] == pytest.approx(2 / 5)


def test_nan_values_dropped_from_aggregates():
    """One NaN row doesn't poison the mean — drop defensively."""
    out = summarize_r_distribution([1.0, float("nan"), -1.0])
    assert out["mean_r"] == pytest.approx(0.0)
    # Only 2 clean rows, neither ≥ 1.5.
    assert out["strong_r_frac"] == 0.0


def test_all_nan_returns_zero_stats():
    """If every row is NaN, don't divide by zero."""
    out = summarize_r_distribution([float("nan"), float("nan")])
    assert out == {"mean_r": 0.0, "strong_r_frac": 0.0}


def test_summarize_result_is_plain_floats():
    """Downstream logs/dashboards expect JSON-serializable floats,
    not numpy scalars."""
    out = summarize_r_distribution([1.0, 2.0, 3.0])
    assert isinstance(out["mean_r"], float)
    assert isinstance(out["strong_r_frac"], float)
    assert not math.isnan(out["mean_r"])
