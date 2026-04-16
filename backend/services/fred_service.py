"""FRED Economic Data Service — pulls key macro indicators from the Federal Reserve.

Uses the FRED API (api.stlouisfed.org) with the key from FRED_API_KEYS env var.
Provides curated economic indicators for the Macro Dashboard.
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE = "https://api.stlouisfed.org/fred"
CACHE = {}
CACHE_TTL = 3600  # 1 hour — macro data doesn't change fast


def _get_key() -> str:
    keys = os.environ.get("FRED_API_KEYS", "")
    return keys.split(",")[0].strip() if keys else ""


# Key macro series to track
MACRO_SERIES = [
    # Growth
    {"id": "GDP", "name": "Gross Domestic Product", "category": "Growth", "unit": "$T", "divisor": 1000, "decimals": 2},
    {"id": "GDPC1", "name": "Real GDP", "category": "Growth", "unit": "$T", "divisor": 1000, "decimals": 2},

    # Inflation
    {"id": "CPIAUCSL", "name": "Consumer Price Index (CPI)", "category": "Inflation", "unit": "", "divisor": 1, "decimals": 1},
    {"id": "CPILFESL", "name": "Core CPI (ex Food & Energy)", "category": "Inflation", "unit": "", "divisor": 1, "decimals": 1},
    {"id": "PCEPI", "name": "PCE Price Index", "category": "Inflation", "unit": "", "divisor": 1, "decimals": 1},

    # Employment
    {"id": "UNRATE", "name": "Unemployment Rate", "category": "Employment", "unit": "%", "divisor": 1, "decimals": 1},
    {"id": "PAYEMS", "name": "Nonfarm Payrolls", "category": "Employment", "unit": "K", "divisor": 1, "decimals": 0},
    {"id": "ICSA", "name": "Initial Jobless Claims", "category": "Employment", "unit": "K", "divisor": 1, "decimals": 0},

    # Rates
    {"id": "FEDFUNDS", "name": "Federal Funds Rate", "category": "Rates", "unit": "%", "divisor": 1, "decimals": 2},
    {"id": "DGS10", "name": "10-Year Treasury Yield", "category": "Rates", "unit": "%", "divisor": 1, "decimals": 2},
    {"id": "DGS2", "name": "2-Year Treasury Yield", "category": "Rates", "unit": "%", "divisor": 1, "decimals": 2},
    {"id": "T10Y2Y", "name": "10Y-2Y Yield Spread", "category": "Rates", "unit": "%", "divisor": 1, "decimals": 2},

    # Housing & Consumer
    {"id": "HOUST", "name": "Housing Starts", "category": "Housing", "unit": "K", "divisor": 1, "decimals": 0},
    {"id": "UMCSENT", "name": "Consumer Sentiment (UMich)", "category": "Consumer", "unit": "", "divisor": 1, "decimals": 1},

    # Trade
    {"id": "BOPGSTB", "name": "Trade Balance (Goods & Services)", "category": "Trade", "unit": "$B", "divisor": 1000, "decimals": 1},
]


async def _fetch_series(client: httpx.AsyncClient, series_id: str, key: str, limit: int = 12) -> Optional[dict]:
    """Fetch observations for a single FRED series."""
    try:
        resp = await client.get(f"{BASE}/series/observations", params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        })
        if resp.status_code == 200:
            data = resp.json()
            obs = data.get("observations", [])
            # Filter out missing values
            valid = [o for o in obs if o.get("value") not in (".", "", None)]
            return {"series_id": series_id, "observations": valid}
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
    return None


async def get_macro_indicators() -> dict:
    """Get all key macro indicators with latest values and trends."""
    key = _get_key()
    if not key:
        return {"error": "FRED API key not configured", "indicators": []}

    # Check cache
    cache_key = "macro_indicators"
    if cache_key in CACHE:
        cached_at, data = CACHE[cache_key]
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_TTL:
            return data

    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [_fetch_series(client, s["id"], key) for s in MACRO_SERIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    indicators = []
    categories = {}

    for spec, result in zip(MACRO_SERIES, results):
        if isinstance(result, Exception) or result is None:
            continue

        obs = result.get("observations", [])
        if not obs:
            continue

        latest_val = None
        prev_val = None
        latest_date = None

        for o in obs:
            try:
                v = float(o["value"])
                if latest_val is None:
                    latest_val = v
                    latest_date = o["date"]
                elif prev_val is None:
                    prev_val = v
                    break
            except (ValueError, TypeError):
                continue

        if latest_val is None:
            continue

        # Calculate change
        change = None
        change_pct = None
        if prev_val is not None and prev_val != 0:
            change = latest_val - prev_val
            change_pct = (change / abs(prev_val)) * 100

        # Format display value
        divisor = spec.get("divisor", 1)
        decimals = spec.get("decimals", 2)
        display_val = latest_val / divisor if divisor > 1 else latest_val

        # Historical mini-series (for sparkline)
        history = []
        for o in reversed(obs[:12]):
            try:
                history.append({"date": o["date"], "value": float(o["value"])})
            except (ValueError, TypeError):
                pass

        indicator = {
            "id": spec["id"],
            "name": spec["name"],
            "category": spec["category"],
            "value": round(display_val, decimals),
            "raw_value": latest_val,
            "unit": spec["unit"],
            "date": latest_date,
            "change": round(change, decimals) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "history": history,
        }
        indicators.append(indicator)

        # Group by category
        cat = spec["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(indicator)

    result = {
        "indicators": indicators,
        "categories": categories,
        "count": len(indicators),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    CACHE[cache_key] = (datetime.now(timezone.utc), result)
    return result


async def get_series_detail(series_id: str, limit: int = 60) -> dict:
    """Get detailed observations for a specific FRED series."""
    key = _get_key()
    if not key:
        return {"error": "FRED API key not configured"}

    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch series info
        info_resp = await client.get(f"{BASE}/series", params={
            "series_id": series_id, "api_key": key, "file_type": "json",
        })
        series_info = {}
        if info_resp.status_code == 200:
            seriess = info_resp.json().get("seriess", [])
            if seriess:
                series_info = seriess[0]

        # Fetch observations
        result = await _fetch_series(client, series_id, key, limit=limit)

    if not result:
        return {"error": f"No data for series {series_id}"}

    obs = result.get("observations", [])
    history = []
    for o in reversed(obs):
        try:
            history.append({"date": o["date"], "value": float(o["value"])})
        except (ValueError, TypeError):
            pass

    return {
        "series_id": series_id,
        "title": series_info.get("title", series_id),
        "frequency": series_info.get("frequency", ""),
        "units": series_info.get("units", ""),
        "last_updated": series_info.get("last_updated", ""),
        "observations": history,
        "count": len(history),
    }


async def get_release_series(release_id: int, limit: int = 50) -> dict:
    """Get all series in a FRED release (e.g., release 51 = Trade data)."""
    key = _get_key()
    if not key:
        return {"error": "FRED API key not configured"}

    cache_key = f"release_{release_id}"
    if cache_key in CACHE:
        cached_at, data = CACHE[cache_key]
        if (datetime.now(timezone.utc) - cached_at).total_seconds() < CACHE_TTL:
            return data

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{BASE}/release/series", params={
            "release_id": release_id,
            "api_key": key,
            "file_type": "json",
            "limit": limit,
            "order_by": "popularity",
            "sort_order": "desc",
        })
        if resp.status_code != 200:
            return {"error": f"FRED returned HTTP {resp.status_code}"}

        data = resp.json()
        series_list = data.get("seriess", [])

    result = {
        "release_id": release_id,
        "series": [
            {
                "id": s["id"],
                "title": s.get("title", ""),
                "frequency": s.get("frequency", ""),
                "units": s.get("units", ""),
                "seasonal_adjustment": s.get("seasonal_adjustment", ""),
                "last_updated": s.get("last_updated", ""),
                "popularity": s.get("popularity", 0),
                "observation_start": s.get("observation_start", ""),
                "observation_end": s.get("observation_end", ""),
            }
            for s in series_list
        ],
        "count": len(series_list),
    }

    CACHE[cache_key] = (datetime.now(timezone.utc), result)
    return result


async def search_series(query: str, limit: int = 20) -> dict:
    """Search FRED series by keyword."""
    key = _get_key()
    if not key:
        return {"error": "FRED API key not configured"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/series/search", params={
            "search_text": query,
            "api_key": key,
            "file_type": "json",
            "limit": limit,
            "order_by": "popularity",
            "sort_order": "desc",
        })
        if resp.status_code != 200:
            return {"error": f"FRED search returned HTTP {resp.status_code}"}

        data = resp.json()
        series_list = data.get("seriess", [])

    return {
        "query": query,
        "results": [
            {
                "id": s["id"],
                "title": s.get("title", ""),
                "frequency": s.get("frequency", ""),
                "units": s.get("units", ""),
                "popularity": s.get("popularity", 0),
                "last_updated": s.get("last_updated", ""),
            }
            for s in series_list
        ],
        "count": len(series_list),
    }
