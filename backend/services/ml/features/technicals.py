"""Universal technicals feature builder (equity + crypto).

Wraps the existing :func:`services.universe_technicals.compute_technicals`
helper (RSI14, MACD, SMA20/50/200) in a feature-shaped output:
raw indicator values plus a small set of binary flags
(``price_above_sma20``, ``rsi_oversold``, ``macd_bullish``, ...).

The flags are FEATURES for the ML to learn from — *never*
hardcoded ``if`` rules in execution authority. Whether the
Strategist treats ``rsi_oversold`` as a real signal in any given
regime is for the model to learn from history, not for this
module to assume.

Hard rules
----------
* Equity AND crypto supported. Bars come from the price provider.
* NEVER raises. Missing bars / unreachable provider → ``{}``.
* NEVER places an order, calls a broker, or branches on the
  output to issue a verdict.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _gt(a: Optional[float], b: Optional[float]) -> int:
    """Strict-gt as an int (1 / 0). ``None`` propagates to 0 — i.e.
    "we don't have evidence for the bullish side"."""
    if a is None or b is None:
        return 0
    return 1 if a > b else 0


def _shape_features(tech: Dict[str, Any]) -> Dict[str, Any]:
    """Pure shape transformation. Adds binary flags on top of the
    raw indicator values returned by ``compute_technicals``."""
    last_close = tech.get("last_close")
    sma20 = tech.get("sma20")
    sma50 = tech.get("sma50")
    sma200 = tech.get("sma200")
    rsi14 = tech.get("rsi14")
    macd_hist = tech.get("macd_hist")

    out: Dict[str, Any] = {
        # Raw indicator values pass through unchanged.
        "last_close": last_close,
        "rsi14": rsi14,
        "macd": tech.get("macd"),
        "macd_signal": tech.get("macd_signal"),
        "macd_hist": macd_hist,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "bar_count": tech.get("bar_count"),
        # ── Binary band flags (features, NOT rules) ──
        "price_above_sma20": _gt(last_close, sma20),
        "price_above_sma50": _gt(last_close, sma50),
        "price_above_sma200": _gt(last_close, sma200),
        # Golden / death cross approximations on the daily frame.
        "sma20_above_sma50": _gt(sma20, sma50),
        "sma50_above_sma200": _gt(sma50, sma200),
        # RSI bands — classic 30 / 70 cuts. Strategist learns
        # whether to trust them per regime; we never branch on them.
        "rsi_oversold": 1 if (rsi14 is not None and rsi14 < 30) else 0,
        "rsi_overbought": 1 if (rsi14 is not None and rsi14 > 70) else 0,
        "rsi_neutral": (
            1 if (rsi14 is not None and 30 <= rsi14 <= 70) else 0
        ),
        # MACD histogram sign — momentum direction proxy.
        "macd_bullish": 1 if (macd_hist is not None and macd_hist > 0) else 0,
        "macd_bearish": 1 if (macd_hist is not None and macd_hist < 0) else 0,
    }
    return out


async def build_technicals_features(
    symbol: str,
    lane: str,
) -> Dict[str, Any]:
    """Build the technicals feature dict for ``symbol``.

    Args:
        symbol: ticker (case-insensitive).
        lane: ``"equity"`` or ``"crypto"`` — both supported.
            Anything else returns ``{}``.

    Returns:
        Flat dict of indicator values + binary band flags.
        Empty dict when bars are unavailable / too short / lane
        firewall trips.
    """
    if not symbol:
        return {}
    lane_norm = (lane or "").strip().lower()
    if lane_norm not in ("equity", "crypto"):
        return {}

    try:
        from services.price_provider import get_daily_history
        from services.universe_technicals import compute_technicals

        bars = await get_daily_history(symbol.upper(), "compact")
        if not bars:
            return {}
        tech = compute_technicals(bars)
        if not tech:
            return {}
        return _shape_features(tech)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[features.technicals] fetch failed for %s: %s", symbol, exc,
        )
        return {}
