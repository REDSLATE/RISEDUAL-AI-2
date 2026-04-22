"""Web Intelligence Service — Unified web search for market research.

Uses DuckDuckGo (free, no API key) with multi-backend retry.
Provides ticker news, general research, and AI-context enrichment.
Results are cached briefly to reduce rate limit pressure.
"""
import logging
import asyncio
import time
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _subprocess_ddg_text(query: str, max_results: int = 5, timelimit: str = None) -> list:
    """Fallback: run DDG search in a subprocess to bypass in-process rate limits."""
    import subprocess
    import json as _json
    tl = f", timelimit='{timelimit}'" if timelimit else ""
    script = f"from ddgs import DDGS; import json; r=DDGS().text(query={repr(query)}, max_results={max_results}{tl}); print(json.dumps(r or []))"
    try:
        result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return _json.loads(result.stdout.strip())
    except Exception as e:
        logger.warning(f"DDG subprocess text fallback error: {e}")
    return []


def _subprocess_ddg_news(query: str, max_results: int = 5, timelimit: str = "w") -> list:
    """Fallback: run DDG news in a subprocess to bypass in-process rate limits."""
    import subprocess
    import json as _json
    tl = f", timelimit='{timelimit}'" if timelimit else ""
    script = f"from ddgs import DDGS; import json; r=DDGS().news(query={repr(query)}, max_results={max_results}{tl}); print(json.dumps(r or []))"
    try:
        result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return _json.loads(result.stdout.strip())
    except Exception as e:
        logger.warning(f"DDG subprocess news fallback error: {e}")
    return []



# Simple in-memory cache to avoid hammering DDG
_cache: dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> Optional[list[dict]]:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
        del _cache[key]
    return None


def _set_cache(key: str, data: list[dict]) -> None:
    _cache[key] = (time.time(), data)
    # Evict old entries
    if len(_cache) > 200:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]


async def _search_ddg(query: str, max_results: int = 10,
                      timelimit: Optional[str] = None) -> list[dict]:
    """Search via DuckDuckGo with backend fallback and caching."""
    cache_key = f"text:{query}:{max_results}:{timelimit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        from ddgs import DDGS

        def _do_search() -> list[dict]:
            # Use subprocess as primary — avoids long-running process rate limits
            raw = _subprocess_ddg_text(query, max_results, timelimit)
            if raw:
                return raw
            # In-process fallback
            for backend in ("auto", "html", "lite"):
                try:
                    r = DDGS().text(query=query, max_results=max_results,
                                    timelimit=timelimit, backend=backend)
                    if r:
                        return r
                except Exception:
                    continue
            return []

        raw = await asyncio.to_thread(_do_search)
        results = []
        for r in (raw or []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "content": r.get("body", ""),
                "score": 0,
                "source": "duckduckgo",
            })
        logger.info(f"DuckDuckGo: {len(results)} results for '{query[:50]}'")
        _set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.warning(f"DuckDuckGo search error: {e}")
        return []


async def _news_ddg(query: str, max_results: int = 10,
                    timelimit: Optional[str] = "w") -> list[dict]:
    """Fetch news via DuckDuckGo news endpoint with caching."""
    cache_key = f"news:{query}:{max_results}:{timelimit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        from ddgs import DDGS

        def _do_news() -> list[dict]:
            # Subprocess primary — avoids long-running process rate limits
            r = _subprocess_ddg_news(query, max_results, timelimit)
            if r:
                return r
            try:
                return DDGS().news(query=query, max_results=max_results, timelimit=timelimit) or []
            except Exception:
                return []

        raw = await asyncio.to_thread(_do_news)
        results = []
        for r in (raw or []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("body", ""),
                "date": r.get("date", ""),
                "source_name": r.get("source", ""),
                "source": "duckduckgo_news",
            })
        logger.info(f"DuckDuckGo News: {len(results)} results for '{query[:50]}'")
        _set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.warning(f"DuckDuckGo news error: {e}")
        return []


async def search(query: str, max_results: int = 10) -> dict:
    """Web search — returns structured results."""
    results = await _search_ddg(query, max_results)

    return {
        "query": query,
        "results": results,
        "count": len(results),
        "source": "duckduckgo",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def search_ticker_news(symbol: str, max_results: int = 10) -> dict:
    """Search for recent news about a specific stock/crypto ticker."""
    query = f"{symbol} stock market news latest"

    results = await _news_ddg(query, max_results, timelimit="w")
    source = "duckduckgo_news"

    if not results:
        results = await _search_ddg(query, max_results, timelimit="w")
        source = "duckduckgo"

    return {
        "symbol": symbol,
        "query": query,
        "results": results,
        "count": len(results),
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def research_topic(topic: str, max_results: int = 8) -> dict:
    """Deep research on a financial topic — returns AI-ready context."""
    results = await _search_ddg(topic, max_results)

    context_parts = []
    for r in results[:6]:
        snippet = r.get("content", "")[:300]
        if snippet:
            context_parts.append(f"[{r.get('title', '')}] {snippet}")
    context = "\n---\n".join(context_parts)

    return {
        "topic": topic,
        "results": results,
        "count": len(results),
        "context": context,
        "source": "duckduckgo",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def is_configured() -> dict:
    """Check which web search providers are available."""
    return {
        "duckduckgo": True,
    }
