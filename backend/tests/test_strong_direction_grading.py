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
    canonical_ai_dir,
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


# ── canonical_ai_dir: the centralised LONG/SHORT/UNKNOWN mapping ───
#
# This helper replaces the scattered hardcoded direction tuples that
# used to live in `prediction_tracker.py:654`, `research_shadow.py`,
# and friends. Every service that needs to map a verdict token to a
# trade side must go through `canonical_ai_dir`. The tests below pin
# that contract.


def test_canonical_strong_buy_maps_to_long():
    assert canonical_ai_dir("STRONG_BUY") == "LONG"


def test_canonical_weak_buy_maps_to_long():
    assert canonical_ai_dir("WEAK_BUY") == "LONG"


def test_canonical_strong_sell_maps_to_short():
    assert canonical_ai_dir("STRONG_SELL") == "SHORT"


def test_canonical_weak_sell_maps_to_short():
    assert canonical_ai_dir("WEAK_SELL") == "SHORT"


def test_canonical_buy_alias_maps_to_long():
    """Legacy BUY/LONG/BULLISH tokens still resolve."""
    assert canonical_ai_dir("BUY") == "LONG"
    assert canonical_ai_dir("BULLISH") == "LONG"
    assert canonical_ai_dir("LONG") == "LONG"
    assert canonical_ai_dir("UP") == "LONG"


def test_canonical_sell_alias_maps_to_short():
    assert canonical_ai_dir("SELL") == "SHORT"
    assert canonical_ai_dir("BEARISH") == "SHORT"
    assert canonical_ai_dir("SHORT") == "SHORT"
    assert canonical_ai_dir("DOWN") == "SHORT"


def test_canonical_hold_returns_unknown():
    """HOLD is NOT a trade side — must NOT default to SHORT.

    The pre-fix tuple ``("BUY", "LONG", "BULLISH")`` defaulted every
    non-bullish token to SHORT, including HOLD. That's how the
    LearningEngine ended up with phantom SHORT pending trades whose
    prediction was actually "wait it out".
    """
    assert canonical_ai_dir("HOLD") == "UNKNOWN"
    assert canonical_ai_dir("WAIT") == "UNKNOWN"
    assert canonical_ai_dir("NEUTRAL") == "UNKNOWN"


def test_canonical_empty_returns_unknown():
    assert canonical_ai_dir("") == "UNKNOWN"
    assert canonical_ai_dir(None) == "UNKNOWN"
    assert canonical_ai_dir("   ") == "UNKNOWN"


def test_canonical_unknown_token_returns_unknown_not_short():
    """The whole point of the centralised helper: unknown tokens
    must NOT silently default to SHORT. Caller skips, doesn't guess."""
    assert canonical_ai_dir("MAYBE_KIND_OF_BUY") == "UNKNOWN"
    assert canonical_ai_dir("STRONG_MAYBE") == "UNKNOWN"


def test_canonical_handles_mixed_case_and_whitespace():
    assert canonical_ai_dir("strong_buy") == "LONG"
    assert canonical_ai_dir(" Strong_Sell ") == "SHORT"
    assert canonical_ai_dir("\tBUY\n") == "LONG"
