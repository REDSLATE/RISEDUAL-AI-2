"""Tests for the selective opportunity-score floor.

Adds four floor families that override the global ``min_score``:

    * penny_breakout      (price ≤ $5, +3%+, rvol ≥ 2.0)  → 0.38
    * short_breakdown     (pct ≤ -2%, rvol ≥ 0.5)         → 0.36
    * large_cap_momo      (price ≥ $20, +0.5%+, rvol ≥ 1.5) → 0.35
    * low_vol_no_news     (anything else)                 → 0.447 (default)

Order matters — penny_breakout is tried before large_cap_momo so a
$3 stock up +5% on 3x rvol doesn't accidentally clear the 0.35
large-cap floor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from services.alpha_day_trader import (
    _family_floor,
    _FLOOR_LARGE_CAP_MOMO,
    _FLOOR_PENNY_BREAKOUT,
    _FLOOR_SHORT_BREAKDOWN,
    _FLOOR_LOW_VOL_NO_NEWS,
)


def _snap(**overrides) -> SimpleNamespace:
    """Cheap ``MarketSnapshot`` stand-in for the floor classifier.

    The classifier only reads ``price``, ``pct_change`` and
    ``relative_volume`` — no need to pay the full dataclass tax.
    """
    return SimpleNamespace(
        symbol=overrides.get("symbol", "TEST"),
        price=overrides.get("price", 100.0),
        pct_change=overrides.get("pct_change", 0.0),
        relative_volume=overrides.get("relative_volume", 1.0),
        vwap=overrides.get("vwap", 100.0),
        volume_acceleration=overrides.get("volume_acceleration", 1.0),
        spread_bps=overrides.get("spread_bps", 5.0),
        open_price=overrides.get("open_price", 100.0),
        high=overrides.get("high", 102.0),
        low=overrides.get("low", 98.0),
        timestamp=overrides.get("timestamp", datetime.now(timezone.utc)),
        recent_bars=overrides.get("recent_bars", None),
    )


# ─────────────────────────────────────────────
#  Family classification
# ─────────────────────────────────────────────
def test_large_cap_momentum_gets_lowest_floor():
    """NVDA-shape: $500, +2%, rvol 2.5x."""
    snap = _snap(price=500.0, pct_change=2.0, relative_volume=2.5)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "large_cap_momo"
    assert floor == _FLOOR_LARGE_CAP_MOMO


def test_adbe_shape_now_lands_in_large_cap_momo():
    """The exact ADBE case from 9/2 production diagnostic: price
    ~$286, pct_change small positive, rvol ~1.3. Old thresholds
    (0.5%/1.5×) missed by a hair → fell to the 0.447 default and
    scored below floor. New thresholds (0.3%/1.2×) catch it so
    the 0.35 floor applies."""
    snap = _snap(price=286.0, pct_change=0.4, relative_volume=1.3)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "large_cap_momo"
    assert floor == _FLOOR_LARGE_CAP_MOMO


def test_penny_breakout_gets_penny_floor_not_large_cap():
    """A $3 stock up +5% on 3x rvol — must classify as
    penny_breakout (0.38), NEVER slip into large_cap_momo (0.35)."""
    snap = _snap(price=3.0, pct_change=5.0, relative_volume=3.0)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "penny_breakout"
    assert floor == _FLOOR_PENNY_BREAKOUT


def test_short_breakdown_fires_regardless_of_price():
    """PLTR-shape: $185, -7.55%, rvol 0.76."""
    snap = _snap(price=185.5, pct_change=-7.55, relative_volume=0.76)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "short_breakdown"
    assert floor == _FLOOR_SHORT_BREAKDOWN


def test_low_volume_default_family_keeps_the_original_global_floor():
    """A boring stock — small move, thin volume — stays gated at
    the historical 0.447 floor so we don't waste tickets on weak
    signals."""
    snap = _snap(price=50.0, pct_change=0.1, relative_volume=0.8)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "low_vol_no_news"
    assert floor >= _FLOOR_LOW_VOL_NO_NEWS


def test_default_floor_never_drops_below_low_vol_family_floor():
    """If a mis-configured env var pushes the global floor below
    0.447, the low-vol family branch still returns ≥ 0.447 so a
    thin no-news setup can't sneak through."""
    snap = _snap(price=50.0, pct_change=0.1, relative_volume=0.8)
    floor, _ = _family_floor(snap, default_floor=0.20)
    assert floor >= _FLOOR_LOW_VOL_NO_NEWS


def test_boundary_price_5_still_qualifies_as_penny():
    """The penny-breakout branch is inclusive at $5 exactly so a
    $5.00 stock up 3% on 2x rvol still gets the penny floor."""
    snap = _snap(price=5.0, pct_change=3.0, relative_volume=2.0)
    floor, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "penny_breakout"
    assert floor == _FLOOR_PENNY_BREAKOUT


def test_negative_move_with_thin_volume_still_hits_short_breakdown():
    """Down 3% on rvol 0.5 is still a legitimate short_breakdown.
    We deliberately don't require heavy volume — a slow bleed on
    thin volume is exactly the setup ``SHORT_SIDE_EXHAUSTION``
    is designed to catch."""
    snap = _snap(price=42.0, pct_change=-3.0, relative_volume=0.5)
    _, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "short_breakdown"


def test_small_positive_move_with_low_rvol_stays_low_vol():
    """+0.5% on rvol 1.0 doesn't clear large_cap_momo (needs rvol
    ≥ 1.5). Must stay in low_vol_no_news."""
    snap = _snap(price=100.0, pct_change=0.5, relative_volume=1.0)
    _, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "low_vol_no_news"


def test_penny_move_without_volume_is_low_vol_not_penny():
    """A $2 stock up +5% on rvol 0.8 lacks the participation we
    require for penny_breakout — falls through to low_vol_no_news."""
    snap = _snap(price=2.0, pct_change=5.0, relative_volume=0.8)
    _, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "low_vol_no_news"


def test_penny_short_breakdown_still_fires_as_short_side():
    """A $3 stock down -6% on rvol 2 should classify as
    short_breakdown (not penny_breakout — penny_breakout requires a
    positive move) so it gets the 0.36 floor and can arm the
    SHORT_SIDE_EXHAUSTION long later."""
    snap = _snap(price=3.0, pct_change=-6.0, relative_volume=2.0)
    _, tag = _family_floor(snap, default_floor=0.447)
    assert tag == "short_breakdown"


# ─────────────────────────────────────────────
#  Regression: the numbers match the operator's spec
# ─────────────────────────────────────────────
def test_operator_spec_floor_values():
    """Guardrail — the four family floors must match the values
    the operator specified in the plan. Anyone tweaking them has
    to update the spec here too."""
    assert _FLOOR_LARGE_CAP_MOMO == 0.35
    assert _FLOOR_PENNY_BREAKOUT == 0.38
    assert _FLOOR_SHORT_BREAKDOWN == 0.36
    assert _FLOOR_LOW_VOL_NO_NEWS == 0.447
