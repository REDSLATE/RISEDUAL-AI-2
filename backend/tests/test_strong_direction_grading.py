"""
Regression tests for the STRONG_* / WEAK_* direction-token bug.

Pre-fix (2026-05-01) `prediction_tracker.DIRECTION_BULLISH` and
`DIRECTION_BEARISH` were missing the `STRONG_*` and `WEAK_*` tokens
emitted by `signal_dispatcher`, so `grade_prediction()` fell through
to the `# unknown direction → conservative` branch at the bottom and
returned ``STRONG_MISS`` regardless of how the price actually moved.

That bug:
* Spammed the operator with false toxic-spike alerts every night.
* Poisoned ChromaDB — winning trades got re-tagged as `toxic_lesson`.
* Polluted the LearningEngine win/loss stats fleet-wide.

These tests pin the behaviour going forward so any future refactor
that breaks direction-token recognition will fail loudly here before
it can hit production data again.
"""

import pytest

from services.prediction_tracker import (
    grade_prediction,
    DIRECTION_BULLISH,
    DIRECTION_BEARISH,
)


# ── Direction set membership (the actual bug) ──────────────────────


def test_strong_buy_is_recognised_as_bullish():
    """STRONG_BUY MUST be in the bullish set or grading collapses."""
    assert "STRONG_BUY" in DIRECTION_BULLISH


def test_weak_buy_is_recognised_as_bullish():
    assert "WEAK_BUY" in DIRECTION_BULLISH


def test_strong_sell_is_recognised_as_bearish():
    assert "STRONG_SELL" in DIRECTION_BEARISH


def test_weak_sell_is_recognised_as_bearish():
    assert "WEAK_SELL" in DIRECTION_BEARISH


# ── End-to-end grading (the operator-visible symptom) ─────────────


def test_strong_buy_grades_as_hit_when_price_rises():
    """SPY STRONG_BUY @ 713.94 → 718.66 (+0.66%) MUST be a HIT.

    This was the canonical false-positive in the operator's
    2026-04-25 toxic-spike alert.
    """
    result = grade_prediction(
        direction="STRONG_BUY",
        price_at_prediction=713.94,
        price_now=718.66,
    )
    assert result in {"STRONG_HIT", "WEAK_HIT", "NEUTRAL"}, (
        f"STRONG_BUY with rising price was graded {result!r} — "
        f"this is the regression that flooded toxic-spike alerts."
    )


def test_strong_sell_grades_as_hit_when_price_falls():
    """STRONG_SELL @ 500 → 495 (-1%) MUST be a HIT, not a miss."""
    result = grade_prediction(
        direction="STRONG_SELL",
        price_at_prediction=500.00,
        price_now=495.00,
    )
    assert result in {"STRONG_HIT", "WEAK_HIT", "NEUTRAL"}


def test_weak_buy_grades_as_hit_when_price_rises():
    result = grade_prediction(
        direction="WEAK_BUY",
        price_at_prediction=200.00,
        price_now=206.00,
    )
    assert result in {"STRONG_HIT", "WEAK_HIT", "NEUTRAL"}


def test_weak_sell_grades_as_hit_when_price_falls():
    result = grade_prediction(
        direction="WEAK_SELL",
        price_at_prediction=200.00,
        price_now=194.00,
    )
    assert result in {"STRONG_HIT", "WEAK_HIT", "NEUTRAL"}


# ── Real misses still grade correctly (no over-correction) ────────


def test_strong_buy_grades_as_miss_when_price_falls_significantly():
    """NVDA STRONG_BUY @ 208.27 → 199.57 (-4.18%) IS a real miss."""
    result = grade_prediction(
        direction="STRONG_BUY",
        price_at_prediction=208.27,
        price_now=199.57,
    )
    assert result in {"STRONG_MISS", "WEAK_MISS"}


def test_strong_sell_grades_as_miss_when_price_rises_significantly():
    """QQQ STRONG_SELL @ 644.33 → 663.88 (+3.03%) IS a real miss."""
    result = grade_prediction(
        direction="STRONG_SELL",
        price_at_prediction=644.33,
        price_now=663.88,
    )
    assert result in {"STRONG_MISS", "WEAK_MISS"}


# ── Defence-in-depth: an unknown direction still falls through ────


def test_truly_unknown_direction_still_graded_as_miss():
    """If a future verdict label sneaks in that nobody mapped, the
    safety net at the bottom of `grade_prediction` should still mark
    it as STRONG_MISS so the data hygiene break is loud, not silent."""
    result = grade_prediction(
        direction="MAYBE_KIND_OF_BUY",
        price_at_prediction=100.0,
        price_now=110.0,
    )
    assert result == "STRONG_MISS"


# ── Parametric matrix: every supported alias for both sides ───────


@pytest.mark.parametrize("direction", sorted(DIRECTION_BULLISH))
def test_every_bullish_alias_grades_rising_price_as_hit(direction):
    assert grade_prediction(
        direction=direction,
        price_at_prediction=100.0,
        price_now=110.0,  # +10% — well above any tolerance band
    ) in {"STRONG_HIT", "WEAK_HIT"}


@pytest.mark.parametrize("direction", sorted(DIRECTION_BEARISH))
def test_every_bearish_alias_grades_falling_price_as_hit(direction):
    assert grade_prediction(
        direction=direction,
        price_at_prediction=100.0,
        price_now=90.0,  # -10%
    ) in {"STRONG_HIT", "WEAK_HIT"}
