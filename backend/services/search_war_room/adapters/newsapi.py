"""NewsAPI.ai adapter — global financial news via newsapi.ai.

Requires NEWSAPI_API_KEY in env (get one free at https://newsapi.ai).
Uses /api/v1/article/getArticles endpoint with keyword search.
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

    # NewsAPI.ai keyword search is strict — use company name as primary keyword
    TICKER_NAMES = {
        "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Google", "AMZN": "Amazon",
        "TSLA": "Tesla", "META": "Meta", "NVDA": "Nvidia", "NFLX": "Netflix",
        "AMD": "AMD", "INTC": "Intel", "JPM": "JPMorgan", "BAC": "Bank of America",
        "GS": "Goldman Sachs", "V": "Visa", "MA": "Mastercard", "DIS": "Disney",
        "CRM": "Salesforce", "ORCL": "Oracle", "CSCO": "Cisco", "PYPL": "PayPal",
    }
    name = TICKER_NAMES.get(symbol.upper(), symbol) if symbol else None
    # Use company name alone as keyword (strict matching), query goes to cache key only
    keyword = name if name else query
    cache_key = f"newsapi_{keyword}_{query}"
    cached = get_cached("newsapi", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://newsapi.ai/api/v1/article/getArticles",
                params={
                    "keyword": keyword,
                    "lang": "eng",
                    "resultType": "articles",
                    "articlesCount": 8,
                    "articlesSortBy": "date",
                    # Opt-in metadata fields (off by default). These
                    # unlock the richer payload the NewsAPI team
                    # flagged in their onboarding email. Enables
                    # article thumbnails, topic concepts, category
                    # tags, and source ranking/logo — all used by
                    # the War Room UI for richer cards.
                    # Docs: https://www.newsapi.ai/documentation
                    "includeArticleImage": "true",
                    "includeArticleConcepts": "true",
                    "includeArticleCategories": "true",
                    "includeSourceRanking": "true",
                    "includeSourceImage": "true",
                    "apiKey": key,
                },
            )
            if resp.status_code != 200:
                return EngineResult(engine="newsapi", status="error", source_type="news",
                                    query=query, error=f"http_{resp.status_code}")
            data = resp.json()

        articles = data.get("articles", {}).get("results", [])[:8]
        if not articles:
            return EngineResult(engine="newsapi", status="ok", source_type="news",
                                query=query, title="NewsAPI: no articles found", items=[])

        items = []
        for a in articles:
            source = a.get("source") or {}
            # Top-3 concepts are what the UI renders as chips. Keep
            # the raw score so downstream ranking (e.g. entity
            # heatmap) doesn't have to re-derive it.
            concepts = [
                {
                    "label": c.get("label", {}).get("eng") or c.get("uri", ""),
                    "type": c.get("type"),
                    "score": c.get("score"),
                }
                for c in (a.get("concepts") or [])[:3]
            ]
            categories = [
                (c.get("label") or c.get("uri"))
                for c in (a.get("categories") or [])[:2]
                if (c.get("label") or c.get("uri"))
            ]
            items.append({
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "snippet": a.get("body", "")[:300],
                "image": a.get("image"),
                "source": source.get("title", ""),
                "source_image": (source.get("image") or {}).get("url") if isinstance(source.get("image"), dict) else source.get("image"),
                "source_ranking": (source.get("ranking") or {}).get("alexaGlobalRank"),
                "published_at": a.get("dateTime", ""),
                "concepts": concepts,
                "categories": categories,
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
