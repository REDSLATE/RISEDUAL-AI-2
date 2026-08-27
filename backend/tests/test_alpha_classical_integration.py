"""Integration tests — classical chart patterns wired into
``AlphaPatternEngine.detect()`` (2026-02).

Guardrails:
* A confirmed bullish classical pattern (double bottom, inv H&S,
  falling wedge) beats the single-bar setups on the same tick.
* A confirmed bearish classical pattern acts as a veto — returns
  ``None`` even when a bullish single-bar setup would otherwise match.
* Empty ``recent_bars`` (older snapshots) does not crash the engine
  and single-bar detection continues to work.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.alpha_day_trader import (
    AlphaPatternEngine,
    MarketSnapshot,
    SetupType,
    CLASSICAL_PATTERNS,
)


def _bar(o, h, l, c, v=1_000_000):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "date": ""}


def _snap(bars, **overrides):
    base = dict(
        symbol="TEST", price=100.0, volume=1e6, avg_volume=1e6,
        relative_volume=1.0, open_price=100.0, high=100.0, low=100.0,
        vwap=100.0, bid=99.99, ask=100.01, spread_bps=2.0,
        pct_change=0.0, volume_acceleration=1.0,
        timestamp=datetime.now(timezone.utc),
        recent_bars=bars,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


# ─── confirmed bullish classical wins over single-bar setups ────


def test_confirmed_double_bottom_beats_single_bar_detectors():
    """Double bottom breakout on bar 5 should be selected as the setup
    even when the snapshot's HOD/VWAP fields would ALSO produce a
    single-bar match on their own."""
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(104, 107, 103, 106),
    ]
    # Snapshot fields ALSO try to trigger HOD_BREAK (price 106, high 107)
    m = _snap(bars, price=106.0, high=107.0, low=103.0, open_price=104.0,
              vwap=104.0, relative_volume=3.0, volume_acceleration=1.5,
              pct_change=1.5)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.DOUBLE_BOTTOM
    assert setup.setup_type.value in CLASSICAL_PATTERNS
    assert setup.score == pytest.approx(0.78, abs=0.001)


# ─── confirmed bearish classical vetoes bullish setups ──────────


def test_confirmed_double_top_vetoes_hod_setup():
    """Even a picture-perfect HOD_BREAK single-bar setup must be
    suppressed when a confirmed double top is active on the symbol."""
    bearish_bars = [
        _bar(99, 100, 95, 96),
        _bar(96, 99, 94, 95),
        _bar(95, 100.3, 96, 97),
        _bar(97, 98, 94.5, 95),
        _bar(95, 96, 92, 93),   # confirmed double_top break
    ]
    # Also stage a strong bullish momentum snapshot
    m = _snap(bearish_bars, price=93.0, high=93.1, low=92.0, open_price=95.0,
              vwap=94.0, relative_volume=3.0, volume_acceleration=1.5,
              pct_change=-2.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    # Veto returns None — no long allowed on a confirmed bear pattern
    assert setup is None


# ─── empty recent_bars: engine still works ──────────────────────


def test_empty_recent_bars_does_not_crash_engine():
    """Older snapshots with no ``recent_bars`` still exercise the
    single-bar detectors as before — no breakage from the wire-in."""
    m = _snap([], price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=5.0, pct_change=5.0,
              volume_acceleration=2.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.HOD_BREAK


def test_few_bars_falls_through_to_single_bar():
    """Fewer than 5 bars: classical detectors return None, single-bar
    layer handles the tick as usual."""
    bars = [_bar(100, 101, 99, 100)] * 3  # too few for any classical pattern
    m = _snap(bars, price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=5.0, pct_change=5.0,
              volume_acceleration=2.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.HOD_BREAK


def test_forming_bullish_does_not_short_circuit():
    """A *forming* (not yet confirmed) classical pattern should NOT
    take priority — engine should fall through to single-bar
    detectors so momentum setups still fire."""
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(104, 104.5, 103, 104.5),  # close < neckline — only "forming"
    ]
    m = _snap(bars, price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=5.0, pct_change=5.0,
              volume_acceleration=2.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.HOD_BREAK  # not double_bottom (only forming)
