"""LLM Fallback Wrapper (2026-05-22, P1 Layer 2).

The Emergent LLM key is a shared-budget facility. When the prod
budget runs dry, every brain in the council fails simultaneously
and the Hypothesis tab goes dark. This module lets operators add
direct provider API keys as a fallback:

    OPENAI_API_KEY=sk-...           # falls back here for openai
    ANTHROPIC_API_KEY=sk-ant-...    # falls back here for anthropic
    GEMINI_API_KEY=AIza...          # falls back here for gemini

Doctrine
--------
* The Emergent LLM key (via ``emergentintegrations.LlmChat``) is
  STILL the primary — fallback only fires on detected
  budget/quota errors.
* The fallback uses official provider SDKs directly so we don't
  inherit the Emergent key's shared budget.
* Fallback failures bubble up — the brain returns its existing
  ERROR shape and the consensus filter drops the vote.
* Detection is conservative: only error strings that clearly
  indicate budget exhaustion trigger fallback. A transient 500
  is NOT a fallback trigger (we'd waste the BYO key on a hiccup).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# Conservative substrings — only fire fallback when the error
# message includes one of these. Provider error wrappers
# (litellm in particular) preserve the original budget keyword.
_BUDGET_HINTS = (
    "budget",
    "quota",
    "insufficient_quota",
    "rate_limit_exceeded",
    "exceeded your current",
    "litellm.budgetexceedederror",
    "billing_hard_limit_reached",
)


def is_budget_error(exc: BaseException) -> bool:
    """Heuristic: does this exception look like budget exhaustion?"""
    if exc is None:
        return False
    msg = str(exc).lower()
    return any(hint in msg for hint in _BUDGET_HINTS)


def has_direct_key(provider: str) -> bool:
    """True iff the operator has set a direct API key for the
    named provider. Provider strings match BRAINS['provider']."""
    p = (provider or "").lower()
    if p == "openai":
        return bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    if p == "anthropic":
        return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    if p == "gemini":
        return bool((os.environ.get("GEMINI_API_KEY") or "").strip())
    return False


async def call_direct_openai(
    *, model: str, system_message: str, user_message: str,
) -> str:
    """Direct OpenAI Chat Completions call. Returns the response
    text. Raises on any failure (caller falls through to ERROR)."""
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set for fallback")
    try:
        # Imported lazily so absence of openai SDK never breaks
        # the rest of the service (Emergent key path doesn't use it).
        from openai import AsyncOpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"openai SDK not installed: {exc}") from exc
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
    )
    return resp.choices[0].message.content or ""


async def call_direct_anthropic(
    *, model: str, system_message: str, user_message: str,
) -> str:
    """Direct Anthropic Messages call."""
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set for fallback")
    try:
        from anthropic import AsyncAnthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"anthropic SDK not installed: {exc}") from exc
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_message,
        messages=[{"role": "user", "content": user_message}],
    )
    # Anthropic returns a list of content blocks; we want the text.
    parts: list[str] = []
    for block in resp.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def call_direct_gemini(
    *, model: str, system_message: str, user_message: str,
) -> str:
    """Direct Google Gemini call via the google-generativeai SDK."""
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set for fallback")
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"google-generativeai not installed: {exc}") from exc
    genai.configure(api_key=api_key)
    full_prompt = f"{system_message}\n\n{user_message}"
    model_obj = genai.GenerativeModel(model)
    # google-generativeai uses sync; wrap to await semantics.
    import asyncio
    resp = await asyncio.to_thread(model_obj.generate_content, full_prompt)
    return getattr(resp, "text", "") or ""


async def call_direct(
    *, provider: str, model: str, system_message: str, user_message: str,
) -> str:
    """Provider-router for the direct fallback call."""
    p = (provider or "").lower()
    if p == "openai":
        return await call_direct_openai(
            model=model, system_message=system_message,
            user_message=user_message,
        )
    if p == "anthropic":
        return await call_direct_anthropic(
            model=model, system_message=system_message,
            user_message=user_message,
        )
    if p == "gemini":
        return await call_direct_gemini(
            model=model, system_message=system_message,
            user_message=user_message,
        )
    raise RuntimeError(f"no direct-fallback adapter for provider {provider!r}")


async def call_with_emergent_then_fallback(
    *, emergent_send, provider: str, model: str,
    system_message: str, user_message: str,
) -> str:
    """Try ``emergent_send()`` first (a 0-arg coroutine that returns
    the response text). On a budget/quota error AND a configured
    direct provider key, retry via :func:`call_direct`. Any other
    exception bubbles up so callers see the honest error path.

    This helper exists so ``multi_model_hypothesis_service`` can
    keep its body lean and stay under the preferred-ceiling
    governance bar (CI invariant
    ``test_no_new_preferred_ceiling_breaches``).
    """
    try:
        return await emergent_send()
    except Exception as primary_exc:
        if is_budget_error(primary_exc) and has_direct_key(provider):
            logger.warning(
                "[llm_fallback] primary budget exhausted for %s/%s; "
                "retrying via direct provider key",
                provider, model,
            )
            return await call_direct(
                provider=provider, model=model,
                system_message=system_message, user_message=user_message,
            )
        raise


__all__ = [
    "is_budget_error",
    "has_direct_key",
    "call_direct",
    "call_direct_openai",
    "call_direct_anthropic",
    "call_direct_gemini",
    "call_with_emergent_then_fallback",
]
