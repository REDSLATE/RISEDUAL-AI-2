"""DuckDuckGo adapter — text + news search via subprocess for reliability."""
import asyncio
import subprocess
import json
import logging
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)
TTL = 300


async def run(query: str):
    cached = get_cached('ddg', query, TTL)
    if cached:
        return EngineResult(**cached)

    def _do():
        script = f"from ddgs import DDGS; import json; r=DDGS().text(query={repr(query)}, max_results=5); print(json.dumps(r or []))"
        try:
            result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except Exception as e:
            logger.warning(f"DDG subprocess error: {e}")
        return []

    try:
        raw = await asyncio.to_thread(_do)
        if not raw:
            return EngineResult(engine='ddg', status='error', source_type='search', query=query, error='rate_limited_or_empty')

        items = [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in raw[:5]]
        summary = " | ".join(r.get("title", "")[:60] for r in raw[:3])
        result = EngineResult(
            engine='ddg', status='ok', source_type='search', query=query,
            title=f'DuckDuckGo: {query[:40]}', summary=summary, items=items,
            confidence=0.65, authoritative=False, cached=False,
        )
        set_cached('ddg', query, result.model_dump())
        return result
    except Exception as exc:
        return EngineResult(engine='ddg', status='error', source_type='search', query=query, error=str(exc))


async def run_news(query: str):
    cached = get_cached('ddg_news', query, TTL)
    if cached:
        return EngineResult(**cached)

    def _do():
        script = f"from ddgs import DDGS; import json; r=DDGS().news(query={repr(query)}, max_results=5); print(json.dumps(r or []))"
        try:
            result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except Exception as e:
            logger.warning(f"DDG news subprocess error: {e}")
        return []

    try:
        raw = await asyncio.to_thread(_do)
        if not raw:
            return EngineResult(engine='ddg_news', status='error', source_type='news', query=query, error='rate_limited_or_empty')

        items = [{"title": r.get("title", ""), "url": r.get("url", ""), "source": r.get("source", ""), "date": r.get("date", "")} for r in raw[:5]]
        summary = " | ".join(r.get("title", "")[:50] for r in raw[:3])
        result = EngineResult(
            engine='ddg_news', status='ok', source_type='news', query=query,
            title=f'DDG News: {query[:40]}', summary=summary, items=items,
            confidence=0.7, authoritative=False, cached=False,
        )
        set_cached('ddg_news', query, result.model_dump())
        return result
    except Exception as exc:
        return EngineResult(engine='ddg_news', status='error', source_type='news', query=query, error=str(exc))
