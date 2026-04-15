"""Search War Room — Orchestrator fires all engines in parallel with per-engine timeouts."""
import asyncio
import logging
from services.search_war_room.schemas import SearchWarRoomResponse, EngineResult
from services.search_war_room.adapters import ddg, wikipedia, fred, sec, yahoo, ai_analysis, tavily
from services.search_war_room.adapters import av_news, finnhub_news
from services.search_war_room.synthesizer import build_brief

logger = logging.getLogger(__name__)

ENGINE_TIMEOUTS = {
    "wikipedia": 4.0,
    "fred": 6.0,
    "sec": 8.0,
    "ddg": 7.0,
    "ddg_news": 7.0,
    "yahoo": 5.0,
    "tavily": 10.0,
    "av_news": 12.0,
    "finnhub_news": 10.0,
    "ai_analysis": 18.0,
}

NON_CRITICAL_ENGINES = {"ddg", "ddg_news", "yahoo", "tavily", "av_news", "finnhub_news", "ai_analysis"}


def classify_mode(query: str, mode: str):
    if mode != "auto":
        return mode

    q = query.lower()
    if any(x in q for x in ["cpi", "gdp", "rates", "unemployment", "fed", "inflation", "treasury", "yield"]):
        return "macro"
    if any(x in q for x in ["10-k", "10-q", "8-k", "form 4", "filing", "insider", "sec"]):
        return "filing"
    if any(x in q for x in ["news", "headline", "breaking", "latest"]):
        return "news"
    return "company"


async def _run_engine(name: str, source_type: str, coro):
    try:
        async with asyncio.timeout(ENGINE_TIMEOUTS.get(name, 6.0)):
            return await coro
    except asyncio.TimeoutError:
        return EngineResult(
            engine=name, status="timeout", source_type=source_type, query="",
            summary=f"{name} timed out", confidence=0.0, error="timeout",
        )
    except Exception as exc:
        return EngineResult(
            engine=name, status="error", source_type=source_type, query="",
            summary=f"{name} failed", confidence=0.0, error=str(exc),
        )


async def run_search(query: str, symbol: str | None = None, mode: str = "auto") -> SearchWarRoomResponse:
    resolved = classify_mode(query, mode)

    engine_jobs = [
        ("wikipedia", "knowledge", wikipedia.run(symbol or query)),
    ]

    if resolved in {"company", "filing"}:
        engine_jobs.extend([
            ("sec", "filing", sec.run(query, symbol)),
            ("tavily", "search", tavily.run(f"{symbol or query} stock analysis financial")),
            ("av_news", "news", av_news.run(query, symbol)),
            ("finnhub_news", "news", finnhub_news.run(query, symbol)),
            ("ddg", "search", ddg.run(query)),
            ("yahoo", "market", yahoo.run(query, symbol)),
            ("ddg_news", "news", ddg.run_news(f"{symbol or query} stock news")),
        ])
    elif resolved == "macro":
        engine_jobs.extend([
            ("fred", "macro", fred.run(query)),
            ("tavily", "search", tavily.run(query)),
            ("ddg", "search", ddg.run(query)),
            ("ddg_news", "news", ddg.run_news(query)),
        ])
    elif resolved == "news":
        engine_jobs.extend([
            ("tavily", "search", tavily.run(query)),
            ("av_news", "news", av_news.run(query, symbol)),
            ("finnhub_news", "news", finnhub_news.run(query, symbol)),
            ("ddg", "search", ddg.run(query)),
            ("ddg_news", "news", ddg.run_news(query)),
            ("yahoo", "market", yahoo.run(query, symbol)),
        ])
    else:
        engine_jobs.extend([
            ("tavily", "search", tavily.run(query)),
            ("ddg", "search", ddg.run(query)),
        ])

    tasks = [asyncio.create_task(_run_engine(name, stype, coro)) for name, stype, coro in engine_jobs]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    normalized = []
    warnings = []
    degraded = False

    for (name, _, __), result in zip(engine_jobs, results):
        if not result.query:
            result.query = query

        if result.status in {"error", "timeout"}:
            warnings.append(f"{name}: {result.error or result.status}")
            degraded = True

        normalized.append(result)

    successful = [r for r in normalized if r.status in {"ok", "cached"}]
    critical_failed = any(
        r.status in {"error", "timeout"} and r.engine not in NON_CRITICAL_ENGINES
        for r in normalized
    )

    # Phase 2: AI Analysis — synthesize all engine results through LLM failover chain
    ai_result = await _run_engine(
        "ai_analysis", "analysis",
        ai_analysis.run(query, [r.model_dump() for r in normalized]),
    )
    if ai_result.status == "ok":
        normalized.append(ai_result)

    brief = build_brief(query, normalized)

    # If AI analysis succeeded, upgrade the brief with AI-generated content
    if ai_result.status == "ok" and ai_result.title:
        brief.headline = ai_result.title
        if ai_result.summary:
            brief.summary = ai_result.summary
        for item in ai_result.items:
            if isinstance(item, dict):
                if item.get("type") == "signals":
                    brief.signals = item["data"][:8]
                elif item.get("type") == "risks":
                    brief.risks = item["data"][:5]

    if not successful:
        brief.summary = "All live engines failed or were unavailable. No useful research results were returned."
        brief.risks = brief.risks + ["No engines returned usable data"]
        degraded = True
    elif critical_failed:
        brief.risks = brief.risks + ["Some critical engines were unavailable; brief may be incomplete"]
    elif degraded:
        brief.risks = brief.risks + ["Some non-critical engines were unavailable; fallback data used where possible"]

    return SearchWarRoomResponse(
        query=query,
        brief=brief,
        engine_results=_dedupe_engine_results([r.model_dump() for r in normalized]),
        degraded=degraded,
        warnings=warnings[:10],
    )


def _dedupe_engine_results(results: list) -> list:
    """Deduplicate results across engines by URL (from artifact pattern)."""
    seen_urls = set()
    deduped = []
    for r in results:
        items = r.get("items", [])
        if isinstance(items, list):
            unique_items = []
            for item in items:
                url = item.get("url", "") if isinstance(item, dict) else ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                unique_items.append(item)
            r["items"] = unique_items
        deduped.append(r)
    return deduped
