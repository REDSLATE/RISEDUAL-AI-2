"""Market Scanner Service — Pre-built screening strategies using technical indicators.

Scans watchlist + popular tickers and returns matches for each strategy.
Reuses existing technical indicator functions from ai_intelligence_service.
"""
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional

from services.ai_intelligence_service import (
    _fetch_daily, _compute_technicals, _calc_rsi, _calc_bollinger, _ema
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


def _check_strategy(strategy_id: str, closes: np.ndarray, volumes: np.ndarray, technicals: dict) -> Optional[dict]:
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


async def scan_symbols(symbols: list[str], strategies: Optional[list[str]] = None) -> dict:
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


async def get_user_scan_symbols(user_id: str) -> list[str]:
    """Get symbols to scan: user watchlist + popular tickers."""
    symbols = set(POPULAR_TICKERS)
    if _db is not None:
        watchlist = await _db.watchlists.find_one({"user_id": user_id}, {"_id": 0, "tickers": 1})
        if watchlist and watchlist.get("tickers"):
            symbols.update(t.upper() for t in watchlist["tickers"])
    return sorted(symbols)



# ── Custom Rule Engine ──

AVAILABLE_INDICATORS = {
    "rsi": {"name": "RSI (14)", "type": "number", "range": [0, 100]},
    "rsi_7": {"name": "RSI (7)", "type": "number", "range": [0, 100]},
    "macd": {"name": "MACD Line", "type": "number", "range": [-50, 50]},
    "macd_signal": {"name": "MACD Signal", "type": "number", "range": [-50, 50]},
    "macd_histogram": {"name": "MACD Histogram", "type": "number", "range": [-10, 10]},
    "bb_upper": {"name": "Bollinger Upper", "type": "price"},
    "bb_lower": {"name": "Bollinger Lower", "type": "price"},
    "bb_bandwidth": {"name": "Bollinger Bandwidth %", "type": "number", "range": [0, 50]},
    "sma_20": {"name": "SMA 20", "type": "price"},
    "sma_50": {"name": "SMA 50", "type": "price"},
    "sma_200": {"name": "SMA 200", "type": "price"},
    "ema_9": {"name": "EMA 9", "type": "price"},
    "ema_21": {"name": "EMA 21", "type": "price"},
    "ema_50": {"name": "EMA 50", "type": "price"},
    "price": {"name": "Current Price", "type": "price"},
    "volume_ratio": {"name": "Volume / 20d Avg", "type": "number", "range": [0, 20]},
    "pct_from_high": {"name": "% From 52w High", "type": "number", "range": [-100, 100]},
    "pct_from_low": {"name": "% Above 52w Low", "type": "number", "range": [0, 500]},
    "atr": {"name": "ATR (14)", "type": "number", "range": [0, 500]},
    "change_1d": {"name": "1-Day Change %", "type": "number", "range": [-30, 30]},
    "change_5d": {"name": "5-Day Change %", "type": "number", "range": [-50, 50]},
    "change_20d": {"name": "20-Day Change %", "type": "number", "range": [-80, 80]},
    "trend": {"name": "Trend", "type": "category", "values": ["strong_bullish", "bullish", "neutral", "bearish"]},
}

OPERATORS = {
    "number": [
        {"id": "gt", "label": ">", "fn": lambda a, b: a > b},
        {"id": "gte", "label": ">=", "fn": lambda a, b: a >= b},
        {"id": "lt", "label": "<", "fn": lambda a, b: a < b},
        {"id": "lte", "label": "<=", "fn": lambda a, b: a <= b},
        {"id": "eq", "label": "=", "fn": lambda a, b: abs(a - b) < 0.01},
        {"id": "between", "label": "between", "fn": lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) else False},
    ],
    "price": [
        {"id": "above", "label": "above", "fn": lambda a, b: a > b},
        {"id": "below", "label": "below", "fn": lambda a, b: a < b},
        {"id": "crosses_above", "label": "crosses above", "fn": lambda a, b: a > b},
        {"id": "crosses_below", "label": "crosses below", "fn": lambda a, b: a < b},
    ],
    "category": [
        {"id": "is", "label": "is", "fn": lambda a, b: a == b},
        {"id": "is_not", "label": "is not", "fn": lambda a, b: a != b},
    ],
}


def _compute_extended_indicators(prices: list[dict]) -> dict:
    """Compute all available indicators for the custom rule engine."""
    base = _compute_technicals(prices)
    if not base:
        return {}

    closes = np.array([p["close"] for p in prices])
    current = float(closes[-1])

    # Extended indicators
    indicators = {
        "price": current,
        "rsi": base.get("rsi", 50),
        "macd": base.get("macd", 0),
        "bb_upper": base.get("bb_upper", 0),
        "bb_lower": base.get("bb_lower", 0),
        "sma_20": base.get("sma_20", 0),
        "sma_50": base.get("sma_50", 0),
        "sma_200": base.get("sma_200", 0),
        "volume_ratio": base.get("vol_ratio", 1),
        "pct_from_high": base.get("pct_from_high", 0),
        "pct_from_low": base.get("pct_from_low", 0),
        "trend": base.get("trend", "neutral"),
    }

    # RSI 7
    if len(closes) >= 7:
        indicators["rsi_7"] = round(_calc_rsi(closes, 7), 1)

    # EMAs
    if len(closes) >= 9:
        indicators["ema_9"] = round(_ema(closes, 9), 4)
    if len(closes) >= 21:
        indicators["ema_21"] = round(_ema(closes, 21), 4)
    if len(closes) >= 50:
        indicators["ema_50"] = round(_ema(closes, 50), 4)

    # MACD signal & histogram
    macd_line, signal, histogram = _calc_macd_full(closes)
    indicators["macd_signal"] = round(signal, 4)
    indicators["macd_histogram"] = round(histogram, 4)

    # Bollinger bandwidth
    bb_mid, bb_upper, bb_lower = _calc_bollinger(closes)
    indicators["bb_bandwidth"] = round((bb_upper - bb_lower) / bb_mid * 100, 2) if bb_mid > 0 else 0

    # ATR (14)
    if len(prices) >= 15:
        trs = []
        for i in range(-14, 0):
            h = prices[i]["high"]
            lo = prices[i]["low"]
            pc = prices[i - 1]["close"]
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        indicators["atr"] = round(np.mean(trs), 4)

    # Price changes
    if len(closes) >= 2:
        indicators["change_1d"] = round((current - float(closes[-2])) / float(closes[-2]) * 100, 2)
    if len(closes) >= 6:
        indicators["change_5d"] = round((current - float(closes[-6])) / float(closes[-6]) * 100, 2)
    if len(closes) >= 21:
        indicators["change_20d"] = round((current - float(closes[-21])) / float(closes[-21]) * 100, 2)

    return indicators


def _evaluate_condition(indicator_values: dict, condition: dict) -> bool:
    """Evaluate a single condition against indicator values."""
    ind_id = condition.get("indicator")
    op_id = condition.get("operator")
    value = condition.get("value")

    if ind_id not in indicator_values:
        return False

    actual = indicator_values[ind_id]
    ind_type = AVAILABLE_INDICATORS.get(ind_id, {}).get("type", "number")
    op_list = OPERATORS.get(ind_type, [])
    op = next((o for o in op_list if o["id"] == op_id), None)
    if not op:
        return False

    try:
        if ind_type == "category":
            return op["fn"](str(actual), str(value))
        return op["fn"](float(actual), float(value) if not isinstance(value, (list, tuple)) else value)
    except (ValueError, TypeError):
        return False


def _evaluate_rule_group(indicator_values: dict, group: dict) -> bool:
    """Evaluate a group of conditions with AND/OR logic."""
    logic = group.get("logic", "AND").upper()
    conditions = group.get("conditions", [])
    sub_groups = group.get("groups", [])

    results = []
    for cond in conditions:
        results.append(_evaluate_condition(indicator_values, cond))
    for sub in sub_groups:
        results.append(_evaluate_rule_group(indicator_values, sub))

    if not results:
        return False
    return all(results) if logic == "AND" else any(results)


async def evaluate_custom_rule(symbols: list[str], rule: dict) -> dict:
    """Evaluate a custom rule against a list of symbols."""
    matches = []
    scanned = 0
    errors = 0

    for symbol in symbols:
        try:
            prices = await _fetch_daily(symbol, compact=True)
            if not prices or len(prices) < 20:
                continue
            indicators = _compute_extended_indicators(prices)
            if not indicators:
                continue
            scanned += 1

            if _evaluate_rule_group(indicators, rule):
                matches.append({
                    "symbol": symbol,
                    "price": indicators.get("price", 0),
                    "rsi": indicators.get("rsi"),
                    "volume_ratio": indicators.get("volume_ratio"),
                    "trend": indicators.get("trend"),
                    "indicators": {k: v for k, v in indicators.items() if k in _extract_rule_indicators(rule)},
                })
        except Exception as e:
            logger.debug(f"Custom rule skip {symbol}: {e}")
            errors += 1

    return {
        "matches": matches,
        "match_count": len(matches),
        "scanned": scanned,
        "errors": errors,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_rule_indicators(rule: dict) -> set:
    """Extract all indicator IDs referenced in a rule for the response."""
    ids = set()
    for cond in rule.get("conditions", []):
        if cond.get("indicator"):
            ids.add(cond["indicator"])
    for sub in rule.get("groups", []):
        ids.update(_extract_rule_indicators(sub))
    return ids


async def save_custom_rule(user_id: str, rule_data: dict) -> dict:
    """Save a custom scanning rule for a user."""
    if _db is None:
        return {"error": "DB not available"}

    doc = {
        "user_id": user_id,
        "name": rule_data.get("name", "Untitled Rule"),
        "description": rule_data.get("description", ""),
        "rule": rule_data.get("rule", {}),
        "signal_type": rule_data.get("signal_type", "neutral"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await _db.custom_scan_rules.insert_one(doc)
    doc.pop("_id", None)
    doc["rule_id"] = str(result.inserted_id)
    return doc


async def get_user_rules(user_id: str) -> list[dict]:
    """Get all custom rules for a user."""
    if _db is None:
        return []
    cursor = _db.custom_scan_rules.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(length=50)


async def delete_custom_rule(user_id: str, rule_id: str) -> dict:
    """Delete a custom rule."""
    if _db is None:
        return {"error": "DB not available"}
    from bson import ObjectId
    result = await _db.custom_scan_rules.delete_one({"_id": ObjectId(rule_id), "user_id": user_id})
    if result.deleted_count == 0:
        return {"error": "Rule not found"}
    return {"status": "deleted", "rule_id": rule_id}
