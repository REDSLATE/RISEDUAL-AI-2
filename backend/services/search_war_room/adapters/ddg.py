"""DuckDuckGo adapter — text + news search with retry, semaphore, and stale cache fallback."""
import asyncio
import logging

from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)

from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

TTL = 300
_ddg_semaphore = asyncio.Semaphore(1)

try:
    from ddgs import DDGS
    from duckduckgo_search.exceptions import DuckDuckGoSearchException, RatelimitException
except Exception:
    DDGS = None
    DuckDuckGoSearchException = Exception
    RatelimitException = Exception


class RetryableDDGError(Exception):
    pass


def _normalize_items(results: list) -> list:
    normalized = []
    for item in results or []:
        normalized.append({
            "title": item.get("title"),
            "url": item.get("href") or item.get("url"),
            "snippet": item.get("body") or item.get("snippet"),
        })
    return normalized


def _sync_search(query: str, max_results: int = 5) -> list:
    if DDGS is None:
        raise RuntimeError("duckduckgo-search package not installed")
    with DDGS() as d:
        return list(d.text(query, max_results=max_results))


def _sync_news(query: str, max_results: int = 5) -> list:
    if DDGS is None:
        raise RuntimeError("duckduckgo-search package not installed")
    with DDGS() as d:
        return list(d.news(query, max_results=max_results))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=8),
    retry=retry_if_exception_type((RetryableDDGError, RatelimitException, DuckDuckGoSearchException)),
    reraise=True,
)
async def _search_with_retry(query: str, max_results: int = 5) -> list:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _sync_search, query, max_results)
    except RatelimitException:
        raise
    except DuckDuckGoSearchException as exc:
        msg = str(exc).lower()
        if "rate" in msg or "timeout" in msg or "202" in msg:
            raise RetryableDDGError(str(exc)) from exc
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ["timeout", "tempor", "connection", "reset"]):
            raise RetryableDDGError(str(exc)) from exc
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=8),
    retry=retry_if_exception_type((RetryableDDGError, RatelimitException, DuckDuckGoSearchException)),
    reraise=True,
)
async def _news_with_retry(query: str, max_results: int = 5) -> list:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _sync_news, query, max_results)
    except RatelimitException:
        raise
    except DuckDuckGoSearchException as exc:
        msg = str(exc).lower()
        if "rate" in msg or "timeout" in msg or "202" in msg:
            raise RetryableDDGError(str(exc)) from exc
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ["timeout", "tempor", "connection", "reset"]):
            raise RetryableDDGError(str(exc)) from exc
        raise


def _build_result(engine: str, source_type: str, query: str, items: list, cached: bool = False) -> EngineResult:
    summary = "No results found."
    if items:
        top_titles = [x["title"] for x in items[:3] if x.get("title")]
        if top_titles:
            summary = "Top results: " + " | ".join(top_titles)

    return EngineResult(
        engine=engine, status="ok", source_type=source_type, query=query,
        title=f"{engine} results for {query[:40]}", summary=summary,
        items=items, confidence=0.58 if items else 0.0,
        authoritative=False, cached=cached,
    )


def _stale_or_error(engine: str, source_type: str, query: str, exc: Exception) -> EngineResult:
    cached_stale = get_cached(engine, query, 86400)
    if cached_stale:
        data = dict(cached_stale)
        data["cached"] = True
        data["status"] = "cached"
        data["error"] = f"live_failed_using_stale_cache: {exc}"
        return EngineResult(**data)

    return EngineResult(
        engine=engine, status="error", source_type=source_type, query=query,
        summary=f"{engine} unavailable; continuing with other engines.",
        confidence=0.0, error=str(exc),
    )


async def run(query: str):
    cached = get_cached("ddg", query, TTL)
    if cached:
        data = dict(cached)
        data["cached"] = True
        if data.get("status") == "ok":
            data["status"] = "cached"
        return EngineResult(**data)

    async with _ddg_semaphore:
        try:
            results = await _search_with_retry(query, max_results=5)
            items = _normalize_items(results)
            result = _build_result("ddg", "search", query, items)
            set_cached("ddg", query, result.model_dump())
            return result
        except Exception as exc:
            return _stale_or_error("ddg", "search", query, exc)


async def run_news(query: str):
    cached = get_cached("ddg_news", query, TTL)
    if cached:
        data = dict(cached)
        data["cached"] = True
        if data.get("status") == "ok":
            data["status"] = "cached"
        return EngineResult(**data)

    async with _ddg_semaphore:
        try:
            results = await _news_with_retry(query, max_results=5)
            items = []
            for r in results or []:
                items.append({
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "source": r.get("source", ""),
                    "date": r.get("date", ""),
                    "snippet": r.get("body", ""),
                })
            result = _build_result("ddg_news", "news", query, items)
            set_cached("ddg_news", query, result.model_dump())
            return result
        except Exception as exc:
            return _stale_or_error("ddg_news", "news", query, exc)
