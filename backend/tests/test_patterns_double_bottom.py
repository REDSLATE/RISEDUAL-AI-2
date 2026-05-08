"""Regression tests for the refactored double-bottom detector.

Guards against behavioural drift after we split the monolithic
`detect_double_bottom` (cyclomatic 17) into three helpers:
  * `_find_local_minima`
  * `_evaluate_bottom_pair`
  * `detect_double_bottom`

The test fixtures drive each branch of the original rule-of-four
(separation, neckline rise, breakout confirmation) so any future
re-tune of the thresholds has to pass the full matrix.
"""
from __future__ import annotations

import pandas as pd
import pytest

from risedual_core.ml.patterns import (
    _evaluate_bottom_pair,
    _find_local_minima,
    detect_double_bottom,
)


def _bars(values: list[dict]) -> pd.DataFrame:
    """Fixture helper — turns a list of (high, low, close) dicts
    into the OHLCV dataframe the detector expects."""
    return pd.DataFrame(
        [
            {"open": v.get("open", v["close"]), "high": v["high"], "low": v["low"], "close": v["close"], "volume": 1000}
            for v in values
        ]
    )


def _baseline_pattern() -> pd.DataFrame:
    """30-bar double-bottom with a clear neckline peak + breakout.
    Two lows at bar 5 and bar 20 both near $95, peak at bar 12 near
    $100, current close at bar 29 above neckline."""
    rows = []
    for i in range(30):
        if i in (5, 20):
            low, close = 95.0, 96.0
        elif i in (11, 12, 13):
            low, close = 99.0, 100.2
        elif i >= 25:
            low, close = 100.5, 101.0      # breakout zone
        else:
            low, close = 97.0, 98.0
        rows.append({"high": close + 0.5, "low": low, "close": close})
    return _bars(rows)


def test_find_local_minima_returns_strict_minima():
    # Both values strictly lower than BOTH neighbours. 10-9-10 → 9 is a
    # strict minimum; 8-7-8 → 7 is too.
    lows = pd.Series([10, 9, 10, 8, 7, 8, 9])
    assert _find_local_minima(lows) == [1, 4]


def test_find_local_minima_ignores_ties():
    # A tie with either neighbour disqualifies the bar (strict < only).
    lows = pd.Series([10, 9, 9, 10])
    assert _find_local_minima(lows) == []


def test_find_local_minima_flat_series():
    lows = pd.Series([5, 5, 5, 5])
    assert _find_local_minima(lows) == []


def test_detects_baseline_pattern():
    df = _baseline_pattern()
    result = detect_double_bottom(df)
    assert result.detected is True
    assert result.name == "double_bottom"
    assert 0.0 < result.confidence <= 1.0
    assert "Double bottom" in result.description
    # Bar index points at the MORE-RECENT of the two lows.
    assert result.bar_index == 20


def test_rejects_when_lows_spread_too_wide():
    """Two minima but >3% apart — should not trigger."""
    rows = []
    for i in range(30):
        if i == 5:
            low, close = 80.0, 82.0       # much deeper
        elif i == 20:
            low, close = 95.0, 96.0       # shallower
        elif i in (11, 12, 13):
            low, close = 99.0, 100.2
        elif i >= 25:
            low, close = 101.0, 101.5
        else:
            low, close = 97.0, 98.0
        rows.append({"high": close + 0.5, "low": low, "close": close})
    result = detect_double_bottom(_bars(rows))
    assert result.detected is False


def test_rejects_when_neckline_rise_is_too_shallow():
    """Neckline barely above the lows — should not trigger."""
    rows = []
    for i in range(30):
        if i in (5, 20):
            low, close = 95.0, 95.5
        elif i in (11, 12, 13):
            low, close = 96.0, 96.2       # <2% above avg_low
        elif i >= 25:
            low, close = 96.3, 96.4
        else:
            low, close = 95.8, 95.9
        rows.append({"high": close + 0.1, "low": low, "close": close})
    result = detect_double_bottom(_bars(rows))
    assert result.detected is False


def test_rejects_when_current_close_below_neckline():
    """Pattern formed but price hasn't broken back above the peak yet."""
    rows = []
    for i in range(30):
        if i in (5, 20):
            low, close = 95.0, 96.0
        elif i in (11, 12, 13):
            low, close = 99.0, 100.2
        elif i >= 25:
            low, close = 97.5, 98.5       # still below neckline at 100.2
        else:
            low, close = 97.0, 98.0
        rows.append({"high": close + 0.5, "low": low, "close": close})
    result = detect_double_bottom(_bars(rows))
    assert result.detected is False


def test_rejects_when_too_few_bars():
    df = _bars([{"high": 10, "low": 9, "close": 9.5}] * 10)
    result = detect_double_bottom(df)
    assert result.detected is False


def test_evaluate_bottom_pair_rejects_when_too_close_in_time():
    """Helper directly: two minima only 2 bars apart → rejected."""
    lows = pd.Series([100.0, 95.0, 96.0, 95.0, 100.0])
    closes = pd.Series([100.0, 95.0, 96.0, 95.0, 100.0])
    # bars 1 and 3 are only 2 apart, below the 5-bar minimum.
    assert _evaluate_bottom_pair(1, 3, lows, closes, "double_bottom") is None


def test_evaluate_bottom_pair_rejects_zero_low():
    lows = pd.Series([100.0, 0.0, 50.0, 0.0, 100.0])
    closes = pd.Series([100.0, 0.0, 50.0, 0.0, 100.0])
    assert _evaluate_bottom_pair(1, 3, lows, closes, "double_bottom") is None


def test_most_recent_qualifying_pair_wins():
    """Three bottoms in a row — detector should report the most-recent
    valid (j1, j2) pair, not the earliest."""
    rows = []
    # bars 5, 15, 25 all have a low ≈ 95
    for i in range(35):
        if i in (5, 15, 25):
            low, close = 95.0, 96.0
        elif i in (10, 11, 20, 21):
            low, close = 99.0, 100.2
        elif i >= 30:
            low, close = 101.0, 101.5
        else:
            low, close = 97.0, 98.0
        rows.append({"high": close + 0.5, "low": low, "close": close})
    result = detect_double_bottom(_bars(rows))
    assert result.detected is True
    assert result.bar_index == 25  # most-recent bottom
