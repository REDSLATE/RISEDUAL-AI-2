"""NewsAPI adapter — global financial news via newsapi.org.

Requires NEWSAPI_API_KEY in env (get one free at https://newsapi.org/register).
Returns recent English-language articles sorted by publish date.
"""
import os
import httpx
import logging
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)
TTL = 600  # 10 min cache


async def run(query: str, symbol: str = None) -> EngineResult:
    key = os.environ.get("NEWSAPI_API_KEY", "")
    if not key:
        return EngineResult(engine="newsapi", status="skipped", source_type="news",
                            query=query, error="missing_newsapi_api_key")

    effective_query = f"{symbol} {query}" if symbol else query
    cache_key = f"newsapi_{effective_query}"
    cached = get_cached("newsapi", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": effective_query,
                    "pageSize": 8,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "apiKey": key,
                },
            )
            if resp.status_code != 200:
                return EngineResult(engine="newsapi", status="error", source_type="news",
                                    query=query, error=f"http_{resp.status_code}")
            data = resp.json()

        articles = data.get("articles", [])[:8]
        if not articles:
            return EngineResult(engine="newsapi", status="ok", source_type="news",
                                query=query, title="NewsAPI: no articles found", items=[])

        items = []
        for a in articles:
            items.append({
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "snippet": a.get("description", ""),
                "source": a.get("source", {}).get("name", ""),
                "published_at": a.get("publishedAt", ""),
            })

        headline = f"NewsAPI: {len(items)} articles"
        if symbol:
            headline += f" for {symbol.upper()}"

        result = EngineResult(
            engine="newsapi", status="ok", source_type="news", query=query,
            title=headline,
            summary=f"{items[0]['title']} — {items[0]['source']}" if items else "",
            items=items, confidence=0.6, cached=False,
        )
        set_cached("newsapi", cache_key, result.model_dump())
        return result

    except Exception as exc:
        logger.warning(f"NewsAPI adapter error: {exc}")
        return EngineResult(engine="newsapi", status="error", source_type="news",
                            query=query, error=str(exc)[:100])
