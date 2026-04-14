"""Search War Room — Orchestrator fires all engines in parallel."""
import asyncio
import logging
from services.search_war_room.schemas import SearchWarRoomResponse
from services.search_war_room.adapters import ddg, wikipedia, fred, sec, yahoo
from services.search_war_room.synthesizer import build_brief

logger = logging.getLogger(__name__)


def classify_mode(query: str, mode: str) -> str:
    if mode != 'auto':
        return mode
    q = query.lower()
    if any(x in q for x in ['cpi', 'gdp', 'rates', 'unemployment', 'fed', 'inflation', 'treasury', 'yield']):
        return 'macro'
    if any(x in q for x in ['10-k', '10-q', '8-k', 'form 4', 'filing', 'insider', 'sec']):
        return 'filing'
    if any(x in q for x in ['news', 'latest', 'headline', 'breaking']):
        return 'news'
    return 'company'


async def run_search(query: str, symbol: str | None = None, mode: str = 'auto') -> SearchWarRoomResponse:
    resolved = classify_mode(query, mode)
    tasks = []

    # Always include DDG text + Wikipedia
    tasks.append(ddg.run(query))
    tasks.append(wikipedia.run(symbol or query))

    if resolved in {'company', 'filing'}:
        tasks.append(sec.run(query, symbol))
        tasks.append(yahoo.run(query, symbol))
        tasks.append(ddg.run_news(f"{symbol or query} stock news"))
    elif resolved == 'macro':
        tasks.append(fred.run(query))
        tasks.append(ddg.run_news(query))
    elif resolved == 'news':
        tasks.append(ddg.run_news(query))
        tasks.append(yahoo.run(query, symbol))
    else:
        tasks.append(ddg.run_news(query))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    normalized = []
    for item in results:
        if isinstance(item, Exception):
            logger.warning(f"Search engine exception: {item}")
            continue
        normalized.append(item)

    brief = build_brief(query, normalized)
    degraded = any(r.status in {'error', 'timeout'} for r in normalized)
    warnings = [f'{r.engine}: {r.error}' for r in normalized if r.error]

    return SearchWarRoomResponse(
        query=query,
        brief=brief,
        engine_results=[r.model_dump() for r in normalized],
        degraded=degraded,
        warnings=warnings[:8],
    )
