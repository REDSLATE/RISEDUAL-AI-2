"""Tavily adapter — premium web search for the Search War Room.

Uses Tavily's advanced search with AI-generated answer + finance topic.
Falls back gracefully when TAVILY_API_KEY is not configured.
"""
import os
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 600  # 10 min cache


async def run(query: str) -> EngineResult:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return EngineResult(engine="tavily", status="skipped", source_type="search", query=query, error="missing_tavily_api_key")

    cached = get_cached("tavily", query, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 5,
                    "include_answer": True,
                    "topic": "finance",
                },
            )
            if resp.status_code != 200:
                return EngineResult(engine="tavily", status="error", source_type="search", query=query, error=f"http_{resp.status_code}")

            data = resp.json()
            answer = data.get("answer", "")
            results = data.get("results", [])

            items = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")[:300],
                 "score": r.get("score", 0)}
                for r in results[:5]
            ]

            summary = answer if answer else (items[0]["content"][:200] if items else "No results")

            result = EngineResult(
                engine="tavily",
                status="ok",
                source_type="search",
                query=query,
                title=f"Tavily: {query}",
                summary=summary,
                items=items,
                confidence=0.88,
                authoritative=False,
                cached=False,
            )
            set_cached("tavily", query, result.model_dump())
            return result

    except Exception as exc:
        return EngineResult(engine="tavily", status="error", source_type="search", query=query, error=str(exc)[:100])
