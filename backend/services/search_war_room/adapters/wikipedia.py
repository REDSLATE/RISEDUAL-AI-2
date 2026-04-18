"""Wikipedia adapter — free knowledge summaries, no key required."""
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 86400


async def run(query: str):
    cached = get_cached('wikipedia', query, TTL)
    if cached:
        return EngineResult(**cached)
    url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(" ", "%20")}'
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(url, headers={'User-Agent': 'RISEDUALAI/1.0 support@risedual.ai'})
            if r.status_code != 200:
                return EngineResult(engine='wikipedia', status='error', source_type='knowledge', query=query, error=f'http_{r.status_code}')
            data = r.json()
            result = EngineResult(
                engine='wikipedia', status='ok', source_type='knowledge', query=query,
                title=data.get('title'), summary=data.get('extract', '')[:500],
                items=[], confidence=0.72, authoritative=False, cached=False,
            )
            set_cached('wikipedia', query, result.model_dump())
            return result
    except Exception as exc:
        return EngineResult(engine='wikipedia', status='error', source_type='knowledge', query=query, error=str(exc))
