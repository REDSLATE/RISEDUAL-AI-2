"""Finnhub Company News adapter — ticker-specific news from the last 7 days."""
import os
from datetime import datetime, timedelta, timezone
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 600


async def run(query: str, symbol: str = None):
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key or not symbol:
        return EngineResult(engine="finnhub_news", status="skipped", source_type="news", query=query,
                            error="missing_key_or_symbol")

    ticker = symbol.upper()
    cache_key = f"{ticker}_{query[:30]}"
    cached = get_cached("finnhub_news", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        today = datetime.now(timezone.utc).date()
        week_ago = today - timedelta(days=7)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://finnhub.io/api/v1/company-news", params={
                "symbol": ticker,
                "from": week_ago.isoformat(),
                "to": today.isoformat(),
                "token": api_key,
            })
            if resp.status_code != 200:
                return EngineResult(engine="finnhub_news", status="error", source_type="news", query=query,
                                    error=f"http_{resp.status_code}")

            data = resp.json()
            if not isinstance(data, list):
                return EngineResult(engine="finnhub_news", status="error", source_type="news", query=query,
                                    error="unexpected_response")

            items = []
            for row in data[:5]:
                items.append({
                    "title": row.get("headline", ""),
                    "url": row.get("url", ""),
                    "snippet": row.get("summary", "")[:200],
                    "published_at": str(row.get("datetime", "")),
                    "source": row.get("source", ""),
                    "category": row.get("category", ""),
                })

            result = EngineResult(
                engine="finnhub_news", status="ok", source_type="news", query=query,
                title=f"Finnhub News: {ticker}",
                summary=f"{len(items)} articles from the last 7 days" if items else "No recent news",
                items=items, confidence=0.7, authoritative=False, cached=False,
            )
            set_cached("finnhub_news", cache_key, result.model_dump())
            return result

    except Exception as exc:
        return EngineResult(engine="finnhub_news", status="error", source_type="news", query=query,
                            error=str(exc)[:100])
