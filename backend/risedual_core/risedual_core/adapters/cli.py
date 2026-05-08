"""CLI adapter shims for risedual_core.

Provides factory helpers that wire ``risedual_core`` primitives into the
``risedual-cli`` surface layer.  The CLI should call these helpers during
startup — passing settings from :class:`~risedual_cli.config.Settings` — and
then treat the returned objects as shared singletons for the lifetime of the
process.

Market clients are imported directly from ``risedual_core.clients`` — the
single source of truth.  LLM providers are still lazily imported from
``risedual_cli`` (those concrete classes are CLI-surface concerns), so the
core library retains zero hard dependencies on the CLI package.

Typical usage::

    from risedual_core.adapters.cli import build_llm_router, build_market_clients

    router = build_llm_router(
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        openai_api_key=settings.OPENAI_API_KEY,
        default_provider=settings.DEFAULT_PROVIDER,
    )

    clients = build_market_clients(
        finnhub_key=settings.FINNHUB_API_KEY,
        alpha_vantage_key=settings.ALPHA_VANTAGE_API_KEY,
        fred_key=settings.FRED_API_KEY,
        fmp_key=settings.FMP_API_KEY,
    )
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient
from risedual_core.clients.alpha_vantage import AlphaVantageClient
from risedual_core.clients.finnhub import FinnhubClient
from risedual_core.clients.fred import FredClient
from risedual_core.clients.fmp import FMPClient

logger = logging.getLogger(__name__)


# ── LLMRouter type alias ──────────────────────────────────────────────────────
# LLM provider classes are CLI-surface concerns (they carry heavy optional deps).
# We accept them as Any here and resolve to the concrete ProviderRouter at call
# sites via lazy import.

LLMRouter = Any  # structural alias; resolved to ProviderRouter at call sites


# ── Factory functions ─────────────────────────────────────────────────────────


def build_llm_router(
    anthropic_api_key: str = "",
    openai_api_key: str = "",
    anthropic_model: str = "claude-sonnet-4-20250514",
    openai_model: str = "gpt-4o",
    default_provider: str = "anthropic",
) -> Any:
    """Construct a :class:`~risedual_cli.providers.router.ProviderRouter` from config values.

    Only creates provider instances for non-empty API keys.  If the
    ``default_provider`` is not among the configured providers, the function
    falls back to the first available provider automatically.

    Parameters
    ----------
    anthropic_api_key:
        Anthropic API key string.  Provider is skipped when empty.
    openai_api_key:
        OpenAI API key string.  Provider is skipped when empty.
    anthropic_model:
        Anthropic model identifier (default: ``"claude-sonnet-4-20250514"``).
    openai_model:
        OpenAI model identifier (default: ``"gpt-4o"``).
    default_provider:
        Name of the preferred provider (``"anthropic"`` or ``"openai"``).
        If the preferred provider has no API key, the first available
        provider is used instead.

    Returns
    -------
    ProviderRouter
        A configured router ready for ``await router.chat(messages)`` calls.

    Raises
    ------
    ValueError
        If no API keys are provided (router would have zero providers).
    ImportError
        If ``risedual_cli`` is not installed in the current environment.
    """
    try:
        from risedual_cli.providers.anthropic_provider import AnthropicProvider  # noqa: PLC0415
        from risedual_cli.providers.openai_provider import OpenAIProvider  # noqa: PLC0415
        from risedual_cli.providers.router import ProviderRouter  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "risedual_cli must be installed to use build_llm_router. "
            "Install it with: pip install -e path/to/risedual-cli"
        ) from exc

    providers = []

    if anthropic_api_key:
        providers.append(AnthropicProvider(api_key=anthropic_api_key, model=anthropic_model))
        logger.debug("Registered Anthropic provider (model=%s).", anthropic_model)

    if openai_api_key:
        providers.append(OpenAIProvider(api_key=openai_api_key, model=openai_model))
        logger.debug("Registered OpenAI provider (model=%s).", openai_model)

    if not providers:
        raise ValueError(
            "No LLM providers could be configured. "
            "Set at least one of: ANTHROPIC_API_KEY, OPENAI_API_KEY."
        )

    available_names = [p.name for p in providers]

    # Fall back to the first available provider if the default isn't configured
    if default_provider not in available_names:
        original = default_provider
        default_provider = available_names[0]
        logger.warning(
            "Default provider %r is not configured; falling back to %r.",
            original,
            default_provider,
        )

    router = ProviderRouter(providers=providers, default_provider_name=default_provider)
    logger.info(
        "LLM router created with providers=%s, default=%r.",
        available_names,
        default_provider,
    )
    return router


def build_market_clients(
    finnhub_key: str = "",
    alpha_vantage_key: str = "",
    fred_key: str = "",
    fmp_key: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, BaseMarketClient]:
    """Return a dict of instantiated market data clients for non-empty keys.

    Clients are sourced directly from ``risedual_core.clients`` — no
    dependency on ``risedual_cli`` is introduced here.  Only clients with a
    non-empty API key are included in the returned dict.  The optional
    ``http_client`` is a shared :class:`httpx.AsyncClient`; when provided all
    created clients share it (the caller manages its lifecycle).

    Parameters
    ----------
    finnhub_key:
        Finnhub API key.  Client included when non-empty.
    alpha_vantage_key:
        Alpha Vantage API key.  Client included when non-empty.
    fred_key:
        FRED API key.  Client included when non-empty.
    fmp_key:
        Financial Modeling Prep API key.  Client included when non-empty.
    http_client:
        Shared :class:`httpx.AsyncClient` to pass into every client.  When
        ``None`` each client manages its own connection lifecycle.

    Returns
    -------
    dict[str, BaseMarketClient]
        Mapping of ``"finnhub"``, ``"alpha_vantage"``, ``"fred"``, ``"fmp"``
        to the corresponding client instance.  Only keys with non-empty API
        keys are present.
    """
    clients: dict[str, BaseMarketClient] = {}

    if finnhub_key:
        clients["finnhub"] = FinnhubClient(
            api_key=finnhub_key, http_client=http_client
        )
        logger.debug("Registered FinnhubClient.")

    if alpha_vantage_key:
        clients["alpha_vantage"] = AlphaVantageClient(
            api_key=alpha_vantage_key, http_client=http_client
        )
        logger.debug("Registered AlphaVantageClient.")

    if fred_key:
        clients["fred"] = FredClient(
            api_key=fred_key, http_client=http_client
        )
        logger.debug("Registered FredClient.")

    if fmp_key:
        clients["fmp"] = FMPClient(
            api_key=fmp_key, http_client=http_client
        )
        logger.debug("Registered FMPClient.")

    logger.info("Market clients configured: %s", list(clients.keys()))
    return clients
