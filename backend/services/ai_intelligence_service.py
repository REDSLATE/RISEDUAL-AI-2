"""AI Intelligence Service — Stock Scoring, Pattern Recognition, Quick Briefs."""
import os
import logging
import asyncio
import requests
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"


def _av_key():
    return os.environ.get("ALPHA_VANTAGE_API_KEY", "")


async def _fetch_daily(symbol: str, compact: bool = True) -> List[Dict]:
    """Fetch daily prices from Alpha Vantage."""
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol.upper(),
        "outputsize": "compact" if compact else "full",
        "apikey": _av_key(),
    }
    resp = await asyncio.to_thread(requests.get, AV_BASE, params=params, timeout=15)
    ts = resp.json().get("Time Series (Daily)", {})
    prices = []
    for d, bar in sorted(ts.items()):
        prices.append({
            "date": d,
            "open": float(bar["1. open"]),
            "high": float(bar["2. high"]),
            "low": float(bar["3. low"]),
            "close": float(bar["4. close"]),
            "volume": int(bar["5. volume"]),
        })
    return prices


async def _fetch_quote(symbol: str) -> Dict:
    """Fetch real-time quote."""
    params = {"function": "GLOBAL_QUOTE", "symbol": symbol.upper(), "apikey": _av_key()}
    resp = await asyncio.to_thread(requests.get, AV_BASE, params=params, timeout=10)
    gq = resp.json().get("Global Quote", {})
    return {
        "price": float(gq.get("05. price", 0)),
        "change": float(gq.get("09. change", 0)),
        "change_pct": float(gq.get("10. change percent", "0").replace("%", "")),
        "volume": int(gq.get("06. volume", 0)),
        "high": float(gq.get("03. high", 0)),
        "low": float(gq.get("04. low", 0)),
        "prev_close": float(gq.get("08. previous close", 0)),
    }


def _compute_technicals(prices: List[Dict]) -> Dict:
    """Compute key technical indicators from price data."""
    if len(prices) < 20:
        return {}
    closes = np.array([p["close"] for p in prices])
    volumes = np.array([p["volume"] for p in prices], dtype=float)

    # RSI (14)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-14:])
    avg_loss = np.mean(losses[-14:])
    rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100

    # SMAs
    sma_20 = float(np.mean(closes[-20:]))
    sma_50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else None
    sma_200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None

    # MACD
    def ema(arr, p):
        if len(arr) < p:
            return float(np.mean(arr))
        k = 2.0 / (p + 1)
        val = float(np.mean(arr[:p]))
        for v in arr[p:]:
            val = float(v) * k + val * (1 - k)
        return val

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd = ema12 - ema26

    # Bollinger Bands
    bb_mid = sma_20
    bb_std = float(np.std(closes[-20:]))
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Volume trend
    avg_vol_20 = float(np.mean(volumes[-20:]))
    vol_ratio = float(volumes[-1] / avg_vol_20) if avg_vol_20 > 0 else 1.0

    # Price position
    current = float(closes[-1])
    pct_from_high = ((max(closes[-52 * 5:] if len(closes) >= 260 else closes) - current) / current * 100) if current > 0 else 0
    pct_from_low = ((current - min(closes[-52 * 5:] if len(closes) >= 260 else closes)) / current * 100) if current > 0 else 0

    # Trend direction
    trend = "bullish" if current > sma_20 else "bearish"
    if sma_50 and current > sma_50:
        trend = "strong_bullish" if trend == "bullish" else "neutral"

    return {
        "rsi": round(rsi, 1),
        "sma_20": round(sma_20, 2),
        "sma_50": round(sma_50, 2) if sma_50 else None,
        "sma_200": round(sma_200, 2) if sma_200 else None,
        "macd": round(macd, 4),
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "vol_ratio": round(vol_ratio, 2),
        "trend": trend,
        "pct_from_high": round(float(pct_from_high), 1),
        "pct_from_low": round(float(pct_from_low), 1),
        "current_price": round(current, 2),
        "price_5d_ago": round(float(closes[-6]), 2) if len(closes) >= 6 else None,
        "price_20d_ago": round(float(closes[-21]), 2) if len(closes) >= 21 else None,
    }


# ═══════════════════════════════════════
# 1. AI STOCK SCORING (Danelfin-style)
# ═══════════════════════════════════════

async def generate_ai_score(api_key: str, symbol: str) -> Dict:
    """Generate a 1-10 AI score with technical, fundamental, and sentiment breakdown."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    prices = await _fetch_daily(symbol, compact=False)
    if not prices:
        raise ValueError(f"No data found for {symbol}")

    technicals = _compute_technicals(prices)
    quote = await _fetch_quote(symbol)

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

    session_id = f"score_{symbol}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    chat = LlmChat(api_key=api_key, session_id=session_id,
                    system_message="You are an expert quantitative analyst. Return only JSON.").with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=prompt))

    import json
    text = str(response).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    scores = json.loads(text)

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

async def detect_patterns(api_key: str, symbol: str) -> Dict:
    """Detect chart patterns using AI analysis of price data."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    prices = await _fetch_daily(symbol, compact=True)
    if len(prices) < 30:
        raise ValueError(f"Insufficient data for pattern analysis on {symbol}")

    # Prepare last 60 days of OHLCV for the AI
    recent = prices[-60:]
    price_summary = []
    for p in recent[-30:]:
        price_summary.append(f"{p['date']}: O={p['open']:.2f} H={p['high']:.2f} L={p['low']:.2f} C={p['close']:.2f} V={p['volume']}")

    technicals = _compute_technicals(prices)

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

    session_id = f"pattern_{symbol}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    chat = LlmChat(api_key=api_key, session_id=session_id,
                    system_message="You are an expert technical analyst specializing in chart pattern recognition. Return only JSON.").with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=prompt))

    import json
    text = str(response).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    result = json.loads(text)

    return {
        "symbol": symbol.upper(),
        "analysis": result,
        "technicals": technicals,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════
# 3. QUICK BRIEFS (Prospero-style)
# ═══════════════════════════════════════

async def generate_quick_brief(api_key: str, symbol: str) -> Dict:
    """Generate a 30-second stock brief with key metrics and verdict."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    prices = await _fetch_daily(symbol, compact=True)
    if not prices:
        raise ValueError(f"No data found for {symbol}")

    technicals = _compute_technicals(prices)
    quote = await _fetch_quote(symbol)

    # Calculate quick stats
    closes = [p["close"] for p in prices]
    perf_1w = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
    perf_1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 2) if len(closes) >= 22 else 0
    perf_3m = round((closes[-1] - closes[-66]) / closes[-66] * 100, 2) if len(closes) >= 66 else 0

    prompt = f"""Generate a concise 30-second stock brief for {symbol}.

Data:
- Price: ${quote['price']}, Today: {quote['change_pct']}%
- 1W: {perf_1w}%, 1M: {perf_1m}%, 3M: {perf_3m}%
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

    session_id = f"brief_{symbol}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    chat = LlmChat(api_key=api_key, session_id=session_id,
                    system_message="You are a financial analyst providing quick stock briefs. Return only JSON. Be concise.").with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=prompt))

    import json
    text = str(response).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    brief = json.loads(text)

    return {
        "symbol": symbol.upper(),
        "brief": brief,
        "performance": {"1w": perf_1w, "1m": perf_1m, "3m": perf_3m},
        "quote": quote,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
