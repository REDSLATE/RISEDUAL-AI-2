"""FastAPI adapter shims for risedual_core.

Provides factory helpers that produce FastAPI dependency callables for the
shared ``LLMRouter`` and market data clients.

Dependencies produced here are designed to be used with
:func:`fastapi.Depends` and follow the generator pattern so that resources
can be cleaned up properly at the end of each request (or at application
shutdown for cached singletons).

Typical usage::

    from risedual_core.adapters.cli import build_llm_router, build_market_clients
    from risedual_core.adapters.fastapi import (
        make_llm_router_dependency,
        make_market_client_dependency,
    )
    from risedual_cli.clients.finnhub_client import FinnhubClient
    from fastapi import APIRouter, Depends

    router = APIRouter()

    get_llm_router = make_llm_router_dependency(
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        openai_api_key=settings.OPENAI_API_KEY,
        default_provider=settings.DEFAULT_PROVIDER,
    )

    get_finnhub = make_market_client_dependency(
        FinnhubClient(api_key=settings.FINNHUB_API_KEY)
    )

    @router.post("/predict")
    async def predict(llm=Depends(get_llm_router)):
        response = await llm.chat([{"role": "user", "content": "Hello"}])
        return {"text": response.content}

    @router.get("/quote")
    async def quote(ticker: str, finnhub=Depends(get_finnhub)):
        return await finnhub.quote(ticker)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from risedual_core.adapters.cli import BaseMarketClient, build_llm_router

logger = logging.getLogger(__name__)


# ── LLM router dependency ─────────────────────────────────────────────────────


def make_llm_router_dependency(
    anthropic_api_key: str,
    openai_api_key: str,
    anthropic_model: str = "claude-sonnet-4-20250514",
    openai_model: str = "gpt-4o",
    default_provider: str = "anthropic",
) -> Callable[[], Any]:
    """Return a FastAPI dependency that yields a shared :class:`ProviderRouter`.

    The router is constructed once (at dependency creation time) and then
    yielded on every request — it is a shared singleton.  The dependency
    function is synchronous so that FastAPI does not need to manage an async
    generator for the happy path.

    Parameters
    ----------
    anthropic_api_key:
        Anthropic API key.  Provider is omitted when empty.
    openai_api_key:
        OpenAI API key.  Provider is omitted when empty.
    anthropic_model:
        Anthropic model identifier (default: ``"claude-sonnet-4-20250514"``).
    openai_model:
        OpenAI model identifier (default: ``"gpt-4o"``).
    default_provider:
        Preferred provider name (``"anthropic"`` or ``"openai"``).

    Returns
    -------
    Callable[[], ProviderRouter]
        A zero-argument callable suitable for ``Depends(get_llm_router)``.

    Raises
    ------
    ValueError
        If no API keys are provided (raised at call time, not at import time).

    Example
    -------
    ::

        get_llm_router = make_llm_router_dependency(
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            openai_api_key=settings.OPENAI_API_KEY,
        )

        @router.post("/predict")
        async def predict(llm: LLMRouter = Depends(get_llm_router)):
            result = await llm.chat([{"role": "user", "content": "analyse AAPL"}])
            return {"text": result.content}
    """
    # Build the router eagerly at dependency-creation time.  This surfaces
    # configuration errors (missing keys, etc.) at startup rather than on the
    # first request.
    _router: Any = build_llm_router(
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        anthropic_model=anthropic_model,
        openai_model=openai_model,
        default_provider=default_provider,
    )
    logger.info(
        "FastAPI LLMRouter dependency created (providers=%s, default=%r).",
        _router.available_providers(),
        _router.get_active_provider(),
    )

    def get_llm_router() -> Any:
        """FastAPI dependency: yield the shared LLMRouter singleton."""
        return _router

    # Preserve a readable name for FastAPI's dependency visualisation
    get_llm_router.__name__ = "get_llm_router"
    get_llm_router.__qualname__ = "get_llm_router"

    return get_llm_router


# ── Market client dependency ──────────────────────────────────────────────────


def make_market_client_dependency(
    client: BaseMarketClient,
) -> Callable[[], BaseMarketClient]:
    """Return a FastAPI dependency that yields the given market client.

    The client is a singleton — the same instance is returned on every
    request.  Callers are responsible for constructing the client (and
    configuring its API key) before passing it here.

    Parameters
    ----------
    client:
        A pre-constructed market client instance implementing
        :class:`~risedual_core.adapters.cli.BaseMarketClient`.

    Returns
    -------
    Callable[[], BaseMarketClient]
        A zero-argument callable suitable for ``Depends(get_client)``.

    Example
    -------
    ::

        get_finnhub = make_market_client_dependency(
            FinnhubClient(api_key=settings.FINNHUB_API_KEY)
        )

        @router.get("/quote/{ticker}")
        async def quote(ticker: str, finnhub=Depends(get_finnhub)):
            return await finnhub.quote(ticker)
    """

    def get_client() -> BaseMarketClient:
        """FastAPI dependency: yield the shared market client singleton."""
        return client

    # Use the client's name if available for clearer FastAPI docs
    client_name = getattr(client, "name", type(client).__name__)
    dep_name = f"get_{client_name}_client"
    get_client.__name__ = dep_name
    get_client.__qualname__ = dep_name

    logger.debug("FastAPI market client dependency created for %r.", client_name)
    return get_client


# ── Async lifespan helper ─────────────────────────────────────────────────────


async def close_market_clients(clients: dict[str, BaseMarketClient]) -> None:
    """Close all market clients that expose an async ``close()`` method.

    Call this inside a FastAPI ``lifespan`` context manager at shutdown to
    release pooled HTTP connections cleanly.

    Parameters
    ----------
    clients:
        Dict of market clients as returned by
        :func:`~risedual_core.adapters.cli.build_market_clients`.

    Example
    -------
    ::

        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from risedual_core.adapters.cli import build_market_clients
        from risedual_core.adapters.fastapi import close_market_clients

        market_clients = build_market_clients(...)

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            await close_market_clients(market_clients)

        app = FastAPI(lifespan=lifespan)
    """
    for name, client in clients.items():
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            try:
                await close_fn()
                logger.debug("Closed market client %r.", name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing market client %r: %s", name, exc)
