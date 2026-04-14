"""Search War Room — Orchestrator fires all engines in parallel with per-engine timeouts."""
import asyncio
import logging
from services.search_war_room.schemas import SearchWarRoomResponse, EngineResult
from services.search_war_room.adapters import ddg, wikipedia, fred, sec, yahoo
from services.search_war_room.synthesizer import build_brief

logger = logging.getLogger(__name__)

ENGINE_TIMEOUTS = {
    "wikipedia": 4.0,
    "fred": 6.0,
    "sec": 8.0,
    "ddg": 7.0,
    "ddg_news": 7.0,
    "yahoo": 5.0,
}

NON_CRITICAL_ENGINES = {"ddg", "ddg_news", "yahoo"}


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


async def _run_engine(name: str, coro):
    try:
        async with asyncio.timeout(ENGINE_TIMEOUTS.get(name, 6.0)):
            result = await coro
            return result
    except asyncio.TimeoutError:
        return EngineResult(
            engine=name,
            status="timeout",
            source_type="unknown",
            query="",
            summary=f"{name} timed out",
            confidence=0.0,
            authoritative=False,
            cached=False,
            error="timeout",
        )
    except Exception as exc:
        return EngineResult(
            engine=name,
            status="error",
            source_type="unknown",
            query="",
            summary=f"{name} failed",
            confidence=0.0,
            authoritative=False,
            cached=False,
            error=str(exc),
        )


async def run_search(query: str, symbol: str | None = None, mode: str = "auto") -> SearchWarRoomResponse:
    resolved = classify_mode(query, mode)

    engine_jobs = [
        ("wikipedia", wikipedia.run(symbol or query)),
    ]

    if resolved in {"company", "filing"}:
        engine_jobs.extend([
            ("sec", sec.run(query, symbol)),
            ("ddg", ddg.run(query)),
            ("yahoo", yahoo.run(query, symbol)),
            ("ddg_news", ddg.run_news(f"{symbol or query} stock news")),
        ])
    elif resolved == "macro":
        engine_jobs.extend([
            ("fred", fred.run(query)),
            ("ddg", ddg.run(query)),
            ("ddg_news", ddg.run_news(query)),
        ])
    elif resolved == "news":
        engine_jobs.extend([
            ("ddg", ddg.run(query)),
            ("ddg_news", ddg.run_news(query)),
            ("yahoo", yahoo.run(query, symbol)),
        ])
    else:
        engine_jobs.append(("ddg", ddg.run(query)))

    tasks = [asyncio.create_task(_run_engine(name, coro)) for name, coro in engine_jobs]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    normalized = []
    warnings = []
    degraded = False

    for (name, _), result in zip(engine_jobs, results):
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

    brief = build_brief(query, normalized)

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
        engine_results=[r.model_dump() for r in normalized],
        degraded=degraded,
        warnings=warnings[:10],
    )
