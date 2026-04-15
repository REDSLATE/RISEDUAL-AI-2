"""Marketstack adapter — EOD market data for the Search War Room.

Uses Marketstack V2 API for end-of-day quotes and recent price history.
Requires MARKETSTACK_API_KEY in env (get one at https://marketstack.com).
"""
import os
import httpx
import logging
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)
TTL = 600  # 10 min cache
BASE = "https://api.marketstack.com/v2"


async def run(query: str, symbol: str = None) -> EngineResult:
    key = os.environ.get("MARKETSTACK_API_KEY", "")
    if not key:
        return EngineResult(engine="marketstack", status="skipped", source_type="market",
                            query=query, error="missing_marketstack_api_key")
    if not symbol:
        return EngineResult(engine="marketstack", status="skipped", source_type="market",
                            query=query, error="no_symbol_provided")

    ticker = symbol.upper()
    cache_key = f"marketstack_{ticker}"
    cached = get_cached("marketstack", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(
                f"{BASE}/eod",
                params={"access_key": key, "symbols": ticker, "limit": 5},
            )
            if resp.status_code != 200:
                return EngineResult(engine="marketstack", status="error", source_type="market",
                                    query=query, error=f"http_{resp.status_code}")
            data = resp.json()
            if "error" in data:
                return EngineResult(engine="marketstack", status="error", source_type="market",
                                    query=query, error=data["error"].get("message", "api_error"))

        rows = data.get("data", [])
        if not rows:
            return EngineResult(engine="marketstack", status="ok", source_type="market",
                                query=query, title=f"Marketstack: no data for {ticker}", items=[])

        latest = rows[0]
        price = latest.get("close", 0)
        prev = rows[1]["close"] if len(rows) > 1 else latest.get("open", price)
        change = round(price - prev, 2) if prev else 0
        change_pct = round((change / prev * 100), 2) if prev else 0

        items = []
        for r in rows:
            items.append({
                "date": r.get("date", "")[:10],
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume": r.get("volume"),
            })

        summary_parts = [f"{ticker} ${price:.2f}"]
        if change_pct:
            summary_parts.append(f"{'+'if change_pct>0 else ''}{change_pct:.1f}%")
        summary_parts.append(f"Vol: {latest.get('volume',0):,.0f}")

        result = EngineResult(
            engine="marketstack", status="ok", source_type="market", query=query,
            title=f"Marketstack: {ticker} EOD Data",
            summary=" | ".join(summary_parts),
            items=items, confidence=0.7, cached=False,
        )
        set_cached("marketstack", cache_key, result.model_dump())
        return result

    except Exception as exc:
        logger.warning(f"Marketstack adapter error: {type(exc).__name__}: {exc}")
        return EngineResult(engine="marketstack", status="error", source_type="market",
                            query=query, error=f"{type(exc).__name__}: {str(exc)[:100]}")
