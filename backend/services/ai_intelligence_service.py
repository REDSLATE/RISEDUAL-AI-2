"""AI Intelligence Service — Stock Scoring, Pattern Recognition, Quick Briefs."""
import logging
import numpy as np
from datetime import datetime, timezone


from services.price_provider import get_quote, get_daily_history

logger = logging.getLogger(__name__)


async def _fetch_daily(symbol: str, compact: bool = True) -> list[dict]:
    """Fetch daily prices via smart price provider (AV -> yfinance -> cache)."""
    outputsize = "compact" if compact else "full"
    history = await get_daily_history(symbol, outputsize)
    if not history:
        return []
    # price_provider returns newest-first; reverse to oldest-first for technicals
    return list(reversed(history))


async def _fetch_quote(symbol: str) -> dict:
    """Fetch real-time quote via smart price provider."""
    quote = await get_quote(symbol)
    if not quote:
        return {"price": 0, "change": 0, "change_pct": 0, "volume": 0, "high": 0, "low": 0, "prev_close": 0}
    return {
        "price": quote.get("price", 0),
        "change": quote.get("change", 0),
        "change_pct": quote.get("change_pct", 0),
        "volume": quote.get("volume", 0),
        "high": quote.get("high", 0),
        "low": quote.get("low", 0),
        "prev_close": quote.get("prev_close", 0),
    }


def _ema(arr, period):
    """Calculate Exponential Moving Average."""
    if len(arr) < period:
        return float(np.mean(arr))
    k = 2.0 / (period + 1)
    val = float(np.mean(arr[:period]))
    for v in arr[period:]:
        val = float(v) * k + val * (1 - k)
    return val


def _calc_rsi(closes, period=14):
    """Calculate Relative Strength Index."""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    return 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100


def _calc_bollinger(closes, period=20):
    """Calculate Bollinger Bands."""
    mid = float(np.mean(closes[-period:]))
    std = float(np.std(closes[-period:]))
    return mid, mid + 2 * std, mid - 2 * std


def _determine_trend(current, sma_20, sma_50):
    """Determine trend direction from moving averages."""
    trend = "bullish" if current > sma_20 else "bearish"
    if sma_50 and current > sma_50:
        trend = "strong_bullish" if trend == "bullish" else "neutral"
    return trend


def _safe_sma(closes: np.ndarray, period: int):
    """Return SMA for the given period, or None if insufficient data."""
    return round(float(np.mean(closes[-period:])), 2) if len(closes) >= period else None


def _yearly_range_pcts(closes: np.ndarray, current: float) -> tuple:
    """Percent from 52-week high and low."""
    year = closes[-260:] if len(closes) >= 260 else closes
    pct_high = ((float(max(year)) - current) / current * 100) if current > 0 else 0
    pct_low = ((current - float(min(year))) / current * 100) if current > 0 else 0
    return round(float(pct_high), 1), round(float(pct_low), 1)


def _compute_technicals(prices: list[dict]) -> dict:
    """Compute key technical indicators from price data."""
    if len(prices) < 20:
        return {}
    closes = np.array([p["close"] for p in prices])
    volumes = np.array([p["volume"] for p in prices], dtype=float)

    current = float(closes[-1])
    rsi = _calc_rsi(closes)
    macd = _ema(closes, 12) - _ema(closes, 26)
    bb_mid, bb_upper, bb_lower = _calc_bollinger(closes)

    avg_vol_20 = float(np.mean(volumes[-20:]))
    vol_ratio = float(volumes[-1] / avg_vol_20) if avg_vol_20 > 0 else 1.0
    pct_from_high, pct_from_low = _yearly_range_pcts(closes, current)

    return {
        "rsi": round(rsi, 1),
        "sma_20": _safe_sma(closes, 20),
        "sma_50": _safe_sma(closes, 50),
        "sma_200": _safe_sma(closes, 200),
        "macd": round(macd, 4),
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "vol_ratio": round(vol_ratio, 2),
        "trend": _determine_trend(current, _safe_sma(closes, 20), _safe_sma(closes, 50)),
        "pct_from_high": pct_from_high,
        "pct_from_low": pct_from_low,
        "current_price": round(current, 2),
        "price_5d_ago": round(float(closes[-6]), 2) if len(closes) >= 6 else None,
        "price_20d_ago": round(float(closes[-21]), 2) if len(closes) >= 21 else None,
    }



def _parse_llm_json(text: str) -> dict:
    """Extract and parse JSON from LLM response text."""
    import json
    text = str(text).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    return json.loads(text)


async def _call_llm(api_key: str, prompt: str, session_prefix: str, symbol: str, system_msg: str) -> dict:
    """Shared LLM call + JSON parse for all intelligence functions."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    session_id = f"{session_prefix}_{symbol}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    chat = LlmChat(api_key=api_key, session_id=session_id,
                    system_message=system_msg).with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=prompt))
    return _parse_llm_json(str(response))


def _calc_performance(closes: list) -> dict:
    """Calculate 1w/1m/3m performance from close prices."""
    perf_1w = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
    perf_1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 2) if len(closes) >= 22 else 0
    perf_3m = round((closes[-1] - closes[-66]) / closes[-66] * 100, 2) if len(closes) >= 66 else 0
    return {"1w": perf_1w, "1m": perf_1m, "3m": perf_3m}


async def _fetch_symbol_context(symbol: str, compact: bool = True) -> tuple:
    """Fetch prices, technicals, and quote for a symbol. Returns (prices, technicals, quote)."""
    prices = await _fetch_daily(symbol, compact=compact)
    if not prices:
        raise ValueError(f"No data found for {symbol}")
    technicals = _compute_technicals(prices)
    quote = await _fetch_quote(symbol)
    return prices, technicals, quote


# ═══════════════════════════════════════
# 1. AI STOCK SCORING (Danelfin-style)
# ═══════════════════════════════════════

async def generate_ai_score(api_key: str, symbol: str) -> dict:
    """Generate a 1-10 AI score with technical, fundamental, and sentiment breakdown."""
    prices, technicals, quote = await _fetch_symbol_context(symbol, compact=False)

    prompt = f"""Analyze {symbol} and provide an AI investment score.

Current Data:
- Price: ${quote['price']}, Change: {quote['change_pct']}%
- RSI(14): {technicals.get('rsi')}, MACD: {technicals.get('macd')}
- SMA20: {technicals.get('sma_20')}, SMA50: {technicals.get('sma_50')}, SMA200: {technicals.get('sma_200')}
- Bollinger: Upper={technicals.get('bb_upper')}, Lower={technicals.get('bb_lower')}
- Volume Ratio (vs 20d avg): {technicals.get('vol_ratio')}x
- Trend: {technicals.get('trend')}
- % from 52w high: {technicals.get('pct_from_high')}%, from low: {technicals.get('pct_from_low')}%

Return ONLY valid JSON:
{{
  "overall_score": 7,
  "technical_score": 8,
  "fundamental_score": 6,
  "sentiment_score": 7,
  "recommendation": "buy",
  "confidence": "high",
  "factors": [
    {{"factor": "Strong momentum above SMA20/50", "impact": "positive", "weight": "high"}},
    {{"factor": "RSI approaching overbought", "impact": "caution", "weight": "medium"}}
  ],
  "risk_level": "moderate",
  "target_range": {{"low": 150.00, "high": 175.00}},
  "time_horizon": "2-4 weeks",
  "summary": "Brief 1-2 sentence verdict"
}}

Scores 1-10 (1=strong sell, 5=hold, 10=strong buy). recommendation: buy/hold/sell. Be realistic based on actual data."""

    scores = await _call_llm(api_key, prompt, "score", symbol,
                              "You are an expert quantitative analyst for a financial research publishing platform. Return only JSON. Never provide personalized investment advice. Present findings as data-driven observations.")

    return {
        "symbol": symbol.upper(),
        "scores": scores,
        "technicals": technicals,
        "quote": quote,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════
# 2. PATTERN RECOGNITION (Tickeron-style)
# ═══════════════════════════════════════

async def detect_patterns(api_key: str, symbol: str) -> dict:
    """Detect chart patterns using AI analysis of price data."""
    prices, technicals, _ = await _fetch_symbol_context(symbol, compact=True)
    if len(prices) < 30:
        raise ValueError(f"Insufficient data for pattern analysis on {symbol}")

    price_summary = [
        f"{p['date']}: O={p['open']:.2f} H={p['high']:.2f} L={p['low']:.2f} C={p['close']:.2f} V={p['volume']}"
        for p in prices[-30:]
    ]

    prompt = f"""Analyze the following 30-day price data for {symbol} and detect ALL active chart patterns.

Price Data (last 30 days):
{chr(10).join(price_summary)}

Technical Context:
- RSI: {technicals.get('rsi')}, MACD: {technicals.get('macd')}, Trend: {technicals.get('trend')}
- SMA20: {technicals.get('sma_20')}, Current: {technicals.get('current_price')}

Return ONLY valid JSON:
{{
  "patterns": [
    {{
      "name": "Bull Flag",
      "type": "continuation",
      "direction": "bullish",
      "confidence": 78,
      "status": "forming",
      "description": "Price consolidating in a tight range after strong upward move",
      "price_target": 185.50,
      "stop_level": 172.00,
      "timeframe": "1-2 weeks"
    }}
  ],
  "overall_bias": "bullish",
  "key_levels": {{
    "resistance": [180.00, 185.00],
    "support": [170.00, 165.00]
  }},
  "volume_analysis": "Volume declining during consolidation — typical for flag patterns"
}}

Pattern types: reversal, continuation, bilateral. Directions: bullish, bearish, neutral. Confidence 0-100. Status: forming, confirmed, breaking_out, failed. Include all patterns you detect — common ones: Head & Shoulders, Double Top/Bottom, Cup & Handle, Bull/Bear Flag, Ascending/Descending Triangle, Wedge, Channel, Pennant, MACD Divergence."""

    result = await _call_llm(api_key, prompt, "pattern", symbol,
                              "You are an expert technical analyst specializing in chart pattern recognition for a financial research publishing platform. Return only JSON. Present findings as observations, not recommendations.")

    return {
        "symbol": symbol.upper(),
        "analysis": result,
        "technicals": technicals,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════
# 3. QUICK BRIEFS (Prospero-style)
# ═══════════════════════════════════════

async def generate_quick_brief(api_key: str, symbol: str) -> dict:
    """Generate a 30-second stock brief with key metrics and verdict."""
    prices, technicals, quote = await _fetch_symbol_context(symbol, compact=True)
    closes = [p["close"] for p in prices]
    perf = _calc_performance(closes)

    prompt = f"""Generate a concise 30-second stock brief for {symbol}.

Data:
- Price: ${quote['price']}, Today: {quote['change_pct']}%
- 1W: {perf['1w']}%, 1M: {perf['1m']}%, 3M: {perf['3m']}%
- RSI: {technicals.get('rsi')}, Trend: {technicals.get('trend')}
- Volume vs avg: {technicals.get('vol_ratio')}x
- BB Upper/Lower: {technicals.get('bb_upper')}/{technicals.get('bb_lower')}

Return ONLY valid JSON:
{{
  "headline": "AAPL Holds Support at $175 — Momentum Building",
  "verdict": "buy",
  "verdict_label": "Bullish Setup",
  "confidence": 72,
  "brief": "Apple is consolidating above its 20-day SMA with RSI showing room to run. Volume is picking up, suggesting institutional interest. Near-term target: $185.",
  "key_metrics": [
    {{"label": "1W Return", "value": "+2.3%", "sentiment": "positive"}},
    {{"label": "RSI(14)", "value": "58", "sentiment": "neutral"}},
    {{"label": "Vol Trend", "value": "1.3x avg", "sentiment": "positive"}},
    {{"label": "Trend", "value": "Bullish", "sentiment": "positive"}}
  ],
  "catalysts": ["Earnings in 2 weeks", "New product announcement expected"],
  "risks": ["Overbought on weekly chart", "Sector rotation risk"],
  "action": "Consider entry near $175 support with stop at $170"
}}

verdict: buy/hold/sell. confidence 0-100. Be concise and actionable. Key metrics max 4 items. Catalysts and risks max 3 each."""

    brief = await _call_llm(api_key, prompt, "brief", symbol,
                             "You are a financial research analyst providing quick stock briefs for a publishing platform. Return only JSON. Be concise. Present data-driven observations, not personal advice.")

    return {
        "symbol": symbol.upper(),
        "brief": brief,
        "performance": perf,
        "quote": quote,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
