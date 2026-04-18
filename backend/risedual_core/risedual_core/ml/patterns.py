"""Technical pattern detectors for the Phase 2 ML pipeline.

All detectors are **pure functions** that accept an OHLCV
:class:`pandas.DataFrame` and return a :class:`~risedual_core.schemas.market.PatternResult`.
No I/O, no async, no side effects.  Callers that need to run all detectors
concurrently should use :func:`detect_all_patterns`, which offloads the
CPU-bound work to a thread via ``asyncio.to_thread``.

OHLCV DataFrame contract
------------------------
Each function expects a DataFrame with at minimum these columns (case-insensitive
names are *not* normalised — pass them correctly):

    open, high, low, close, volume

Rows are in **chronological order** (oldest first), and the **last row is the
most-recent bar**.  All functions require at least 20 bars; fewer bars will
cause the detector to return ``detected=False`` rather than raising.

Pattern confidence scores
-------------------------
Confidence values are **heuristic** and reflect how cleanly the pattern
geometry meets its criteria, not a trained probability.  They are scaled to
[0.0, 1.0] and are always 0.0 when ``detected=False``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING

from risedual_core.schemas.market import FeaturesSnapshot, PatternResult

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MIN_BARS: int = 20
"""Minimum number of OHLCV bars required for any detector to run."""

_PATTERN_NAMES: tuple[str, ...] = (
    "double_bottom",
    "bullish_engulfing",
    "bearish_engulfing",
    "bull_flag",
    "rsi_divergence",
    "macd_crossover",
    "volume_surge",
    "head_and_shoulders",
)


# ── Helper utilities ──────────────────────────────────────────────────────────


def _not_detected(name: str) -> PatternResult:
    """Return a canonical 'not detected' result for *name*."""
    return PatternResult(name=name, detected=False, confidence=0.0, bar_index=-1, description="")


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _compute_rsi(close: "pd.Series", period: int = 14) -> "pd.Series":
    """Compute RSI without external TA libraries."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _compute_macd(
    close: "pd.Series",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple["pd.Series", "pd.Series"]:
    """Return (macd_line, signal_line)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _compute_atr(df: "pd.DataFrame", period: int = 14) -> "pd.Series":
    """Compute Average True Range."""
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = (
        (high - low)
        .combine(abs(high - close_prev), max)
        .combine(abs(low - close_prev), max)
    )
    return tr.ewm(com=period - 1, min_periods=period).mean()


# ── Detector 1 — Double Bottom ────────────────────────────────────────────────


def detect_double_bottom(df: "pd.DataFrame") -> PatternResult:
    """Detect a double-bottom reversal pattern.

    Algorithm
    ---------
    1. Take the last 40 bars (or fewer if unavailable, minimum 20).
    2. Find all local minima: bars where ``low[i] < low[i-1]`` and
       ``low[i] < low[i+1]``.
    3. For each pair of minima separated by at least 5 bars, check if:
       a. Their lows are within 3% of each other.
       b. The highest ``close`` between the two minima (the «neckline peak»)
          is at least 2% above both lows.
       c. The most-recent close has recovered above the neckline peak.
    4. The strongest (most-recent qualifying) pair is reported.

    Confidence
    ----------
    ``min(low_separation_score, neckline_breakout_score)`` where each score
    is the ratio of the observed value to the threshold (capped at 1.0).
    """
    name = "double_bottom"
    if len(df) < _MIN_BARS:
        return _not_detected(name)

    window = df.tail(40).reset_index(drop=True)
    lows = window["low"]
    closes = window["close"]

    # Find local minima
    minima: list[int] = []
    for i in range(1, len(lows) - 1):
        if lows.iloc[i] < lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1]:
            minima.append(i)

    best: PatternResult | None = None
    for a_idx in range(len(minima)):
        for b_idx in range(a_idx + 1, len(minima)):
            i, j = minima[a_idx], minima[b_idx]
            if j - i < 5:
                continue

            low_a = lows.iloc[i]
            low_b = lows.iloc[j]
            if low_a == 0 or low_b == 0:
                continue

            # ── a. proximity of the two lows ──────────────────────────────────
            separation_pct = abs(low_a - low_b) / ((low_a + low_b) / 2)
            if separation_pct > 0.03:
                continue

            # ── b. neckline peak ──────────────────────────────────────────────
            neckline = float(closes.iloc[i:j].max())
            avg_low = (low_a + low_b) / 2
            if avg_low == 0:
                continue
            peak_rise_pct = (neckline - avg_low) / avg_low
            if peak_rise_pct < 0.02:
                continue

            # ── c. current close above neckline (breakout confirmed) ──────────
            current_close = float(closes.iloc[-1])
            if current_close < neckline:
                continue

            confidence = _clamp(
                min(
                    (0.03 - separation_pct) / 0.03,
                    (current_close - neckline) / (neckline * 0.01),
                )
            )
            desc = (
                f"Double bottom at ${avg_low:.2f}, "
                f"{separation_pct * 100:.1f}% low separation, "
                f"neckline ${neckline:.2f} broken"
            )
            result = PatternResult(
                name=name,
                detected=True,
                confidence=confidence,
                bar_index=int(j),
                description=desc,
            )
            # Keep the most-recent (highest j) qualifying pattern
            if best is None or j > best.bar_index:
                best = result

    return best if best is not None else _not_detected(name)


# ── Detector 2 — Bullish Engulfing ───────────────────────────────────────────


def detect_bullish_engulfing(df: "pd.DataFrame") -> PatternResult:
    """Detect a bullish engulfing candlestick pattern on the last two bars.

    Criteria
    --------
    - Prior bar is bearish: ``close[n-1] < open[n-1]``.
    - Current bar is bullish: ``close[n] > open[n]``.
    - Current bar's body fully engulfs the prior bar's body:
      ``open[n] <= close[n-1]`` and ``close[n] >= open[n-1]``.

    Confidence
    ----------
    Ratio of the current body to the prior body (capped at 1.0), reflecting
    how decisively the candle engulfs its predecessor.
    """
    name = "bullish_engulfing"
    if len(df) < 2:
        return _not_detected(name)

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_open, prev_close = float(prev["open"]), float(prev["close"])
    curr_open, curr_close = float(curr["open"]), float(curr["close"])

    # Prior bar must be bearish
    if prev_close >= prev_open:
        return _not_detected(name)
    # Current bar must be bullish
    if curr_close <= curr_open:
        return _not_detected(name)
    # Current body must engulf prior body
    if curr_open > prev_close or curr_close < prev_open:
        return _not_detected(name)

    prior_body = abs(prev_open - prev_close)
    curr_body = abs(curr_close - curr_open)
    confidence = _clamp(curr_body / prior_body if prior_body > 0 else 1.0)

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=len(df) - 1,
        description=(
            f"Bullish engulfing: current body ${curr_body:.2f} "
            f"engulfs prior body ${prior_body:.2f} "
            f"({confidence * 100:.0f}% size ratio)"
        ),
    )


# ── Detector 3 — Bearish Engulfing ───────────────────────────────────────────


def detect_bearish_engulfing(df: "pd.DataFrame") -> PatternResult:
    """Detect a bearish engulfing candlestick pattern on the last two bars.

    Inverse of :func:`detect_bullish_engulfing`.
    """
    name = "bearish_engulfing"
    if len(df) < 2:
        return _not_detected(name)

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_open, prev_close = float(prev["open"]), float(prev["close"])
    curr_open, curr_close = float(curr["open"]), float(curr["close"])

    # Prior bar must be bullish
    if prev_close <= prev_open:
        return _not_detected(name)
    # Current bar must be bearish
    if curr_close >= curr_open:
        return _not_detected(name)
    # Current body must engulf prior body
    if curr_open < prev_close or curr_close > prev_open:
        return _not_detected(name)

    prior_body = abs(prev_open - prev_close)
    curr_body = abs(curr_open - curr_close)
    confidence = _clamp(curr_body / prior_body if prior_body > 0 else 1.0)

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=len(df) - 1,
        description=(
            f"Bearish engulfing: current body ${curr_body:.2f} "
            f"engulfs prior body ${prior_body:.2f} "
            f"({confidence * 100:.0f}% size ratio)"
        ),
    )


# ── Detector 4 — Bull Flag ────────────────────────────────────────────────────


def detect_bull_flag(df: "pd.DataFrame") -> PatternResult:
    """Detect a bull-flag continuation pattern.

    Algorithm
    ---------
    1. **Pole**: The 5 bars ending 10 bars ago must show a cumulative return
       of at least +5% (strong upward move).
    2. **Flag (consolidation)**: The last 10 bars must have an ATR / close
       ratio < 0.012 (tight range) and a net price change < ±2%
       (directionless drift).

    Confidence
    ----------
    Average of the pole strength score and the tightness score.
    """
    name = "bull_flag"
    if len(df) < _MIN_BARS:
        return _not_detected(name)

    closes = df["close"]
    n = len(closes)

    # Pole: bars [n-15 .. n-10]
    pole_start = closes.iloc[max(0, n - 15)]
    pole_end = closes.iloc[n - 10]
    if pole_start == 0:
        return _not_detected(name)
    pole_return = (pole_end - pole_start) / pole_start
    if pole_return < 0.05:
        return _not_detected(name)

    # Flag: last 10 bars
    flag_bars = df.tail(10)
    flag_close_start = float(flag_bars["close"].iloc[0])
    flag_close_end = float(flag_bars["close"].iloc[-1])
    if flag_close_start == 0:
        return _not_detected(name)
    flag_drift = abs((flag_close_end - flag_close_start) / flag_close_start)
    if flag_drift > 0.02:
        return _not_detected(name)

    atr_series = _compute_atr(flag_bars)
    mean_atr = float(atr_series.dropna().mean()) if not atr_series.dropna().empty else 0.0
    mid_price = float(flag_bars["close"].mean())
    if mid_price == 0:
        return _not_detected(name)
    atr_ratio = mean_atr / mid_price
    if atr_ratio > 0.012:
        return _not_detected(name)

    pole_score = _clamp((pole_return - 0.05) / 0.05)       # 5-10% maps to 0-1
    tight_score = _clamp(1 - (atr_ratio / 0.012))           # lower ATR ratio = higher score
    confidence = (pole_score + tight_score) / 2

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=n - 1,
        description=(
            f"Bull flag: pole +{pole_return * 100:.1f}% over 5 bars, "
            f"flag consolidation ATR/price {atr_ratio * 100:.2f}%"
        ),
    )


# ── Detector 5 — RSI Divergence ───────────────────────────────────────────────


def detect_rsi_divergence(df: "pd.DataFrame") -> PatternResult:
    """Detect bullish RSI divergence: price lower-low but RSI higher-low.

    Algorithm
    ---------
    1. Compute 14-period RSI on the full series.
    2. Look at the last 20 bars.
    3. Find the two lowest ``close`` lows in that window.
    4. Bullish divergence: the more-recent low has a *lower* close than the
       prior low (price is falling) **but** a *higher* RSI (momentum is
       recovering).

    Confidence
    ----------
    Scaled by the magnitude of the RSI divergence (RSI difference / 10,
    capped at 1.0).
    """
    name = "rsi_divergence"
    if len(df) < 30:
        return _not_detected(name)

    closes = df["close"]
    rsi = _compute_rsi(closes)
    window = 20
    recent_closes = closes.tail(window).reset_index(drop=True)
    recent_rsi = rsi.tail(window).reset_index(drop=True)

    # Find two local close lows
    local_lows: list[int] = []
    for i in range(1, len(recent_closes) - 1):
        if recent_closes.iloc[i] < recent_closes.iloc[i - 1] and recent_closes.iloc[i] < recent_closes.iloc[i + 1]:
            local_lows.append(i)

    if len(local_lows) < 2:
        return _not_detected(name)

    # Use the two most-recent local lows
    i1, i2 = local_lows[-2], local_lows[-1]
    close1, close2 = float(recent_closes.iloc[i1]), float(recent_closes.iloc[i2])
    rsi1, rsi2 = float(recent_rsi.iloc[i1]), float(recent_rsi.iloc[i2])

    if math.isnan(rsi1) or math.isnan(rsi2):
        return _not_detected(name)

    # Bullish divergence: price made a lower low but RSI made a higher low
    if not (close2 < close1 and rsi2 > rsi1):
        return _not_detected(name)

    rsi_diff = rsi2 - rsi1
    confidence = _clamp(rsi_diff / 10.0)

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=len(df) - window + i2,
        description=(
            f"Bullish RSI divergence: price ${close1:.2f}→${close2:.2f} "
            f"(lower low), RSI {rsi1:.1f}→{rsi2:.1f} (higher low, +{rsi_diff:.1f})"
        ),
    )


# ── Detector 6 — MACD Crossover ──────────────────────────────────────────────


def detect_macd_crossover(df: "pd.DataFrame") -> PatternResult:
    """Detect a bullish MACD crossover: MACD line crosses above signal line.

    Criteria
    --------
    - On the previous bar: ``macd[n-1] < signal[n-1]`` (MACD below signal).
    - On the current bar: ``macd[n] > signal[n]`` (MACD crossed above).

    Confidence
    ----------
    Magnitude of the crossover gap relative to a 20-bar average of the
    absolute MACD–signal difference, capped at 1.0.
    """
    name = "macd_crossover"
    if len(df) < 35:
        return _not_detected(name)

    macd_line, signal_line = _compute_macd(df["close"])
    diff = macd_line - signal_line

    if len(diff) < 2 or diff.iloc[-1] is None or diff.iloc[-2] is None:
        return _not_detected(name)

    prev_diff = float(diff.iloc[-2])
    curr_diff = float(diff.iloc[-1])

    if math.isnan(prev_diff) or math.isnan(curr_diff):
        return _not_detected(name)

    # Bullish crossover: was negative, now positive
    if not (prev_diff < 0 < curr_diff):
        return _not_detected(name)

    avg_abs_diff = float(diff.abs().tail(20).mean())
    if avg_abs_diff == 0:
        return _not_detected(name)

    confidence = _clamp(curr_diff / avg_abs_diff)

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=len(df) - 1,
        description=(
            f"Bullish MACD crossover: diff {prev_diff:.4f} → {curr_diff:.4f} "
            f"(confidence {confidence:.2f})"
        ),
    )


# ── Detector 7 — Volume Surge ─────────────────────────────────────────────────


def detect_volume_surge(df: "pd.DataFrame") -> PatternResult:
    """Detect a volume surge: current volume exceeds 2× the 20-bar average.

    Confidence
    ----------
    ``(current_volume / avg_volume - 2) / 3`` — each additional multiple
    above 2× adds 0.33 confidence, capped at 1.0 (i.e. 5× average = 1.0).
    """
    name = "volume_surge"
    if len(df) < _MIN_BARS:
        return _not_detected(name)

    volumes = df["volume"]
    current_vol = float(volumes.iloc[-1])
    avg_vol = float(volumes.tail(21).iloc[:-1].mean())  # 20-bar average (excluding current)

    if avg_vol == 0 or math.isnan(avg_vol) or math.isnan(current_vol):
        return _not_detected(name)

    ratio = current_vol / avg_vol
    if ratio < 2.0:
        return _not_detected(name)

    confidence = _clamp((ratio - 2.0) / 3.0)

    return PatternResult(
        name=name,
        detected=True,
        confidence=confidence,
        bar_index=len(df) - 1,
        description=(
            f"Volume surge: {ratio:.1f}× the 20-bar average "
            f"({current_vol:,.0f} vs {avg_vol:,.0f})"
        ),
    )


# ── Detector 8 — Head and Shoulders ──────────────────────────────────────────


def detect_head_and_shoulders(df: "pd.DataFrame") -> PatternResult:
    """Detect a head-and-shoulders (bearish reversal) top pattern.

    Algorithm
    ---------
    1. Take the last 40 bars.
    2. Find all local highs: ``high[i] > high[i-1]`` and ``high[i] > high[i+1]``.
    3. For each triple of local highs (left shoulder, head, right shoulder):
       a. Head must be higher than both shoulders.
       b. Shoulders should be within 5% of each other in height.
       c. Head must be at least 3% above the average shoulder height.
       d. The neckline (average of the two troughs between the peaks) must
          have been violated by the most-recent close.

    Confidence
    ----------
    ``head_dominance * shoulder_symmetry`` where head_dominance is the
    fractional excess of the head over the shoulders, and shoulder_symmetry
    is ``1 - asymmetry_pct``.
    """
    name = "head_and_shoulders"
    if len(df) < _MIN_BARS:
        return _not_detected(name)

    window = df.tail(40).reset_index(drop=True)
    highs = window["high"]
    lows = window["low"]
    closes = window["close"]

    # Find local highs
    peaks: list[int] = []
    for i in range(1, len(highs) - 1):
        if highs.iloc[i] > highs.iloc[i - 1] and highs.iloc[i] > highs.iloc[i + 1]:
            peaks.append(i)

    if len(peaks) < 3:
        return _not_detected(name)

    best: PatternResult | None = None

    # Evaluate all consecutive peak triples
    for k in range(len(peaks) - 2):
        ls_idx, hd_idx, rs_idx = peaks[k], peaks[k + 1], peaks[k + 2]
        ls_h = float(highs.iloc[ls_idx])
        hd_h = float(highs.iloc[hd_idx])
        rs_h = float(highs.iloc[rs_idx])

        # Head must be higher than both shoulders
        if not (hd_h > ls_h and hd_h > rs_h):
            continue

        avg_shoulder = (ls_h + rs_h) / 2
        if avg_shoulder == 0:
            continue

        # Head dominance
        head_excess_pct = (hd_h - avg_shoulder) / avg_shoulder
        if head_excess_pct < 0.03:
            continue

        # Shoulder symmetry: within 5%
        asymmetry = abs(ls_h - rs_h) / avg_shoulder
        if asymmetry > 0.05:
            continue

        # Neckline: average of troughs between peaks
        trough1 = float(lows.iloc[ls_idx:hd_idx].min())
        trough2 = float(lows.iloc[hd_idx:rs_idx].min())
        neckline = (trough1 + trough2) / 2

        # Pattern confirmed when price breaks below neckline
        current_close = float(closes.iloc[-1])
        if current_close >= neckline:
            continue

        confidence = _clamp(
            min((head_excess_pct - 0.03) / 0.03, 1 - asymmetry / 0.05)
        )
        desc = (
            f"Head & shoulders top: H=${hd_h:.2f}, shoulders avg=${avg_shoulder:.2f}, "
            f"neckline ${neckline:.2f} broken at ${current_close:.2f}"
        )
        result = PatternResult(
            name=name,
            detected=True,
            confidence=confidence,
            bar_index=int(rs_idx),
            description=desc,
        )
        if best is None or rs_idx > best.bar_index:
            best = result

    return best if best is not None else _not_detected(name)


# ── Runner ────────────────────────────────────────────────────────────────────

_DETECTORS = {
    "double_bottom":       detect_double_bottom,
    "bullish_engulfing":   detect_bullish_engulfing,
    "bearish_engulfing":   detect_bearish_engulfing,
    "bull_flag":           detect_bull_flag,
    "rsi_divergence":      detect_rsi_divergence,
    "macd_crossover":      detect_macd_crossover,
    "volume_surge":        detect_volume_surge,
    "head_and_shoulders":  detect_head_and_shoulders,
}


def _run_all_sync(df: "pd.DataFrame") -> dict[str, PatternResult]:
    """Run all detectors synchronously. Called via ``asyncio.to_thread``."""
    results: dict[str, PatternResult] = {}
    for pattern_name, fn in _DETECTORS.items():
        try:
            results[pattern_name] = fn(df)
        except Exception:
            logger.exception("Pattern detector '%s' failed — returning not-detected.", pattern_name)
            results[pattern_name] = _not_detected(pattern_name)
    return results


async def detect_all_patterns(ohlcv_df: "pd.DataFrame") -> dict[str, PatternResult]:
    """Run all 8 pattern detectors on *ohlcv_df* in a thread pool.

    Uses ``asyncio.to_thread`` to avoid blocking the event loop while pandas
    computations run.  Individual detector failures are caught and logged;
    they return a not-detected result rather than raising.

    Parameters
    ----------
    ohlcv_df:
        OHLCV DataFrame with columns ``open``, ``high``, ``low``, ``close``,
        ``volume`` in chronological order (oldest first).  A minimum of 20
        rows is required; fewer rows will cause all detectors to return
        ``detected=False``.

    Returns
    -------
    dict[str, PatternResult]
        Mapping of pattern name → :class:`~risedual_core.schemas.market.PatternResult`
        for all 8 patterns.  Always returns all 8 keys.
    """
    return await asyncio.to_thread(_run_all_sync, ohlcv_df)


def apply_patterns_to_snapshot(
    snapshot: FeaturesSnapshot,
    patterns: dict[str, PatternResult],
) -> FeaturesSnapshot:
    """Return a copy of *snapshot* with pattern boolean fields populated.

    Parameters
    ----------
    snapshot:
        The original :class:`~risedual_core.schemas.market.FeaturesSnapshot`.
    patterns:
        Output of :func:`detect_all_patterns`.

    Returns
    -------
    FeaturesSnapshot
        A new snapshot with all ``pattern_*`` boolean fields set based on
        the ``detected`` attribute of each :class:`~risedual_core.schemas.market.PatternResult`.
    """
    return snapshot.model_copy(
        update={
            "pattern_double_bottom":      patterns.get("double_bottom",      _not_detected("double_bottom")).detected,
            "pattern_bullish_engulfing":  patterns.get("bullish_engulfing",  _not_detected("bullish_engulfing")).detected,
            "pattern_bearish_engulfing":  patterns.get("bearish_engulfing",  _not_detected("bearish_engulfing")).detected,
            "pattern_bull_flag":          patterns.get("bull_flag",           _not_detected("bull_flag")).detected,
            "pattern_rsi_divergence":     patterns.get("rsi_divergence",     _not_detected("rsi_divergence")).detected,
            "pattern_macd_crossover":     patterns.get("macd_crossover",     _not_detected("macd_crossover")).detected,
            "pattern_volume_surge":       patterns.get("volume_surge",       _not_detected("volume_surge")).detected,
            "pattern_head_and_shoulders": patterns.get("head_and_shoulders", _not_detected("head_and_shoulders")).detected,
        }
    )
