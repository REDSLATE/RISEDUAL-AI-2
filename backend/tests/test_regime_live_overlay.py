"""Tests for the live-quote overlay on classifier (2026-02).

Regression the fix targets: on a mid-session tick with yesterday's
daily bar showing SPY -0.55%, the classifier called the market
``session_chop`` even though today's tape was clearly +0.52%. The
live-quote overlay flips prev_close to yesterday's completed close
and uses today's live price as the new "close" — so classification
reflects the actual intraday move.
"""
from __future__ import annotations

import pytest

from services.fast_intraday_regime import _classify_bars


def _bars(*, prev_close=100.0, open_=99.5, high=100.5, low=99.2,
          close=99.5, volume=1_000_000, count=25):
    """Build a synthetic daily bar sequence with a specified 'today'."""
    out = []
    # 23 filler bars for ATR calc
    for i in range(count - 2):
        out.append({"open": 99.0 + i * 0.01, "high": 100.0 + i * 0.01,
                     "low": 98.5 + i * 0.01, "close": 99.5 + i * 0.01,
                     "volume": volume})
    # yesterday's completed bar (bars[-2])
    out.append({"open": prev_close - 0.5, "high": prev_close + 0.5,
                 "low": prev_close - 0.6, "close": prev_close,
                 "volume": volume})
    # 'today' bar (bars[-1]) — this is what the classifier reads by default
    out.append({"open": open_, "high": high, "low": low,
                 "close": close, "volume": volume})
    return out


def test_no_live_quote_falls_back_to_daily():
    """Yesterday: 100 close. Today's daily bar closes down at 99.4
    (-0.6%). No live quote → classifier calls this a down day."""
    bars = _bars(prev_close=100.0, open_=99.5, high=100.5, low=99.0,
                  close=99.4)
    label, features = _classify_bars(bars, live_quote=None)
    assert features["today_return_pct"] == pytest.approx(-0.6, abs=0.05)
    assert features["live_overlay"] is False
    # Falls into risk_off / trend_down / session_chop family
    assert label in ("risk_off", "trend_down", "session_chop")


def test_live_quote_flips_stale_down_to_todays_up():
    """The bug scenario: yesterday closed -0.6%, today live is +0.5%
    from yesterday's close. Classifier must reflect TODAY's move."""
    bars = _bars(prev_close=100.0, open_=99.5, high=100.5, low=99.0,
                  close=99.4)  # yesterday closed -0.6%
    live_price = 99.4 * 1.005  # +0.5% intraday today from yest close
    label, features = _classify_bars(bars, live_quote={"last": live_price})
    assert features["today_return_pct"] == pytest.approx(0.5, abs=0.05)
    assert features["live_overlay"] is True
    # +0.5% should NOT be session_chop
    assert label != "session_chop"


def test_live_quote_none_falls_back_gracefully():
    bars = _bars()
    label, features = _classify_bars(bars, live_quote=None)
    assert features["live_overlay"] is False
    assert label != "UNKNOWN"


def test_live_quote_bad_shape_falls_back_gracefully():
    bars = _bars()
    label, features = _classify_bars(bars, live_quote={"garbage": "data"})
    # No usable price field → treated as no-quote → falls back
    assert features["live_overlay"] is False


def test_live_quote_zero_price_falls_back():
    bars = _bars()
    label, features = _classify_bars(bars, live_quote={"last": 0.0})
    assert features["live_overlay"] is False


def test_live_quote_accepts_price_field_alias():
    bars = _bars(prev_close=100.0, open_=99.5, close=99.4)
    label, features = _classify_bars(bars, live_quote={"price": 100.2})
    # +0.8% off yesterday's close of 99.4 (= 99.4 * 1.008 ≈ 100.2)
    assert features["live_overlay"] is True
    assert features["today_return_pct"] > 0
