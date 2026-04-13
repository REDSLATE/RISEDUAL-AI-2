"""Market Scanner Service — Pre-built screening strategies using technical indicators.

Scans watchlist + popular tickers and returns matches for each strategy.
Reuses existing technical indicator functions from ai_intelligence_service.
"""
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services.price_provider import get_quote, get_daily_history, get_crypto_quote
from services.ai_intelligence_service import (
    _fetch_daily, _compute_technicals, _calc_rsi, _calc_bollinger, _ema, _safe_sma
)

logger = logging.getLogger(__name__)

_db = None

def set_db(database):
    global _db
    _db = database


POPULAR_TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "NFLX", "DIS", "BA", "JPM", "V", "UNH", "XOM", "COST", "HD",
]

CRYPTO_TICKERS = ["BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "BNB", "LINK", "DOT"]


# ── Strategy Definitions ──

STRATEGIES = {
    "rsi_oversold": {
        "name": "RSI Oversold",
        "description": "RSI below 30 — potential bounce setup",
        "category": "mean_reversion",
        "signal": "bullish",
    },
    "rsi_overbought": {
        "name": "RSI Overbought",
        "description": "RSI above 70 — potential pullback setup",
        "category": "mean_reversion",
        "signal": "bearish",
    },
    "macd_bullish_cross": {
        "name": "MACD Bullish Cross",
        "description": "MACD line crosses above signal line",
        "category": "momentum",
        "signal": "bullish",
    },
    "macd_bearish_cross": {
        "name": "MACD Bearish Cross",
        "description": "MACD line crosses below signal line",
        "category": "momentum",
        "signal": "bearish",
    },
    "bollinger_squeeze": {
        "name": "Bollinger Squeeze",
        "description": "Bandwidth narrowing — volatility expansion imminent",
        "category": "volatility",
        "signal": "neutral",
    },
    "ema_golden_cross": {
        "name": "EMA 9/21 Golden Cross",
        "description": "Short-term EMA crosses above long-term — bullish momentum",
        "category": "trend",
        "signal": "bullish",
    },
    "volume_spike": {
        "name": "Volume Spike",
        "description": "Volume 2x+ above 20-day average — unusual activity",
        "category": "volume",
        "signal": "neutral",
    },
    "near_52w_high": {
        "name": "Near 52-Week High",
        "description": "Price within 3% of 52-week high — breakout potential",
        "category": "trend",
        "signal": "bullish",
    },
    "near_52w_low": {
        "name": "Near 52-Week Low",
        "description": "Price within 5% of 52-week low — value or falling knife",
        "category": "mean_reversion",
        "signal": "bearish",
    },
    "momentum_breakout": {
        "name": "Momentum Breakout",
        "description": "Price above SMA 20 with expanding volume and RSI > 55",
        "category": "momentum",
        "signal": "bullish",
    },
}


def _calc_macd_full(closes):
    """Calculate MACD line, signal line, and histogram."""
    if len(closes) < 26:
        return 0, 0, 0
    macd_line = _ema(closes, 12) - _ema(closes, 26)

    # Signal line = 9-period EMA of MACD
    # We approximate by computing MACD for the last 9+ points
    macd_series = []
    for i in range(min(35, len(closes)), len(closes) + 1):
        sub = closes[:i]
        if len(sub) >= 26:
            macd_series.append(_ema(sub, 12) - _ema(sub, 26))
    signal = _ema(np.array(macd_series), 9) if len(macd_series) >= 9 else macd_line
    histogram = macd_line - signal
    return macd_line, signal, histogram


def _calc_prev_macd(closes):
    """Get MACD values for the previous day (for crossover detection)."""
    if len(closes) < 27:
        return 0, 0
    prev_closes = closes[:-1]
    macd_line, signal, _ = _calc_macd_full(prev_closes)
    return macd_line, signal


def _check_strategy(strategy_id: str, closes: np.ndarray, volumes: np.ndarray, technicals: Dict) -> Optional[Dict]:
    """Check if a single strategy triggers for given price data. Returns match details or None."""
    current = float(closes[-1])
    rsi = technicals.get("rsi", 50)
    vol_ratio = technicals.get("vol_ratio", 1.0)

    if strategy_id == "rsi_oversold":
        if rsi < 30:
            return {"strength": round((30 - rsi) / 30 * 100, 1), "detail": f"RSI = {rsi:.1f}"}

    elif strategy_id == "rsi_overbought":
        if rsi > 70:
            return {"strength": round((rsi - 70) / 30 * 100, 1), "detail": f"RSI = {rsi:.1f}"}

    elif strategy_id == "macd_bullish_cross":
        macd_now, signal_now, _ = _calc_macd_full(closes)
        macd_prev, signal_prev = _calc_prev_macd(closes)
        if macd_now > signal_now and macd_prev <= signal_prev:
            return {"strength": 75, "detail": f"MACD {macd_now:.4f} crossed above signal {signal_now:.4f}"}

    elif strategy_id == "macd_bearish_cross":
        macd_now, signal_now, _ = _calc_macd_full(closes)
        macd_prev, signal_prev = _calc_prev_macd(closes)
        if macd_now < signal_now and macd_prev >= signal_prev:
            return {"strength": 75, "detail": f"MACD {macd_now:.4f} crossed below signal {signal_now:.4f}"}

    elif strategy_id == "bollinger_squeeze":
        bb_mid, bb_upper, bb_lower = _calc_bollinger(closes)
        bandwidth = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid > 0 else 0
        # Historical average bandwidth
        if len(closes) >= 40:
            hist_bw = []
            for i in range(20, len(closes)):
                m, u, low = _calc_bollinger(closes[:i + 1])
                if m > 0:
                    hist_bw.append((u - low) / m * 100)
            avg_bw = np.mean(hist_bw) if hist_bw else bandwidth
            if bandwidth < avg_bw * 0.6:
                return {"strength": round((1 - bandwidth / avg_bw) * 100, 1), "detail": f"BW {bandwidth:.2f}% vs avg {avg_bw:.2f}%"}
        elif bandwidth < 4:
            return {"strength": 60, "detail": f"Bandwidth {bandwidth:.2f}% (tight)"}

    elif strategy_id == "ema_golden_cross":
        if len(closes) >= 21:
            ema9_now = _ema(closes, 9)
            ema21_now = _ema(closes, 21)
            ema9_prev = _ema(closes[:-1], 9)
            ema21_prev = _ema(closes[:-1], 21)
            if ema9_now > ema21_now and ema9_prev <= ema21_prev:
                return {"strength": 80, "detail": f"EMA9 {ema9_now:.2f} crossed above EMA21 {ema21_now:.2f}"}

    elif strategy_id == "volume_spike":
        if vol_ratio >= 2.0:
            return {"strength": min(round((vol_ratio - 1) * 50, 1), 100), "detail": f"Volume {vol_ratio:.1f}x above 20-day avg"}

    elif strategy_id == "near_52w_high":
        pct_from_high = technicals.get("pct_from_high", 100)
        if abs(pct_from_high) <= 3:
            return {"strength": round((3 - abs(pct_from_high)) / 3 * 100, 1), "detail": f"{abs(pct_from_high):.1f}% from 52w high"}

    elif strategy_id == "near_52w_low":
        pct_from_low = technicals.get("pct_from_low", 0)
        if pct_from_low <= 5:
            return {"strength": round((5 - pct_from_low) / 5 * 100, 1), "detail": f"{pct_from_low:.1f}% above 52w low"}

    elif strategy_id == "momentum_breakout":
        sma_20 = technicals.get("sma_20")
        if sma_20 and current > sma_20 and rsi > 55 and vol_ratio > 1.3:
            return {
                "strength": round(min((rsi - 55) / 15 * 50 + (vol_ratio - 1) * 50, 100), 1),
                "detail": f"Price > SMA20 (${sma_20}), RSI={rsi:.1f}, Vol={vol_ratio:.1f}x"
            }

    return None


async def scan_symbols(symbols: List[str], strategies: Optional[List[str]] = None) -> Dict:
    """Scan a list of symbols against selected strategies. Returns matches grouped by strategy."""
    active_strategies = strategies or list(STRATEGIES.keys())
    results = {sid: {"info": STRATEGIES[sid], "matches": []} for sid in active_strategies if sid in STRATEGIES}

    scanned = 0
    errors = 0
    for symbol in symbols:
        try:
            prices = await _fetch_daily(symbol, compact=True)
            if not prices or len(prices) < 20:
                continue
            closes = np.array([p["close"] for p in prices])
            volumes = np.array([p["volume"] for p in prices], dtype=float)
            technicals = _compute_technicals(prices)
            current_price = float(closes[-1])

            for sid in active_strategies:
                if sid not in STRATEGIES:
                    continue
                match = _check_strategy(sid, closes, volumes, technicals)
                if match:
                    results[sid]["matches"].append({
                        "symbol": symbol,
                        "price": round(current_price, 2),
                        "strength": match["strength"],
                        "detail": match["detail"],
                        "rsi": technicals.get("rsi"),
                        "vol_ratio": technicals.get("vol_ratio"),
                        "trend": technicals.get("trend"),
                    })
            scanned += 1
        except Exception as e:
            logger.debug(f"Scanner skip {symbol}: {e}")
            errors += 1

    # Sort matches by strength (strongest first)
    for sid in results:
        results[sid]["matches"].sort(key=lambda x: x["strength"], reverse=True)
        results[sid]["match_count"] = len(results[sid]["matches"])

    return {
        "strategies": results,
        "scanned": scanned,
        "errors": errors,
        "total_symbols": len(symbols),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_user_scan_symbols(user_id: str) -> List[str]:
    """Get symbols to scan: user watchlist + popular tickers."""
    symbols = set(POPULAR_TICKERS)
    if _db is not None:
        watchlist = await _db.watchlists.find_one({"user_id": user_id}, {"_id": 0, "tickers": 1})
        if watchlist and watchlist.get("tickers"):
            symbols.update(t.upper() for t in watchlist["tickers"])
    return sorted(symbols)
