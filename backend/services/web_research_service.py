"""Web Research Service — Tavily + LLM stance classifier (SHADOW MODE).

Why this exists
---------------
The crypto bot already knows the "what" (price, RSI, EMA, momentum) from the
structured indicator pipeline. It does NOT know the "why" — was this rip
fueled by a Bitcoin ETF inflow headline or by a thin-liquidity weekend pump?

This service provides narrative context by:

1. Querying **Tavily** (finance topic, advanced depth) for recent news /
   macro / catalysts on the symbol.
2. Asking a small LLM to classify the resulting headlines into a single
   ``stance`` (BULLISH / BEARISH / NEUTRAL / UNKNOWN) with a one-line
   rationale.

The verdict is then logged into ``crypto_signal_audit_log.web_research_shadow_verdict``
and `crypto_paper_trades.web_research_shadow_verdict` (when a fill happens).

Architectural rule (SHADOW ONLY)
--------------------------------
The verdict MUST NOT influence ``signal["direction"]`` or
``signal["confidence"]`` in the live trade path. It is collected purely so
that the team can later evaluate whether LLM-summarised macro context
statistically improves expectancy — without contaminating the 100-trade
observation baseline.

All failures (Tavily HTTP errors, LLM provider faults, JSON parse fails)
are swallowed and surfaced as ``stance="UNKNOWN"`` so the live bot path is
never affected.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Tavily constants (mirror services/search_war_room/adapters/tavily.py).
_TAVILY_URL = "https://api.tavily.com/search"
_TAVILY_TIMEOUT_S = 12.0
_TAVILY_MAX_RESULTS = 5

# LLM stance-classifier — kept tiny + cheap.
_LLM_TIMEOUT_S = 15.0
_LLM_MODEL = "gpt-4o-mini"
_LLM_PROVIDER = "openai"

_VALID_STANCES = {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"}


def _build_query(symbol: str) -> str:
    """Compact finance-topic query that biases toward 24h catalysts."""
    sym = symbol.upper()
    return (
        f"{sym} crypto price news catalysts last 24 hours macro impact"
    )


async def _run_tavily(query: str) -> dict[str, Any]:
    """Call Tavily directly. Returns ``{answer, items, error}``."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return {"answer": "", "items": [], "error": "missing_tavily_api_key"}

    try:
        async with httpx.AsyncClient(timeout=_TAVILY_TIMEOUT_S) as client:
            resp = await client.post(
                _TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": _TAVILY_MAX_RESULTS,
                    "include_answer": True,
                    "topic": "finance",
                },
            )
        if resp.status_code != 200:
            return {
                "answer": "",
                "items": [],
                "error": f"http_{resp.status_code}",
            }
        data = resp.json()
        items = [
            {
                "title": (r.get("title") or "")[:160],
                "url": r.get("url") or "",
                "snippet": (r.get("content") or "")[:280],
            }
            for r in (data.get("results") or [])[:_TAVILY_MAX_RESULTS]
        ]
        return {
            "answer": (data.get("answer") or "")[:600],
            "items": items,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"answer": "", "items": [], "error": str(exc)[:120]}


def _parse_stance_json(raw: str) -> dict[str, Any]:
    """Tolerant JSON parser — strips code fences and finds first {...}."""
    if not raw:
        return {"stance": "UNKNOWN", "rationale": "empty_llm_response"}
    txt = raw.strip()
    if txt.startswith("```"):
        # Drop the opening fence (and optional ``json`` tag).
        parts = txt.split("```", 2)
        if len(parts) >= 2:
            txt = parts[1]
            if txt.lstrip().lower().startswith("json"):
                txt = txt.lstrip()[4:].strip()
    match = re.search(r"\{.*\}", txt, re.DOTALL)
    if not match:
        return {"stance": "UNKNOWN", "rationale": "no_json_block"}
    try:
        obj = json.loads(match.group(0))
    except Exception as exc:  # noqa: BLE001
        return {"stance": "UNKNOWN", "rationale": f"json_parse_error:{str(exc)[:60]}"}

    stance = str(obj.get("stance") or "").upper().strip()
    if stance not in _VALID_STANCES:
        stance = "UNKNOWN"
    rationale = str(obj.get("rationale") or "")[:240]
    try:
        confidence = float(obj.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {"stance": stance, "rationale": rationale, "confidence": confidence}


async def _classify_stance(
    symbol: str,
    tavily_payload: dict[str, Any],
) -> dict[str, Any]:
    """Run LLM stance classifier over the Tavily payload.

    Returns ``{stance, rationale, confidence}``. Never raises — all
    failures degrade to ``stance="UNKNOWN"``.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("UNIVERSAL_LLM_KEY")
    if not api_key:
        return {"stance": "UNKNOWN", "rationale": "missing_llm_key", "confidence": 0.0}

    answer = tavily_payload.get("answer") or ""
    items = tavily_payload.get("items") or []

    if not answer and not items:
        return {"stance": "UNKNOWN", "rationale": "no_research_content", "confidence": 0.0}

    headlines = "\n".join(
        f"- {it.get('title','')}: {it.get('snippet','')}"
        for it in items[:4]
    )
    prompt = (
        f"Symbol: {symbol.upper()}\n\n"
        f"Tavily answer:\n{answer or '(none)'}\n\n"
        f"Top headlines:\n{headlines or '(none)'}\n\n"
        "Classify the NET narrative stance for this crypto over the last 24h. "
        "Respond with a single JSON object (no prose, no code fences) with keys: "
        "\"stance\" (BULLISH | BEARISH | NEUTRAL | UNKNOWN), "
        "\"rationale\" (one short sentence, <= 200 chars), "
        "\"confidence\" (0.0-1.0)."
    )
    system = (
        "You are a crypto news classifier. Read the provided headlines and answer with "
        "a strict JSON object capturing the net narrative stance. Be conservative — if "
        "headlines are mixed or off-topic, return NEUTRAL or UNKNOWN with low confidence."
    )

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        session_id = f"crypto-shadow-{symbol.upper()}-{int(time.time()) // 600}"
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message=system,
        ).with_model(_LLM_PROVIDER, _LLM_MODEL)
        resp = await chat.send_message(UserMessage(text=prompt))
        return _parse_stance_json(str(resp))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[web_research] LLM classifier failed for %s: %s", symbol, exc)
        return {
            "stance": "UNKNOWN",
            "rationale": f"llm_error:{str(exc)[:80]}",
            "confidence": 0.0,
        }


def _agreement(direction: Optional[str], stance: str) -> str:
    """Derive a coarse agreement label between the trade direction
    and the LLM stance. Logged so a later analysis can compute
    expectancy conditional on agreement."""
    d = (direction or "").upper()
    s = (stance or "").upper()
    if d not in ("LONG", "SHORT"):
        return "n/a"
    if s in ("UNKNOWN", "NEUTRAL"):
        return "neutral"
    if (d == "LONG" and s == "BULLISH") or (d == "SHORT" and s == "BEARISH"):
        return "agree"
    if (d == "LONG" and s == "BEARISH") or (d == "SHORT" and s == "BULLISH"):
        return "disagree"
    return "neutral"


async def get_shadow_verdict(
    symbol: str,
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Build a shadow web-research verdict for ``symbol``.

    Always returns a dict (never None, never raises). Callers attach
    the returned dict to ``signal["web_research_shadow_verdict"]`` for
    downstream logging — but MUST NOT use it to alter direction or
    confidence. SHADOW ONLY.
    """
    started = time.time()
    query = _build_query(symbol)
    tavily = await _run_tavily(query)

    if tavily.get("error"):
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "stance": "UNKNOWN",
            "rationale": f"tavily_error:{tavily['error']}",
            "confidence": 0.0,
            "agreement": "n/a",
            "sources": [],
            "tavily_answer": "",
            "query": query,
            "elapsed_ms": elapsed_ms,
            "cached": False,
            "shadow_only": True,
            "error": tavily["error"],
        }

    classified = await _classify_stance(symbol, tavily)
    elapsed_ms = int((time.time() - started) * 1000)

    return {
        "stance": classified["stance"],
        "rationale": classified["rationale"],
        "confidence": classified["confidence"],
        "agreement": _agreement(signal.get("direction"), classified["stance"]),
        "sources": [
            {"title": it.get("title", ""), "url": it.get("url", "")}
            for it in (tavily.get("items") or [])[:3]
        ],
        "tavily_answer": tavily.get("answer", ""),
        "query": query,
        "elapsed_ms": elapsed_ms,
        "cached": False,
        "shadow_only": True,
        "error": None,
    }
