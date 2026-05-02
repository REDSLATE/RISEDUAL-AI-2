"""Local technical-indicator computation for the pre-warmed top universe.

All indicators are derived from daily OHLCV bars already cached by
`price_provider.get_daily_history`. Running them here (pandas, in-process)
avoids spending Alpha Vantage calls on `RSI` / `MACD` / `SMA` endpoints —
which charge a full request per indicator per symbol.

Exposed surface
---------------
``compute_technicals(bars) -> dict``
    Returns a flat dict with the latest values the ranker + UI consume:
    rsi14, macd, macd_signal, macd_hist, sma20, sma50, sma200, last_close,
    bar_count. Missing-data rows return ``{}``.

Design notes
------------
* Bars are expected in the shape ``price_provider`` returns — newest first,
  list of dicts with ``close`` (float). We normalise to a numeric
  oldest-first pandas Series internally; callers don't need to reorder.
* No dependency on ``ta-lib`` — the three indicators here are short enough
  to implement with pure pandas/numpy and it keeps the dependency footprint
  unchanged.
* If we ever need a full indicator row history (for charting), extend with
  a ``compute_series`` variant instead of widening the return shape here.
"""
from __future__ import annotations

from typing import Any


def _to_close_series(bars: list[dict]) -> "Any":
    import pandas as pd

    if not bars:
        return pd.Series(dtype=float)
    # `price_provider` returns newest-first — reverse for indicator math.
    closes = [float(b["close"]) for b in reversed(bars) if b.get("close") is not None]
    return pd.Series(closes, dtype=float)


def _rsi(series: "Any", period: int = 14) -> float | None:
    """Wilder's RSI on closes. Returns None when insufficient history."""
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    # Wilder-smoothed means (EWM with alpha=1/period, adjust=False replicates
    # the recursive definition used by TA-Lib / most reference implementations).
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0.0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _macd(series: "Any", fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float | None, float | None, float | None]:
    """Return (macd, signal, histogram) on the last bar, or a triple of Nones."""
    if len(series) < slow + signal:
        return None, None, None
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return (
        round(float(macd_line.iloc[-1]), 4),
        round(float(signal_line.iloc[-1]), 4),
        round(float(hist.iloc[-1]), 4),
    )


def _sma(series: "Any", period: int) -> float | None:
    if len(series) < period:
        return None
    return round(float(series.tail(period).mean()), 4)


def compute_technicals(bars: list[dict]) -> dict:
    """Compute RSI(14), MACD(12,26,9), and SMA(20/50/200) from daily bars.

    Returns an empty dict if `bars` is too short for any indicator. Every
    key is guaranteed present — unavailable values surface as ``None`` so
    downstream consumers can render "—" without KeyError branches.
    """
    if not bars:
        return {}
    closes = _to_close_series(bars)
    if len(closes) < 15:  # below the minimum for RSI14 — nothing meaningful
        return {}

    macd, macd_signal, macd_hist = _macd(closes)
    return {
        "last_close": round(float(closes.iloc[-1]), 4),
        "bar_count": int(len(closes)),
        "rsi14": _rsi(closes, 14),
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "sma20": _sma(closes, 20),
        "sma50": _sma(closes, 50),
        "sma200": _sma(closes, 200),
    }
