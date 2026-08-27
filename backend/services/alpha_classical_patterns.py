"""Classical chart patterns for Alpha (2026-02).

Ported from the IGNISpilot ``camaroBullishPatterns.js`` /
``camaroBearishPatterns.js`` reference implementation. All math is
side-effect free and works on a list of OHLC bar dicts:
    {"open": float, "high": float, "low": float, "close": float,
     "volume": float, "date": str}

Six patterns, three of each polarity:

Bullish (Alpha may act on ``confirmed``):
  * Double Bottom              (5 bars)
  * Inverse Head & Shoulders   (7 bars)
  * Falling Wedge              (6 bars)

Bearish (info-only — Public.com is cash-only, no short capability;
Alpha uses these to *veto* a bullish setup on the same symbol):
  * Double Top                 (5 bars)
  * Head & Shoulders           (7 bars)
  * Rising Wedge               (6 bars)

Each detector returns a ``PatternAssessment`` regardless of outcome so
the caller can log the ``blocked`` / ``forming`` states for later
model training. ``state`` values:

    * ``blocked``       — geometry ambiguous, don't act
    * ``forming``       — geometry present but breakout not confirmed
    * ``confirmed``     — full breakout on the latest bar (actionable)
    * ``invalidated``   — price violated the invalidation level

Attribution: geometry rules and confidence numbers derive from the
IGNISpilot handoff (2026-08). Numbers were preserved intact for
apples-to-apples comparison with that project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Literal, Optional

BULLISH_PATTERNS = ("double_bottom", "inverse_head_and_shoulders", "falling_wedge")
BEARISH_PATTERNS = ("double_top", "head_and_shoulders", "rising_wedge")

# Shared tolerances (mirror the JS module).
TROUGH_TOLERANCE = 0.03
PEAK_TOLERANCE = 0.03
SHOULDER_TOLERANCE = 0.05
INVALIDATION_BUFFER = 0.01

PatternState = Literal["blocked", "forming", "confirmed", "invalidated"]


@dataclass
class PatternAssessment:
    pattern: str
    state: PatternState
    confidence: float
    latest_close: float
    detail: str
    invalidation_level: Optional[float] = None
    neckline_or_support: Optional[float] = None
    criteria: List[dict] = field(default_factory=list)


# ─── helpers ─────────────────────────────────────────────────────


def _clamp_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))) * 100.0) / 100.0


def _is_finite_bar(bar: dict) -> bool:
    try:
        h = float(bar.get("high"))
        l = float(bar.get("low"))
        c = float(bar.get("close"))
    except (TypeError, ValueError):
        return False
    return h == h and l == l and c == c and h >= l  # NaN guards


def _blocked(pattern: str, latest_close: float, detail: str) -> PatternAssessment:
    return PatternAssessment(
        pattern=pattern,
        state="blocked",
        confidence=0.0,
        latest_close=latest_close,
        detail=detail,
        criteria=[{"label": "Evidence", "met": False, "detail": detail}],
    )


def _regression(points: List[tuple]) -> tuple:
    """Simple least-squares slope-and-eval-at-index over (index, value)."""
    n = len(points)
    avg_i = sum(p[0] for p in points) / n
    avg_v = sum(p[1] for p in points) / n
    denom = sum((p[0] - avg_i) ** 2 for p in points)
    if denom == 0:
        slope = 0.0
    else:
        slope = sum((p[0] - avg_i) * (p[1] - avg_v) for p in points) / denom
    return slope, avg_i, avg_v


def _line_at(idx: float, slope: float, avg_i: float, avg_v: float) -> float:
    return avg_v + slope * (idx - avg_i)


# ─── bullish patterns ────────────────────────────────────────────


def _assess_double_bottom(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 5:
        return _blocked(
            "double_bottom", latest_close,
            "At least five ordered bars are required to inspect two troughs and a neckline.",
        )
    window = bars[-5:]
    first_trough = window[0]
    neckline_bar = window[1]
    second_trough = window[2]
    lo1, lo2 = float(first_trough["low"]), float(second_trough["low"])
    trough_diff = abs(lo1 - lo2) / max(lo1, lo2)
    troughs_comparable = trough_diff <= TROUGH_TOLERANCE
    neckline = float(neckline_bar["high"])
    invalidation = min(lo1, lo2) * (1.0 - INVALIDATION_BUFFER)
    broke_neckline = latest_close > neckline
    invalidated = float(latest["low"]) <= invalidation or latest_close <= invalidation

    if invalidated:
        state: PatternState = "invalidated"
    elif troughs_comparable and broke_neckline:
        state = "confirmed"
    elif troughs_comparable:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.78 if broke_neckline else 0.52
    if not troughs_comparable:
        conf = 0.18
    return PatternAssessment(
        pattern="double_bottom",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bullish_detail("Double Bottom", state, "neckline"),
        neckline_or_support=neckline,
        invalidation_level=invalidation,
        criteria=[
            {"label": "Comparable troughs", "met": troughs_comparable,
             "detail": f"Trough separation {trough_diff * 100:.1f}% (max {TROUGH_TOLERANCE * 100:.0f}%)"},
            {"label": "Neckline breakout", "met": broke_neckline,
             "detail": f"Close {latest_close:.2f} vs neckline {neckline:.2f}"},
            {"label": "Invalidation", "met": invalidated,
             "detail": f"Invalidation below {invalidation:.2f}"},
        ],
    )


def _assess_inverse_head_and_shoulders(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 7:
        return _blocked(
            "inverse_head_and_shoulders", latest_close,
            "At least seven ordered bars are required to inspect inverse shoulders, head, and neckline.",
        )
    window = bars[-7:]
    left_shoulder, left_peak, head, right_peak, right_shoulder = window[0], window[1], window[2], window[3], window[4]
    neckline = (float(left_peak["high"]) + float(right_peak["high"])) / 2.0
    ls_lo = float(left_shoulder["low"])
    rs_lo = float(right_shoulder["low"])
    head_lo = float(head["low"])
    shoulder_diff = abs(ls_lo - rs_lo) / max(ls_lo, rs_lo)
    shoulders_comparable = shoulder_diff <= SHOULDER_TOLERANCE
    head_dominant = head_lo < ls_lo and head_lo < rs_lo
    invalidation = head_lo * (1.0 - INVALIDATION_BUFFER)
    broke_neckline = latest_close > neckline
    invalidated = float(latest["low"]) <= invalidation or latest_close <= invalidation
    geometry = shoulders_comparable and head_dominant

    if invalidated:
        state: PatternState = "invalidated"
    elif geometry and broke_neckline:
        state = "confirmed"
    elif geometry:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.81 if broke_neckline else 0.56
    if not geometry:
        conf = 0.17
    return PatternAssessment(
        pattern="inverse_head_and_shoulders",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bullish_detail("Inverse Head & Shoulders", state, "neckline"),
        neckline_or_support=neckline,
        invalidation_level=invalidation,
        criteria=[
            {"label": "Shoulder symmetry", "met": shoulders_comparable,
             "detail": f"Shoulder separation {shoulder_diff * 100:.1f}% (max {SHOULDER_TOLERANCE * 100:.0f}%)"},
            {"label": "Head dominance", "met": head_dominant,
             "detail": f"Head {head_lo:.2f} vs shoulders {ls_lo:.2f} / {rs_lo:.2f}"},
            {"label": "Neckline breakout", "met": broke_neckline,
             "detail": f"Close {latest_close:.2f} vs neckline {neckline:.2f}"},
        ],
    )


def _assess_falling_wedge(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 6:
        return _blocked(
            "falling_wedge", latest_close,
            "At least six ordered bars are required to inspect converging falling support and resistance.",
        )
    window = bars[-6:]
    highs = [(i, float(b["high"])) for i, b in enumerate(window)]
    lows = [(i, float(b["low"])) for i, b in enumerate(window)]
    r_slope, r_ai, r_av = _regression(highs)
    s_slope, s_ai, s_av = _regression(lows)
    start_width = _line_at(0, r_slope, r_ai, r_av) - _line_at(0, s_slope, s_ai, s_av)
    end_idx = len(window) - 1
    resistance_level = _line_at(end_idx, r_slope, r_ai, r_av)
    support_level = _line_at(end_idx, s_slope, s_ai, s_av)
    end_width = resistance_level - support_level
    falling = r_slope < 0 and s_slope < 0
    converging = end_width > 0 and end_width < start_width and r_slope < s_slope
    invalidation = support_level * (1.0 - INVALIDATION_BUFFER)
    broke_resistance = latest_close > resistance_level
    invalidated = float(latest["low"]) <= invalidation or latest_close <= invalidation
    geometry = falling and converging

    if invalidated:
        state: PatternState = "invalidated"
    elif geometry and broke_resistance:
        state = "confirmed"
    elif geometry:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.74 if broke_resistance else 0.49
    if not geometry:
        conf = 0.16
    return PatternAssessment(
        pattern="falling_wedge",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bullish_detail("Falling Wedge", state, "resistance"),
        neckline_or_support=resistance_level,
        invalidation_level=invalidation,
        criteria=[
            {"label": "Falling boundaries", "met": falling,
             "detail": f"Resistance slope {r_slope:.3f}, support slope {s_slope:.3f}"},
            {"label": "Convergence", "met": converging,
             "detail": f"Channel width {start_width:.2f} → {end_width:.2f}"},
            {"label": "Resistance breakout", "met": broke_resistance,
             "detail": f"Close {latest_close:.2f} vs resistance {resistance_level:.2f}"},
        ],
    )


# ─── bearish patterns (invalidation gates only) ──────────────────


def _assess_double_top(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 5:
        return _blocked(
            "double_top", latest_close,
            "At least five ordered bars are required to inspect two peaks and a neckline.",
        )
    window = bars[-5:]
    first_peak, neckline_bar, second_peak = window[0], window[1], window[2]
    hi1, hi2 = float(first_peak["high"]), float(second_peak["high"])
    peak_diff = abs(hi1 - hi2) / max(hi1, hi2)
    peaks_comparable = peak_diff <= PEAK_TOLERANCE
    neckline = float(neckline_bar["low"])
    invalidation = max(hi1, hi2) * (1.0 + INVALIDATION_BUFFER)
    broke_neckline = latest_close < neckline
    invalidated = float(latest["high"]) >= invalidation or latest_close >= invalidation

    if invalidated:
        state: PatternState = "invalidated"
    elif peaks_comparable and broke_neckline:
        state = "confirmed"
    elif peaks_comparable:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.78 if broke_neckline else 0.52
    if not peaks_comparable:
        conf = 0.18
    return PatternAssessment(
        pattern="double_top",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bearish_detail("Double Top", state, "neckline"),
        neckline_or_support=neckline,
        invalidation_level=invalidation,
    )


def _assess_head_and_shoulders(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 7:
        return _blocked(
            "head_and_shoulders", latest_close,
            "At least seven ordered bars are required to inspect shoulders, head, and neckline.",
        )
    window = bars[-7:]
    left_shoulder, left_trough, head, right_trough, right_shoulder = window[0], window[1], window[2], window[3], window[4]
    neckline = (float(left_trough["low"]) + float(right_trough["low"])) / 2.0
    ls_hi = float(left_shoulder["high"])
    rs_hi = float(right_shoulder["high"])
    head_hi = float(head["high"])
    shoulder_diff = abs(ls_hi - rs_hi) / max(ls_hi, rs_hi)
    shoulders_comparable = shoulder_diff <= SHOULDER_TOLERANCE
    head_dominant = head_hi > ls_hi and head_hi > rs_hi
    invalidation = head_hi * (1.0 + INVALIDATION_BUFFER)
    broke_neckline = latest_close < neckline
    invalidated = float(latest["high"]) >= invalidation or latest_close >= invalidation
    geometry = shoulders_comparable and head_dominant

    if invalidated:
        state: PatternState = "invalidated"
    elif geometry and broke_neckline:
        state = "confirmed"
    elif geometry:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.81 if broke_neckline else 0.56
    if not geometry:
        conf = 0.17
    return PatternAssessment(
        pattern="head_and_shoulders",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bearish_detail("Head & Shoulders", state, "neckline"),
        neckline_or_support=neckline,
        invalidation_level=invalidation,
    )


def _assess_rising_wedge(bars: List[dict]) -> PatternAssessment:
    latest = bars[-1]
    latest_close = float(latest["close"])
    if len(bars) < 6:
        return _blocked(
            "rising_wedge", latest_close,
            "At least six ordered bars are required to inspect converging rising support and resistance.",
        )
    window = bars[-6:]
    highs = [(i, float(b["high"])) for i, b in enumerate(window)]
    lows = [(i, float(b["low"])) for i, b in enumerate(window)]
    r_slope, r_ai, r_av = _regression(highs)
    s_slope, s_ai, s_av = _regression(lows)
    start_width = _line_at(0, r_slope, r_ai, r_av) - _line_at(0, s_slope, s_ai, s_av)
    end_idx = len(window) - 1
    resistance_level = _line_at(end_idx, r_slope, r_ai, r_av)
    support_level = _line_at(end_idx, s_slope, s_ai, s_av)
    end_width = resistance_level - support_level
    rising = r_slope > 0 and s_slope > 0
    converging = end_width > 0 and end_width < start_width and s_slope > r_slope
    invalidation = resistance_level * (1.0 + INVALIDATION_BUFFER)
    broke_support = latest_close < support_level
    invalidated = float(latest["high"]) >= invalidation or latest_close >= invalidation
    geometry = rising and converging

    if invalidated:
        state: PatternState = "invalidated"
    elif geometry and broke_support:
        state = "confirmed"
    elif geometry:
        state = "forming"
    else:
        state = "blocked"

    conf = 0.74 if broke_support else 0.49
    if not geometry:
        conf = 0.16
    return PatternAssessment(
        pattern="rising_wedge",
        state=state,
        confidence=_clamp_confidence(conf),
        latest_close=latest_close,
        detail=_bearish_detail("Rising Wedge", state, "support"),
        neckline_or_support=support_level,
        invalidation_level=invalidation,
    )


# ─── detail-string builders ──────────────────────────────────────


def _bullish_detail(name: str, state: str, level_name: str) -> str:
    if state == "confirmed":
        return f"{name} {level_name} breakout confirmed — bullish research context."
    if state == "invalidated":
        return f"{name} structure was invalidated; append-only research record."
    if state == "forming":
        return f"{name} geometry forming but not yet confirmed by a {level_name} break."
    return f"{name} evidence is ambiguous and was blocked."


def _bearish_detail(name: str, state: str, level_name: str) -> str:
    if state == "confirmed":
        return f"{name} {level_name} break confirmed — bearish research context (Alpha uses this as an invalidation gate for longs)."
    if state == "invalidated":
        return f"{name} structure was invalidated; append-only research record."
    if state == "forming":
        return f"{name} geometry forming but not yet confirmed by a {level_name} break."
    return f"{name} evidence is ambiguous and was blocked."


# ─── public entry points ─────────────────────────────────────────


def assess_bullish_patterns(bars: List[dict]) -> List[PatternAssessment]:
    """Return one assessment per bullish pattern (always 3 rows)."""
    if not bars:
        return []
    latest_close = float(bars[-1].get("close") or 0.0)
    if not all(_is_finite_bar(b) for b in bars):
        return [
            _blocked(p, latest_close, "Malformed OHLC input; no pattern conclusion recorded.")
            for p in BULLISH_PATTERNS
        ]
    return [
        _assess_double_bottom(bars),
        _assess_inverse_head_and_shoulders(bars),
        _assess_falling_wedge(bars),
    ]


def assess_bearish_patterns(bars: List[dict]) -> List[PatternAssessment]:
    """Return one assessment per bearish pattern (always 3 rows)."""
    if not bars:
        return []
    latest_close = float(bars[-1].get("close") or 0.0)
    if not all(_is_finite_bar(b) for b in bars):
        return [
            _blocked(p, latest_close, "Malformed OHLC input; no pattern conclusion recorded.")
            for p in BEARISH_PATTERNS
        ]
    return [
        _assess_double_top(bars),
        _assess_head_and_shoulders(bars),
        _assess_rising_wedge(bars),
    ]


def best_bullish(bars: List[dict]) -> Optional[PatternAssessment]:
    """Return the best (highest confidence) confirmed bullish pattern, or None."""
    confirmed = [a for a in assess_bullish_patterns(bars) if a.state == "confirmed"]
    if not confirmed:
        return None
    return max(confirmed, key=lambda a: a.confidence)


def has_bearish_veto(bars: List[dict]) -> Optional[PatternAssessment]:
    """Return the highest-confidence confirmed bearish pattern (a *veto* on longs)
    on this symbol, or None. Alpha uses this to skip a bullish setup when a
    confirmed bearish pattern is active on the same symbol.
    """
    confirmed = [a for a in assess_bearish_patterns(bars) if a.state == "confirmed"]
    if not confirmed:
        return None
    return max(confirmed, key=lambda a: a.confidence)


__all__ = [
    "PatternAssessment",
    "PatternState",
    "BULLISH_PATTERNS",
    "BEARISH_PATTERNS",
    "assess_bullish_patterns",
    "assess_bearish_patterns",
    "best_bullish",
    "has_bearish_veto",
]
