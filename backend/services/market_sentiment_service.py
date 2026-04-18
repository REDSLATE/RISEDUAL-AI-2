"""Market Sentiment Service — Fear & Greed Index + VIX-based sentiment signals.

Fetches and caches the Fear & Greed Index from Alternative.me (free, no key needed).
Provides historical F&G data for memory training bootstrap.
"""
import logging
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict

import yfinance as yf

logger = logging.getLogger(__name__)

FNG_API = "https://api.alternative.me/fng/"
_fng_cache = {"data": None, "expires": None}


async def get_fear_greed_index() -> Dict:
    """Get current Fear & Greed Index (cached 30 min)."""
    now = datetime.now(timezone.utc)
    if _fng_cache["data"] and _fng_cache["expires"] and _fng_cache["expires"] > now:
        return _fng_cache["data"]

    try:
        resp = await asyncio.to_thread(requests.get, FNG_API, params={"limit": 1}, timeout=10)
        data = resp.json()
        entry = data.get("data", [{}])[0]
        result = {
            "value": int(entry.get("value", 50)),
            "classification": entry.get("value_classification", "Neutral"),
            "timestamp": entry.get("timestamp", ""),
        }
        _fng_cache["data"] = result
        _fng_cache["expires"] = now + timedelta(minutes=30)
        return result
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        return {"value": 50, "classification": "Neutral", "timestamp": ""}


def get_fear_greed_historical(days: int = 730) -> Dict[str, int]:
    """Get historical Fear & Greed data as {date_str: value} dict.
    Used for training bootstrap to tag historical regimes with sentiment.
    """
    try:
        resp = requests.get(FNG_API, params={"limit": days, "format": "json"}, timeout=30)
        data = resp.json()
        entries = data.get("data", [])
        result = {}
        for e in entries:
            ts = int(e.get("timestamp", 0))
            if ts > 0:
                date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                result[date_str] = int(e.get("value", 50))
        logger.info(f"Loaded {len(result)} days of Fear & Greed history")
        return result
    except Exception as e:
        logger.warning(f"Historical Fear & Greed fetch failed: {e}")
        return {}


async def get_vix_level() -> Dict:
    """Get current VIX (volatility index) via yfinance."""
    try:
        ticker = yf.Ticker("^VIX")
        info = await asyncio.to_thread(lambda: ticker.fast_info)
        price = float(info.get("lastPrice", 0) or info.get("last_price", 0))
        prev = float(info.get("previousClose", 0) or info.get("previous_close", 0))
        change = round(price - prev, 2) if prev > 0 else 0

        if price >= 30:
            level = "Extreme Fear"
        elif price >= 20:
            level = "Elevated"
        elif price >= 15:
            level = "Normal"
        else:
            level = "Complacent"

        return {
            "vix": round(price, 2),
            "change": change,
            "level": level,
        }
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")
        return {"vix": 0, "change": 0, "level": "Unknown"}
