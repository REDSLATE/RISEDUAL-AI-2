"""AI Analysis adapter — multi-provider failover for Search War Room synthesis.

Failover chain: Groq -> OpenRouter -> Emergent LLM (litellm)
Each provider supports multiple rotating keys.
"""
import os
import logging
import httpx
from typing import Optional
from services.key_rotator import KeyRotator
from services.search_war_room.schemas import EngineResult

logger = logging.getLogger(__name__)

groq_rotator = KeyRotator("GROQ_API_KEYS")
openrouter_rotator = KeyRotator("OPENROUTER_API_KEYS")

GROQ_MODEL = "llama-3.3-70b-versatile"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"

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


async def _try_groq(prompt: str) -> Optional[dict]:
    key = groq_rotator.get()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code == 200:
                groq_rotator.mark_success(key)
                data = resp.json()
                import json
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            else:
                groq_rotator.mark_failed(key)
                logger.warning(f"Groq API {resp.status_code}: {resp.text[:200]}")
                return None
    except Exception as e:
        groq_rotator.mark_failed(key)
        logger.warning(f"Groq failed: {e}")
        return None


async def _try_openrouter(prompt: str) -> Optional[dict]:
    key = openrouter_rotator.get()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://risedual.ai",
                    "X-Title": "RISEDUAL AI War Room",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 600,
                },
            )
            if resp.status_code == 200:
                openrouter_rotator.mark_success(key)
                data = resp.json()
                import json
                content = data["choices"][0]["message"]["content"]
                # OpenRouter may not enforce JSON mode, so try to parse
                # Strip markdown fences if present
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                return json.loads(content)
            else:
                openrouter_rotator.mark_failed(key)
                logger.warning(f"OpenRouter API {resp.status_code}: {resp.text[:200]}")
                return None
    except Exception as e:
        openrouter_rotator.mark_failed(key)
        logger.warning(f"OpenRouter failed: {e}")
        return None


async def _try_emergent(prompt: str) -> Optional[dict]:
    """Fallback to Emergent LLM Key via emergentintegrations."""
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import json as json_mod
        chat = LlmChat(
            api_key=key,
            session_id=f"war_room_ai_{id(prompt)}",
            system_message=SYSTEM_PROMPT,
        ).with_model("openai", "gpt-4o-mini")
        response = await chat.send_message(UserMessage(text=prompt))
        content = response.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        return json_mod.loads(content)
    except Exception as e:
        logger.warning(f"Emergent LLM fallback failed: {e}")
        return None


async def run(query: str, engine_results: list) -> EngineResult:
    """Run AI analysis across all search results with multi-provider failover."""
    prompt = _build_user_prompt(query, engine_results)

    providers = [
        ("groq", _try_groq),
        ("openrouter", _try_openrouter),
        ("emergent", _try_emergent),
    ]

    for provider_name, fn in providers:
        result = await fn(prompt)
        if result:
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

    return EngineResult(
        engine="ai_analysis",
        status="skipped",
        source_type="analysis",
        query=query,
        error="all_providers_exhausted",
        summary="AI analysis unavailable — no configured providers responded.",
    )


def status() -> dict:
    return {
        "groq": groq_rotator.status(),
        "openrouter": openrouter_rotator.status(),
        "emergent": bool(os.environ.get("EMERGENT_LLM_KEY")),
    }
