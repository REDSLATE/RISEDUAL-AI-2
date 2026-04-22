"""AI Provider Pool — Priority-based LLM failover across multiple providers.

Pool chain (configurable via AI_PROVIDER_POOL env var):
  Emergent GPT-5.2 → OpenAI GPT-4.1 → Anthropic Claude Sonnet 4

Usage:
    from services.ai_pool import ai_pool, ai_complete
    result = await ai_complete(system_msg, user_msg, json_mode=True)
"""
import os
import json as json_mod
import logging
from typing import Any

import httpx

from services.provider_pool import ProviderPool, ProviderEntry
from services.pool_config import get_ai_provider_pool

logger = logging.getLogger(__name__)

# Singleton pool — assembled from pool_config
ai_pool = ProviderPool(get_ai_provider_pool(), name="AI_PROVIDER_POOL")


async def _call_emergent(provider: ProviderEntry, system_msg: str, user_msg: str,
                         temperature: float, max_tokens: int, json_mode: bool) -> str:
    """Call via emergentintegrations LlmChat."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    chat = LlmChat(
        api_key=provider.api_key,
        session_id=f"pool_{provider.name}_{id(user_msg)}",
        system_message=system_msg,
    ).with_model(provider.provider, provider.model)

    response = await chat.send_message(UserMessage(text=user_msg))
    if not response or not response.strip():
        raise ValueError(f"Empty response from {provider.name}")
    return response.strip()


async def _call_openai_direct(provider: ProviderEntry, system_msg: str, user_msg: str,
                              temperature: float, max_tokens: int, json_mode: bool) -> str:
    """Call OpenAI API directly via httpx."""
    body = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise ValueError(f"Empty content from {provider.name}")
        return content.strip()


async def _call_anthropic_direct(provider: ProviderEntry, system_msg: str, user_msg: str,
                                 temperature: float, max_tokens: int, json_mode: bool) -> str:
    """Call Anthropic Messages API directly via httpx."""
    body = {
        "model": provider.model,
        "max_tokens": max_tokens,
        "system": system_msg,
        "messages": [{"role": "user", "content": user_msg}],
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        blocks = data.get("content", [])
        text_parts = [b["text"] for b in blocks if b.get("type") == "text"]
        content = "\n".join(text_parts)
        if not content:
            raise ValueError(f"Empty content from {provider.name}")
        return content.strip()


async def _call_openrouter(provider: ProviderEntry, system_msg: str, user_msg: str,
                           temperature: float, max_tokens: int, json_mode: bool) -> str:
    """Call OpenRouter — OpenAI-compatible endpoint with custom headers."""
    body = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://risedual.ai",
                "X-Title": "RISEDUAL AI",
            },
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise ValueError(f"Empty content from {provider.name}")
        return content.strip()


# Dispatch map: provider type → call function
_DISPATCH = {
    "emergent": _call_emergent,
    "openai_direct": _call_openai_direct,
    "anthropic": _call_anthropic_direct,
    "openrouter": _call_openrouter,
}


def _get_caller(provider: ProviderEntry) -> Any:
    """Determine which caller to use based on provider config."""
    if provider.api_key.startswith("sk-emergent"):
        return _call_emergent
    if provider.provider == "openrouter":
        return _call_openrouter
    if provider.provider == "anthropic":
        return _call_anthropic_direct
    if provider.provider == "openai":
        return _call_openai_direct
    return _call_emergent


async def ai_complete(
    system_msg: str,
    user_msg: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    json_mode: bool = False,
) -> str:
    """Execute an LLM completion with automatic provider failover.

    Returns the raw text response from the first successful provider.
    Raises RuntimeError if all providers fail.
    """
    if not ai_pool.available:
        # Fallback: try Emergent LLM key directly
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if key:
            logger.info("[AI Pool] No pool configured, falling back to EMERGENT_LLM_KEY")
            fallback = ProviderEntry(
                name="emergent-fallback", provider="openai",
                api_key=key, model="gpt-4o-mini", priority=99,
            )
            return await _call_emergent(fallback, system_msg, user_msg, temperature, max_tokens, json_mode)
        raise RuntimeError("No AI providers configured")

    async def _execute(provider: ProviderEntry) -> str:
        caller = _get_caller(provider)
        return await caller(provider, system_msg, user_msg, temperature, max_tokens, json_mode)

    return await ai_pool.execute(_execute)


async def ai_complete_json(system_msg: str, user_msg: str, **kwargs) -> dict:
    """Like ai_complete but parses the response as JSON."""
    raw = await ai_complete(system_msg, user_msg, json_mode=True, **kwargs)
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    return json_mod.loads(raw)


def ai_pool_status() -> dict:
    """Get current AI pool health status."""
    return ai_pool.status()
