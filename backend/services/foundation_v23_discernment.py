"""Namespaced extraction of Foundation v2.3 directional discernment.

Use as an advisory/feature source beside Alpha's existing five-pattern engine.
Do NOT replace Alpha's production pattern matcher without A/B validation.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

BUY = "BUY"
SELL_SHORT = "SELL_SHORT"
HOLD = "HOLD"
ACTIONABLE = "ACTIONABLE"
WATCH = "WATCH"
REJECT = "REJECT"

@dataclass(frozen=True)
class DiscernmentResult:
    direction: str
    pattern: str | None
    score: float
    actionability: str
    reason: str


def detect_pattern(s: Any) -> tuple[str, str] | None:
    if getattr(s, "last_price", None) is None:
        return None
    m1 = float(getattr(s, "momentum_1m", 0.0) or 0.0)
    m5 = float(getattr(s, "momentum_5m", 0.0) or 0.0)
    m15 = float(getattr(s, "momentum_15m", 0.0) or 0.0)
    rv = getattr(s, "rvol", None)
    p = float(s.last_price)
    vwap = getattr(s, "vwap", None)
    hod = getattr(s, "high_of_day", None)
    lod = getattr(s, "low_of_day", None)
    closes = getattr(s, "closes", []) or []
    if vwap and p > vwap and m1 > 0 and m5 > 0 and rv is not None and rv >= 1.2: return BUY, "VWAP_RECLAIM"
    if hod and p >= hod * .998 and m1 > 0 and rv is not None and rv >= 1.5: return BUY, "HOD_BREAK"
    if m1 > m5 > 0 and rv is not None and rv >= 1.25: return BUY, "MOMENTUM_REACCEL"
    if m5 > 0 and m1 > 0 and len(closes) >= 4 and closes[-2] < closes[-1]: return BUY, "PULLBACK"
    if vwap and p < vwap and m1 < 0 and m5 < 0 and rv is not None and rv >= 1.2: return SELL_SHORT, "VWAP_REJECT"
    if lod and p <= lod * 1.002 and m1 < 0 and rv is not None and rv >= 1.5: return SELL_SHORT, "LOD_BREAK"
    if m1 < m5 < 0 and rv is not None and rv >= 1.25: return SELL_SHORT, "MOMENTUM_BREAKDOWN"
    if hod and p < hod * .99 and m15 > 0 and m5 < 0 and m1 < 0: return SELL_SHORT, "FAILED_BREAKOUT"
    return None


def evaluate(s: Any, edge: float = 0.0) -> DiscernmentResult:
    found = detect_pattern(s)
    if not found:
        return DiscernmentResult(HOLD, None, 0.0, REJECT, "no_actionable_pattern")
    direction, pattern = found
    sign = 1 if direction == BUY else -1
    m1 = float(getattr(s, "momentum_1m", 0.0) or 0.0)
    m5 = float(getattr(s, "momentum_5m", 0.0) or 0.0)
    m15 = float(getattr(s, "momentum_15m", 0.0) or 0.0)
    rv = getattr(s, "rvol", None)
    score = 0.0
    if rv is not None:
        score += .22 if rv >= 2 else .14 if rv >= 1.3 else 0.0
    score += .22 if sign*m1 > sign*m5 > 0 else .13 if sign*m1 > 0 and sign*m5 > 0 else 0.0
    if sign*m15 > 0: score += .10
    spread = getattr(s, "spread_bps", None)
    spread = spread() if callable(spread) else spread
    score += .02 if spread is None else .14 if spread <= 10 else .07 if spread <= 25 else 0.0
    score += max(-.20, min(float(edge or 0.0), .25))
    if sign*m15 > .025 and sign*m1 < sign*m5*.35: score -= .25
    score = max(0.0, min(1.0, score))
    actionability = ACTIONABLE if score >= .55 else WATCH if score >= .35 else REJECT
    reason = "actionable_edge" if actionability == ACTIONABLE else "near_ready_watch" if actionability == WATCH else "movement_not_actionable"
    if rv is None and pattern not in ("PULLBACK", "FAILED_BREAKOUT"):
        actionability = WATCH if score >= .25 else REJECT
        reason = "RVOL_UNAVAILABLE:warming_baseline"
    return DiscernmentResult(direction, pattern, score, actionability, reason)
