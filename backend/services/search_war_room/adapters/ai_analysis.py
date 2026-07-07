"""AI Analysis adapter — uses AI Provider Pool for Search War Room synthesis.

Failover chain is configured via AI_PROVIDER_POOL env var.
Falls back to Emergent LLM key if no pool is configured.
"""
import logging
from services.search_war_room.schemas import EngineResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior financial analyst in the RISEDUAL AI War Room.
Given raw search engine results, produce a concise intelligence brief:
1. HEADLINE: One punchy sentence summarizing the most actionable finding.
2. SUMMARY: 2-3 sentences synthesizing key data across sources.
3. SIGNALS: Up to 5 bullet points of actionable signals (bullish/bearish/neutral).
4. RISKS: Up to 3 risk factors or data gaps.
Respond in valid JSON: {"headline":"...","summary":"...","signals":["..."],"risks":["..."]}"""


def _build_user_prompt(query: str, engine_results: list) -> str:
    parts = [f"QUERY: {query}\n\nENGINE RESULTS:"]
    for r in engine_results:
        if r.get("status") not in ("ok", "cached"):
            continue
        engine = r.get("engine", "unknown")
        summary = r.get("summary", "")
        items = r.get("items", [])[:3]
        parts.append(f"\n[{engine.upper()}] {summary}")
        for item in items:
            if isinstance(item, dict):
                parts.append(f"  - {item}")
    return "\n".join(parts)


async def run(query: str, engine_results: list) -> EngineResult:
    """Run AI analysis across all search results with multi-provider failover."""
    prompt = _build_user_prompt(query, engine_results)

    try:
        from services.ai_pool import ai_complete_json, ai_pool
        result = await ai_complete_json(SYSTEM_PROMPT, prompt, temperature=0.3, max_tokens=400)

        # Determine which provider was used
        providers = ai_pool.get_healthy_providers()
        provider_name = providers[0].name if providers else "emergent-fallback"

        return EngineResult(
            engine=f"ai_analysis:{provider_name}",
            status="ok",
            source_type="analysis",
            query=query,
            title=result.get("headline", "AI Analysis"),
            summary=result.get("summary", ""),
            items=[
                {"type": "signals", "data": result.get("signals", [])},
                {"type": "risks", "data": result.get("risks", [])},
            ],
            confidence=0.85,
            authoritative=False,
            cached=False,
        )
    except Exception as e:
        logger.warning(f"AI analysis failed: {e}")
        return EngineResult(
            engine="ai_analysis",
            status="skipped",
            source_type="analysis",
            query=query,
            error=str(e)[:200],
            summary="AI analysis unavailable — all providers exhausted.",
        )


def status() -> dict:
    try:
        from services.ai_pool import ai_pool_status
        return ai_pool_status()
    except Exception:
        return {"error": "AI pool not initialized"}
