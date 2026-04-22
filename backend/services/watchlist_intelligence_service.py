"""Watchlist Intelligence Service — Batch AI analysis of user's watchlist tickers."""
import logging
import asyncio
from typing import Any
import numpy as np
from datetime import datetime, timezone


from services.price_provider import get_quote_sync, get_daily_history_sync

logger = logging.getLogger(__name__)


def _fetch_quote(symbol: str) -> dict:
    """Fetch real-time quote via smart price provider (AV -> yfinance)."""
    try:
        quote = get_quote_sync(symbol)
        if not quote:
            return {"symbol": symbol.upper(), "price": 0, "change": 0, "change_pct": 0, "volume": 0, "error": True}
        return {
            "symbol": symbol.upper(),
            "price": quote.get("price", 0),
            "change": quote.get("change", 0),
            "change_pct": quote.get("change_pct", 0),
            "volume": quote.get("volume", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "prev_close": quote.get("prev_close", 0),
        }
    except Exception as e:
        logger.warning(f"Quote fetch failed for {symbol}: {e}")
        return {"symbol": symbol.upper(), "price": 0, "change": 0, "change_pct": 0, "volume": 0, "error": True}


def _fetch_daily_compact(symbol: str) -> list[dict]:
    """Fetch compact daily prices via smart price provider (AV -> yfinance)."""
    try:
        history = get_daily_history_sync(symbol, "compact")
        if not history:
            return []
        # price_provider returns newest-first; reverse to oldest-first for technicals
        return list(reversed(history))
    except Exception as e:
        logger.warning(f"Daily fetch failed for {symbol}: {e}")
        return []


def _quick_technicals(prices: list[dict]) -> dict:
    """Compute quick technicals from price data."""
    if len(prices) < 15:
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

    sma_20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else float(np.mean(closes))
    sma_50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else None

    avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
    vol_ratio = float(volumes[-1] / avg_vol) if avg_vol > 0 else 1.0

    current = float(closes[-1])
    trend = "bullish" if current > sma_20 else "bearish"

    perf_1w = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
    perf_1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 2) if len(closes) >= 22 else 0

    return {
        "rsi": round(rsi, 1),
        "sma_20": round(sma_20, 2),
        "sma_50": round(sma_50, 2) if sma_50 else None,
        "vol_ratio": round(vol_ratio, 2),
        "trend": trend,
        "perf_1w": perf_1w,
        "perf_1m": perf_1m,
    }


import time


def _gather_ticker_data(tickers: list[str]) -> list[dict]:
    """Gather quotes + technicals for each ticker. Premium plan: 150 req/min."""
    results = []
    for i, ticker in enumerate(tickers[:10]):  # Cap at 10 tickers
        quote = _fetch_quote(ticker)
        prices = _fetch_daily_compact(ticker)
        technicals = _quick_technicals(prices) if prices else {}

        results.append({
            "symbol": ticker.upper(),
            "quote": quote,
            "technicals": technicals,
        })

        # 150 req/min = 2.5 req/sec. 2 calls per ticker, short pause every few tickers.
        if (i + 1) % 5 == 0 and i < len(tickers) - 1:
            time.sleep(1)

    return results


async def generate_watchlist_summary(api_key: str, tickers: list[str], db: Any = None, user_id: Any = None) -> dict:
    """Generate a batch AI intelligence summary for all watchlist tickers."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    if not tickers:
        return {
            "tickers": [],
            "summary": {"headline": "Empty Watchlist", "outlook": "Add tickers to your watchlist to get AI intelligence."},
            "top_movers": [],
            "alerts": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Check cache (1 hour TTL)
    if db is not None and user_id is not None:
        cache_key = f"wl_intel_{user_id}"
        cached = await db.watchlist_intelligence.find_one({"cache_key": cache_key}, {"_id": 0})
        if cached:
            cached_time = datetime.fromisoformat(cached.get("generated_at", "2000-01-01"))
            age_minutes = (datetime.now(timezone.utc) - cached_time.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_minutes < 60:
                return cached.get("data", {})

    # Gather market data for all tickers (run in thread to avoid blocking)
    ticker_data = await asyncio.to_thread(_gather_ticker_data, tickers)

    # Build ticker summary for AI
    ticker_lines = []
    for td in ticker_data:
        q = td["quote"]
        t = td["technicals"]
        ticker_lines.append(
            f"{td['symbol']}: Price=${q.get('price', 0):.2f}, Change={q.get('change_pct', 0):.1f}%, "
            f"RSI={t.get('rsi', 'N/A')}, Trend={t.get('trend', 'N/A')}, "
            f"VolRatio={t.get('vol_ratio', 'N/A')}x, 1W={t.get('perf_1w', 0)}%, 1M={t.get('perf_1m', 0)}%"
        )

    prompt = f"""Analyze this watchlist of {len(ticker_data)} stocks and provide a comprehensive intelligence summary.

Watchlist Data:
{chr(10).join(ticker_lines)}

Return ONLY valid JSON:
{{
  "summary": {{
    "headline": "Watchlist trending bullish — 4 of 6 stocks above SMA20",
    "outlook": "2-3 sentence market outlook based on the watchlist composition",
    "health_score": 72,
    "bullish_count": 4,
    "bearish_count": 2,
    "neutral_count": 0
  }},
  "tickers": [
    {{
      "symbol": "AAPL",
      "score": 7,
      "verdict": "buy",
      "one_liner": "Holding support, momentum building",
      "alert": null
    }}
  ],
  "top_movers": [
    {{"symbol": "TSLA", "change_pct": -3.2, "reason": "Broke below SMA20"}}
  ],
  "alerts": [
    {{"symbol": "AAPL", "type": "oversold", "severity": "medium", "message": "RSI dropped below 30 — potential bounce"}}
  ]
}}

Rules:
- score: 1-10 (1=strong sell, 10=strong buy)
- verdict: buy/hold/sell
- alert types: oversold, overbought, breakout, breakdown, volume_spike, trend_change
- severity: low, medium, high
- Include ALL tickers from the watchlist in the tickers array
- top_movers: top 2-3 by absolute change %
- alerts: only for tickers that actually need attention (RSI extremes, big volume, trend shifts)
- health_score: 0-100 representing overall watchlist health"""

    session_id = f"wl_intel_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    chat = LlmChat(api_key=api_key, session_id=session_id,
                    system_message="You are an expert portfolio analyst. Return only JSON. Be concise and actionable.").with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=prompt))

    import json
    text = str(response).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    ai_result = json.loads(text)

    # Merge quote data into ticker results
    quote_map = {td["symbol"]: td["quote"] for td in ticker_data}
    tech_map = {td["symbol"]: td["technicals"] for td in ticker_data}
    for t in ai_result.get("tickers", []):
        sym = t.get("symbol", "")
        t["quote"] = quote_map.get(sym, {})
        t["technicals"] = tech_map.get(sym, {})

    result = {
        "tickers": ai_result.get("tickers", []),
        "summary": ai_result.get("summary", {}),
        "top_movers": ai_result.get("top_movers", []),
        "alerts": ai_result.get("alerts", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Cache in MongoDB
    if db is not None and user_id is not None:
        await db.watchlist_intelligence.update_one(
            {"cache_key": cache_key},
            {"$set": {"cache_key": cache_key, "data": result, "generated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True
        )

    return result
