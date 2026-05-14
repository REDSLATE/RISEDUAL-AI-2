"""SSE streaming hypothesis endpoint.

Wall-clock time is unchanged from the regular hypothesis route —
the bottleneck is the LLM, not our orchestration. What changes is
**perceived** speed:

  - Single-brain mode: emits ``status`` events as the brain progresses
    (gathering data → invoking LLM → parsing → done) so the UI never
    looks frozen.

  - Consensus mode: kicks off all 4 brains in parallel and emits
    ``brain_done`` events as each one returns, so users see their
    brains debating live instead of staring at a 14s spinner.

Doctrine:
  - Cache hit fast-path: if the result is already in L1 or Mongo,
    emit one ``done`` event and close. No new LLM call.
  - SSE only — no WebSocket. Browser EventSource handles it natively.
  - Pro-gating mirrors the JSON route exactly.

Event schema (each line is a separate SSE event):
    event: status      data: {"stage": "...", "message": "..."}
    event: brain_done  data: {"brain": "alpha", "verdict": "...", "confidence": ...}
    event: done        data: <full hypothesis object>
    event: error       data: {"error": "...", "code": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from services.ai_cache_service import AICacheService
from services.brain_persona_service import BRAINS
from services.llm_response_cache import hypothesis_l1_cache
from services.multi_model_hypothesis_service import (
    MODELS,
    _build_prompt,
    _run_single_model,
    _weighted_consensus,
    generate_hypothesis,
)

logger = logging.getLogger("hypothesis_stream")
router = APIRouter(prefix="/api/hypothesis", tags=["hypothesis-stream"])

VALID_MODELS = {*BRAINS.keys(), "consensus"}


def _sse(event: str, payload: Any) -> dict[str, str]:
    """Format an SSE event. EventSourceResponse takes ``{event, data}`` dicts."""
    return {"event": event, "data": json.dumps(payload, default=str)}


async def _gather_market_data(db, symbol: str) -> dict:
    """Reuse the same data-gather path the JSON route does, with a
    safe fallback so a slow/failing data source can't strand the
    stream."""
    try:
        from routes.market_data import _collect_all_scrape_data
        return await _collect_all_scrape_data()
    except Exception as e:  # noqa: BLE001
        logger.warning("[stream] data gather failed for %s: %s", symbol, e)
        return {"symbol": symbol, "warning": f"data gather failed: {e}"}


async def _stream_single(
    *,
    api_key: str,
    db,
    symbol: str,
    model: str,
    cache: AICacheService,
    cache_key: str,
) -> AsyncIterator[dict[str, str]]:
    """Stream events for a single-brain run."""
    yield _sse("status", {"stage": "data", "message": f"Gathering market data for {symbol}…"})
    data = await _gather_market_data(db, symbol)
    prompt = _build_prompt(symbol, data)
    brand = BRAINS[model]["label"]
    yield _sse("status", {"stage": "brain", "message": f"{brand} forming thesis…"})

    t0 = time.monotonic()
    result = await _run_single_model(api_key, model, symbol, prompt)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    result["is_pro"] = True
    result["elapsed_ms"] = elapsed_ms

    yield _sse("brain_done", {
        "brain": model, "label": brand,
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "elapsed_ms": elapsed_ms,
    })

    # Persist (both layers).
    try:
        await cache.set(
            cache_key=cache_key, namespace="hypothesis",
            data=result, ttl_seconds=600,
            meta={"symbol": symbol.upper(), "model": model},
        )
        hypothesis_l1_cache.set(cache_key, result, ttl_seconds=60)
    except Exception as e:  # noqa: BLE001 — cache errors shouldn't fail the stream
        logger.debug("[stream] cache set failed (non-critical): %s", e)

    yield _sse("done", result)


async def _stream_consensus(
    *,
    api_key: str,
    db,
    symbol: str,
    cache: AICacheService,
    cache_key: str,
) -> AsyncIterator[dict[str, str]]:
    """Stream events for the 4-brain consensus run."""
    yield _sse("status", {"stage": "data", "message": f"Gathering market data for {symbol}…"})
    data = await _gather_market_data(db, symbol)
    prompt = _build_prompt(symbol, data)

    yield _sse("status", {
        "stage": "consensus_kickoff",
        "message": "Convening Alpha 1.6, Camaro 1.3, Chevelle 1.3, RedEye 1.3…",
        "brains": list(MODELS.keys()),
    })

    # Fire all 4 in parallel. Emit a ``brain_done`` as each completes.
    keys = list(MODELS.keys())
    pending = {
        asyncio.create_task(_run_single_model(api_key, k, symbol, prompt)): k
        for k in keys
    }
    results: list[dict] = []
    t0 = time.monotonic()

    while pending:
        done, _ = await asyncio.wait(
            pending.keys(), return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            brain_key = pending.pop(task)
            try:
                res = task.result()
            except Exception as e:  # noqa: BLE001
                logger.warning("[stream] %s crashed: %s", brain_key, e)
                res = {
                    "model": BRAINS[brain_key]["label"], "model_key": brain_key,
                    "symbol": symbol.upper(), "verdict": "ERROR",
                    "confidence": 0, "summary": str(e)[:160],
                    "catalysts": [], "risks": [], "error": True,
                }
            results.append(res)
            yield _sse("brain_done", {
                "brain": brain_key,
                "label": BRAINS[brain_key]["label"],
                "verdict": res.get("verdict"),
                "confidence": res.get("confidence"),
                "summary": (res.get("summary") or "")[:280],
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "error": bool(res.get("error")),
            })
            # Force a context switch so SSE flushes each event individually
            # instead of coalescing tight back-to-back yields.
            await asyncio.sleep(0)

    yield _sse("status", {"stage": "consensus_synth",
                          "message": "Synthesizing 4-brain consensus…"})

    consensus = _weighted_consensus(results)
    consensus["symbol"] = symbol.upper()
    consensus["model"] = "Consensus"
    consensus["model_key"] = "consensus"
    consensus["individual_results"] = [
        {"model": r["model"], "model_key": r["model_key"],
         "verdict": r.get("verdict", "ERROR"),
         "confidence": r.get("confidence", 0),
         "summary": r.get("summary", ""),
         "error": r.get("error", False)} for r in results
    ]

    try:
        await cache.set(
            cache_key=cache_key, namespace="hypothesis",
            data=consensus, ttl_seconds=600,
            meta={"symbol": symbol.upper(), "model": "consensus"},
        )
        hypothesis_l1_cache.set(cache_key, consensus, ttl_seconds=60)
    except Exception as e:  # noqa: BLE001
        logger.debug("[stream] cache set failed (non-critical): %s", e)

    yield _sse("done", consensus)


async def _event_iterator(
    request: Request, db, symbol: str, model: str, is_pro: bool,
) -> AsyncIterator[dict[str, str]]:
    cache = AICacheService(db)
    cache_key = cache.build_key(
        "hypothesis", symbol=symbol.upper(), model=model,
    )

    # Cache hot-path: emit one done event and close.
    if not request.query_params.get("force_refresh") == "true":
        l1_hit = hypothesis_l1_cache.get(cache_key)
        if l1_hit is not None:
            l1_hit_payload = {**l1_hit, "_cache": {"hit": True, "layer": "l1"}}
            yield _sse("status", {"stage": "cache", "message": "Cached result available"})
            yield _sse("done", l1_hit_payload)
            return
        mongo_hit = await cache.get(cache_key)
        if mongo_hit:
            mongo_hit["_cache"] = {"hit": True, "layer": "mongo"}
            hypothesis_l1_cache.set(cache_key, mongo_hit, ttl_seconds=60)
            yield _sse("status", {"stage": "cache", "message": "Cached result available"})
            yield _sse("done", mongo_hit)
            return

    # Pro gating mirrors the JSON route exactly.
    if not is_pro and model != "alpha":
        yield _sse("error", {
            "error": "Premium AI brains require Pro. Free users can use Alpha 1.6.",
            "code": "premium_required",
        })
        return

    # Pull the Emergent LLM key. If missing → friendly error.
    from os import environ
    api_key = environ.get("EMERGENT_LLM_KEY") or environ.get("OPENAI_API_KEY")
    if not api_key:
        yield _sse("error", {
            "error": "AI service unavailable (no API key configured)",
            "code": "no_api_key",
        })
        return

    if model == "consensus":
        async for evt in _stream_consensus(
            api_key=api_key, db=db, symbol=symbol,
            cache=cache, cache_key=cache_key,
        ):
            yield evt
    else:
        async for evt in _stream_single(
            api_key=api_key, db=db, symbol=symbol, model=model,
            cache=cache, cache_key=cache_key,
        ):
            yield evt


@router.get("/{symbol}/stream")
async def get_hypothesis_stream(
    symbol: str, request: Request, model: str = "alpha",
):
    """SSE endpoint streaming progressive hypothesis events."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise HTTPException(400, detail="symbol required")
    if model not in VALID_MODELS:
        model = "alpha"

    db = request.app.state.db
    # Mirror the JSON route's pro gate exactly.
    try:
        from routes.ai import get_optional_user, is_pro_user
        user = await get_optional_user(request)
        is_pro = is_pro_user(user)
    except Exception:
        is_pro = model == "alpha"

    return EventSourceResponse(
        _event_iterator(request, db, symbol, model, is_pro),
        ping=15,  # keep-alive every 15s for slow LLM calls
    )


def set_db(_db) -> None:
    """Compat shim for the route registry."""
    return
