"""Search War Room — Orchestrator fires registered engines in parallel.

Uses the provider registry instead of hardcoded adapter imports.
AI analysis runs as a second phase after all engines complete.
"""
import asyncio
import logging
from services.search_war_room.schemas import SearchWarRoomResponse, EngineResult
from services.search_war_room.registry import get_enabled_providers, ProviderEntry
from services.search_war_room.adapters import ai_analysis
from services.search_war_room.synthesizer import build_brief

logger = logging.getLogger(__name__)

AI_ANALYSIS_TIMEOUT = 18.0


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


async def _run_provider(provider: ProviderEntry, query: str, symbol: str | None) -> EngineResult:
    """Execute a single provider with its configured timeout."""
    try:
        async with asyncio.timeout(provider.timeout):
            return await provider.run_fn(query, symbol)
    except asyncio.TimeoutError:
        return EngineResult(
            engine=provider.name, status="timeout", source_type=provider.source_type,
            query=query, summary=f"{provider.name} timed out", error="timeout",
        )
    except Exception as exc:
        return EngineResult(
            engine=provider.name, status="error", source_type=provider.source_type,
            query=query, summary=f"{provider.name} failed", error=str(exc)[:200],
        )


async def run_search(query: str, symbol: str | None = None, mode: str = "auto") -> SearchWarRoomResponse:
    resolved = classify_mode(query, mode)

    # Phase 1: Get all enabled providers for this mode and fire in parallel
    providers = get_enabled_providers(resolved, symbol)
    tasks = [asyncio.create_task(_run_provider(p, query, symbol)) for p in providers]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Normalize results
    normalized = []
    warnings = []
    degraded = False

    for provider, result in zip(providers, results):
        if not result.query:
            result.query = query
        if result.status in {"error", "timeout"}:
            warnings.append(f"{provider.name}: {result.error or result.status}")
            if provider.critical:
                degraded = True
        normalized.append(result)

    successful = [r for r in normalized if r.status in {"ok", "cached"}]

    # Phase 2: AI Analysis — synthesize all engine results through LLM
    try:
        async with asyncio.timeout(AI_ANALYSIS_TIMEOUT):
            ai_result = await ai_analysis.run(query, [r.model_dump() for r in normalized])
    except (asyncio.TimeoutError, Exception) as exc:
        ai_result = EngineResult(
            engine="ai_analysis", status="error", source_type="analysis",
            query=query, error=str(exc)[:200],
        )

    if ai_result.status == "ok":
        normalized.append(ai_result)

    brief = build_brief(query, normalized)

    # Upgrade brief with AI-generated content
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
    elif degraded:
        brief.risks = brief.risks + ["Some critical engines were unavailable; brief may be incomplete"]
    elif any(r.status in {"error", "timeout"} for r in normalized if r.engine != "ai_analysis"):
        brief.risks = brief.risks + ["Some non-critical engines were unavailable; fallback data used where possible"]

    return SearchWarRoomResponse(
        query=query,
        brief=brief,
        engine_results=_dedupe_engine_results([r.model_dump() for r in normalized]),
        degraded=degraded,
        warnings=warnings[:10],
    )


# Alias for backwards compatibility
run_war_room = run_search


def _dedupe_engine_results(results: list) -> list:
    """Deduplicate results across engines by URL."""
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
