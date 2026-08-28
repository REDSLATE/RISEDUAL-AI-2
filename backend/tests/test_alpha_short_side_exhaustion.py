"""Tests for the SHORT_SIDE_EXHAUSTION pattern.

Covers the PLTR-shaped case that was dying at ``no_pattern_match``
in the Why-Not-Trade diagnostic: sharp -3% to -12% decline where the
current bar shows stabilization.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.alpha_day_trader import (
    AlphaPatternEngine,
    MarketSnapshot,
    SetupType,
    MEAN_REVERT_PATTERNS,
)


def _snap(**overrides) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=overrides.get("symbol", "PLTR"),
        timestamp=overrides.get("timestamp", datetime.now(timezone.utc)),
        price=overrides.get("price", 185.5),
        vwap=overrides.get("vwap", 200.0),
        volume=overrides.get("volume", 5_000_000),
        avg_volume=overrides.get("avg_volume", 4_000_000),
        pct_change=overrides.get("pct_change", -7.55),
        relative_volume=overrides.get("relative_volume", 0.76),
        volume_acceleration=overrides.get("volume_acceleration", 0.9),
        spread_bps=overrides.get("spread_bps", 18.0),
        bid=overrides.get("bid", 185.0),
        ask=overrides.get("ask", 186.0),
        open_price=overrides.get("open_price", 200.0),
        high=overrides.get("high", 201.0),
        low=overrides.get("low", 183.0),
        recent_bars=overrides.get("recent_bars", None),
    )


def _engine() -> AlphaPatternEngine:
    return AlphaPatternEngine()


def test_pltr_shaped_snapshot_fires_short_side_exhaustion():
    """The exact features from the live Why-Not-Trade diagnostic:
    PLTR at -7.55%, rvol 0.76, price stabilizing above intraday low
    → must produce a SHORT_SIDE_EXHAUSTION setup instead of falling
    through to ``no_pattern_match``.
    """
    snap = _snap(
        price=185.5, pct_change=-7.55, relative_volume=0.76,
        low=183.0,   # price 185.5 ≥ 183.0 * 1.003 = 183.55 → stable
        open_price=200.0,
    )
    setup = _engine().detect(snap, slow_regime="choppy_meanrevert", fast_regime="trend_up")
    assert setup is not None, "expected a setup for the PLTR shape"
    assert setup.setup_type == SetupType.SHORT_SIDE_EXHAUSTION
    # Long entry: trigger price must be ABOVE current price
    assert setup.trigger_price > snap.price
    # Invalidation must be BELOW current price
    assert setup.invalidation_price < snap.price


def test_free_fall_below_neg_12_percent_does_not_fire():
    """A -15% collapse is outside the -12% floor — the pattern is
    designed to buy the *bounce*, not catch a falling knife."""
    snap = _snap(pct_change=-15.0, low=170.0)
    setup = _engine().detect(snap)
    if setup is not None:
        assert setup.setup_type != SetupType.SHORT_SIDE_EXHAUSTION


def test_shallow_neg_2_percent_does_not_fire():
    """-2% is the short_breakdown family floor, but the PATTERN
    itself only fires from -3% down — a shallow -2% dip should not
    become a SHORT_SIDE_EXHAUSTION setup (other patterns may fire)."""
    snap = _snap(pct_change=-2.0, low=196.0)
    setup = _engine().detect(snap)
    if setup is not None:
        assert setup.setup_type != SetupType.SHORT_SIDE_EXHAUSTION


def test_snap_still_dumping_far_below_low_does_not_fire():
    """Price sitting AT the intraday low with no bounce = still
    dumping. Must not fire — we want confirmation of stabilization."""
    snap = _snap(
        price=170.0, pct_change=-7.5, relative_volume=1.0,
        low=170.0,           # price at low exactly — 170 < 170*1.003
        open_price=185.0,    # 170 < 185 * 0.995 → fails the OR too
    )
    setup = _engine().detect(snap)
    if setup is not None:
        assert setup.setup_type != SetupType.SHORT_SIDE_EXHAUSTION


def test_panic_rvol_over_three_does_not_fire():
    """rvol > 3.0 = still panicking. Wait for volume to fade first."""
    snap = _snap(pct_change=-7.5, relative_volume=5.0, low=170.0)
    setup = _engine().detect(snap)
    if setup is not None:
        assert setup.setup_type != SetupType.SHORT_SIDE_EXHAUSTION


def test_short_side_exhaustion_is_in_the_mean_revert_family():
    """Regression: the new pattern must be classified as mean-
    reversion so it gets the chop-regime score boost (and doesn't
    accidentally get the momentum-in-chop penalty)."""
    assert SetupType.SHORT_SIDE_EXHAUSTION.value in MEAN_REVERT_PATTERNS


def test_price_hold_via_open_price_fallback():
    """When there's no session low yet but price is holding at
    open, the pattern must still fire via the ``open_price``
    fallback branch of the stability check."""
    snap = _snap(
        price=185.5, pct_change=-5.0, relative_volume=1.0,
        low=0.0,   # unknown / not populated yet
        open_price=185.0,   # 185.5 ≥ 185.0 * 0.995 → stable
    )
    setup = _engine().detect(snap)
    assert setup is not None
    assert setup.setup_type == SetupType.SHORT_SIDE_EXHAUSTION
