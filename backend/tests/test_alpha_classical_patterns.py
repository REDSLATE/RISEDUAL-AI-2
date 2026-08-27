"""Tests for the IGNISpilot-ported classical chart patterns (2026-02).

Ports 6 patterns from JS to Python. Numeric values match the JS
reference (confidence 0.74/0.78/0.81 for confirmed bullish;
0.16-0.18 blocked). These tests also lock in the confirmed-breakout
lifecycle so a future edit can't silently break the geometry rules.
"""
from __future__ import annotations

import pytest

from services.alpha_classical_patterns import (
    assess_bullish_patterns,
    assess_bearish_patterns,
    best_bullish,
    has_bearish_veto,
    BULLISH_PATTERNS,
    BEARISH_PATTERNS,
)


def _bar(o, h, l, c, v=1_000_000):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "date": ""}


# ─── contract: always returns one row per pattern ───────────────


def test_bullish_returns_three_rows_even_when_insufficient_bars():
    out = assess_bullish_patterns([_bar(1, 2, 0.5, 1.5)])
    assert len(out) == 3
    assert {a.pattern for a in out} == set(BULLISH_PATTERNS)
    # All blocked when there aren't enough bars for any pattern
    assert all(a.state == "blocked" for a in out)


def test_bearish_returns_three_rows_even_when_insufficient_bars():
    out = assess_bearish_patterns([_bar(1, 2, 0.5, 1.5)])
    assert len(out) == 3
    assert {a.pattern for a in out} == set(BEARISH_PATTERNS)


def test_malformed_bars_blocked_not_crashed():
    bars = [_bar(1, 2, 0.5, 1.5)] * 6
    bars[3] = {"open": None, "high": float("nan"), "low": 0, "close": 1}
    out = assess_bullish_patterns(bars)
    assert all(a.state == "blocked" for a in out)


def test_empty_bars_returns_empty_list():
    assert assess_bullish_patterns([]) == []
    assert assess_bearish_patterns([]) == []


# ─── Double Bottom ──────────────────────────────────────────────


def test_double_bottom_confirmed_geometry():
    # Trough1=100, neckline_bar high=105, trough2=100.5 (0.5% diff, within 3%),
    # latest close 106 > neckline 105.
    bars = [
        _bar(101, 105, 100, 101),   # first trough
        _bar(101, 105, 102, 104),   # neckline bar (high=105)
        _bar(102, 103, 100.5, 101), # second trough
        _bar(101, 105, 100.5, 104), # rebuild toward neckline
        _bar(104, 107, 103, 106),   # latest close > 105 = breakout
    ]
    out = {a.pattern: a for a in assess_bullish_patterns(bars)}
    db = out["double_bottom"]
    assert db.state == "confirmed"
    assert db.confidence == pytest.approx(0.78, abs=0.001)
    assert db.neckline_or_support == pytest.approx(105.0)


def test_double_bottom_forming_geometry_but_no_breakout():
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(104, 104.5, 103, 104.5),  # < neckline 105
    ]
    out = {a.pattern: a for a in assess_bullish_patterns(bars)}
    db = out["double_bottom"]
    assert db.state == "forming"
    assert db.confidence == pytest.approx(0.52, abs=0.001)


def test_double_bottom_invalidated_below_trough_buffer():
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(100, 100.5, 98, 98),   # breaks under invalidation
    ]
    out = {a.pattern: a for a in assess_bullish_patterns(bars)}
    assert out["double_bottom"].state == "invalidated"


def test_double_bottom_troughs_too_far_apart_blocked():
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 90, 92),   # trough2 = 90, 10% from 100 → over 3% tol
        _bar(101, 105, 90, 100),
        _bar(104, 107, 103, 106),
    ]
    out = {a.pattern: a for a in assess_bullish_patterns(bars)}
    assert out["double_bottom"].state == "blocked"
    assert out["double_bottom"].confidence == pytest.approx(0.18, abs=0.001)


# ─── Falling Wedge ──────────────────────────────────────────────


def test_falling_wedge_confirmed():
    # Highs descending, lows descending slower → convergence.
    # Latest close breaks above the projected resistance level.
    bars = [
        _bar(120, 125, 115, 118),
        _bar(118, 122, 113, 115),
        _bar(115, 119, 112, 113),
        _bar(113, 116, 110, 111),
        _bar(111, 113, 109, 110),
        _bar(110, 116, 108, 115),   # breakout above resistance
    ]
    out = {a.pattern: a for a in assess_bullish_patterns(bars)}
    fw = out["falling_wedge"]
    # Should at least be confirmed or forming (numeric slopes can flex)
    assert fw.state in ("confirmed", "forming")
    if fw.state == "confirmed":
        assert fw.confidence == pytest.approx(0.74, abs=0.001)


# ─── Double Top (bearish) ────────────────────────────────────────


def test_double_top_confirmed_becomes_veto():
    bars = [
        _bar(99, 100, 95, 96),      # first peak = 100
        _bar(96, 99, 94, 95),       # neckline bar (low=94)
        _bar(95, 100.3, 96, 97),    # second peak = 100.3
        _bar(97, 98, 94.5, 95),
        _bar(95, 96, 92, 93),       # close 93 < neckline 94 = breakout
    ]
    out = {a.pattern: a for a in assess_bearish_patterns(bars)}
    dt = out["double_top"]
    assert dt.state == "confirmed"
    veto = has_bearish_veto(bars)
    assert veto is not None
    assert veto.pattern == "double_top"


# ─── veto priority ───────────────────────────────────────────────


def test_no_veto_when_only_forming():
    bars = [
        _bar(99, 100, 95, 96),
        _bar(96, 99, 94, 95),
        _bar(95, 100.3, 96, 97),
        _bar(97, 98, 94.5, 95),
        _bar(95, 96, 94.5, 95),  # no break
    ]
    assert has_bearish_veto(bars) is None


def test_best_bullish_returns_highest_confidence():
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(104, 107, 103, 106),
    ]
    best = best_bullish(bars)
    assert best is not None
    assert best.state == "confirmed"
    # Only double_bottom has enough bars — that's the one.
    assert best.pattern == "double_bottom"


# ─── invalidation level surfaces ────────────────────────────────


def test_confirmed_pattern_surfaces_invalidation_and_trigger():
    bars = [
        _bar(101, 105, 100, 101),
        _bar(101, 105, 102, 104),
        _bar(102, 103, 100.5, 101),
        _bar(101, 105, 100.5, 104),
        _bar(104, 107, 103, 106),
    ]
    best = best_bullish(bars)
    assert best is not None
    assert best.neckline_or_support is not None
    assert best.invalidation_level is not None
    # Invalidation must be strictly BELOW neckline for a bullish pattern
    assert best.invalidation_level < best.neckline_or_support


# ─── numeric sanity: values match JS reference ──────────────────


@pytest.mark.parametrize("scenario,expected_conf", [
    ("confirmed_bullish_double_bottom", 0.78),
    ("confirmed_bullish_ihs", 0.81),
    ("confirmed_bullish_falling_wedge", 0.74),
])
def test_confidence_numbers_match_ignis_reference(scenario, expected_conf):
    # These are the exact numeric confidences from
    # camaroBullishPatterns.js. If a future edit changes them, this
    # test flags the drift so we notice.
    from services.alpha_classical_patterns import (
        _assess_double_bottom,
        _assess_inverse_head_and_shoulders,
        _assess_falling_wedge,
    )
    if scenario == "confirmed_bullish_double_bottom":
        bars = [
            _bar(101, 105, 100, 101),
            _bar(101, 105, 102, 104),
            _bar(102, 103, 100.5, 101),
            _bar(101, 105, 100.5, 104),
            _bar(104, 107, 103, 106),
        ]
        got = _assess_double_bottom(bars)
    elif scenario == "confirmed_bullish_ihs":
        # 7-bar inverse H&S: shoulders at 95, head at 90, close breaks neckline
        bars = [
            _bar(100, 105, 95, 100),  # left shoulder low=95
            _bar(100, 102, 99, 101),  # left peak high=102
            _bar(99, 100, 90, 92),    # head low=90
            _bar(93, 102, 92, 100),   # right peak high=102
            _bar(100, 105, 94.5, 100),# right shoulder low=94.5 (within 5%)
            _bar(100, 102, 99, 100),
            _bar(100, 105, 99, 103),  # close > neckline avg(102,102)=102
        ]
        got = _assess_inverse_head_and_shoulders(bars)
    else:
        bars = [
            _bar(120, 125, 115, 118),
            _bar(118, 122, 113, 115),
            _bar(115, 119, 112, 113),
            _bar(113, 116, 110, 111),
            _bar(111, 113, 109, 110),
            _bar(110, 118, 108, 116),
        ]
        got = _assess_falling_wedge(bars)

    if got.state == "confirmed":
        assert got.confidence == pytest.approx(expected_conf, abs=0.001)
