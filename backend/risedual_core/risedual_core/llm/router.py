"""LLM router with automatic health-aware failover.

:class:`LLMRouter` manages a pool of :class:`~risedual_core.llm.base.LLMProvider`
instances and transparently falls back to healthy alternatives when the
preferred provider is unavailable or returns an error.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from risedual_core.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class LLMRouter:
    """Routes LLM requests across multiple providers with health-based failover.

    The router attempts providers in the following priority order:

    1. The *active* (default) provider, if it is currently healthy.
    2. All remaining healthy providers, in the order they were supplied.

    On a successful call the used provider is marked healthy.  On failure it is
    marked unhealthy (60-second cooldown) and the next candidate is tried.
    If every provider fails, a :class:`RuntimeError` is raised.

    Parameters
    ----------
    providers:
        Ordered list of :class:`LLMProvider` instances to manage.
    default_provider_name:
        Name of the provider to prefer for each request.  Must match the
        :attr:`~risedual_core.llm.base.LLMProvider.name` of one of the supplied
        providers.

    Raises
    ------
    ValueError
        If *providers* is empty or *default_provider_name* is not found.
    """

    def __init__(
        self,
        providers: list[LLMProvider],
        default_provider_name: str,
    ) -> None:
        if not providers:
            raise ValueError("At least one provider must be supplied to LLMRouter.")

        self._providers: dict[str, LLMProvider] = {p.name: p for p in providers}
        self._default_name: str = default_provider_name
        self._active_name: str = default_provider_name

        if default_provider_name not in self._providers:
            raise ValueError(
                f"Default provider {default_provider_name!r} is not in the "
                f"supplied provider list: {list(self._providers)}"
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_active_provider(self) -> str:
        """Return the name of the provider that will be tried first on the next request."""
        return self._active_name

    def set_active(self, name: str) -> None:
        """Switch the default and active provider by name.

        Parameters
        ----------
        name:
            Must match the :attr:`~risedual_core.llm.base.LLMProvider.name`
            of one of the providers supplied at construction time.

        Raises
        ------
        ValueError
            If *name* does not correspond to a registered provider.
        """
        if name not in self._providers:
            available = ", ".join(sorted(self._providers))
            raise ValueError(
                f"Unknown provider {name!r}. Available: {available}"
            )
        self._default_name = name
        self._active_name = name

    def available_providers(self) -> list[str]:
        """Return the names of all registered providers."""
        return list(self._providers)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a chat request, failing over to healthy providers as needed.

        Parameters
        ----------
        messages:
            Conversation history passed through to the underlying provider.
        tools:
            Optional tool definitions passed through to the underlying provider.

        Returns
        -------
        LLMResponse
            The first successful response from any available provider.

        Raises
        ------
        RuntimeError
            When all providers are either unhealthy or return errors.
        """
        ordered = self._build_ordered_candidates()
        last_exc: Exception | None = None

        for provider in ordered:
            if not provider.is_healthy:
                logger.debug("Skipping unhealthy provider '%s'.", provider.name)
                continue

            if provider.name != self._active_name:
                _log_provider_switch(self._active_name, provider.name)
                self._active_name = provider.name

            try:
                response = await provider.chat(messages, tools)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.error(
                    "Provider '%s' raised an error: %s. Trying next candidate.",
                    provider.name,
                    exc,
                )
                provider.mark_unhealthy()
                # Restore active name to default so the next iteration
                # can switch cleanly.
                self._active_name = self._default_name
                continue

            provider.mark_healthy()
            self._active_name = provider.name
            return response

        raise RuntimeError(
            "All LLM providers are unavailable or returned errors. "
            f"Last error: {last_exc}"
        ) from last_exc

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_ordered_candidates(self) -> list[LLMProvider]:
        """Return providers ordered with the default provider first.

        If the default provider is currently unhealthy it is still placed first
        (the caller skips it based on the ``is_healthy`` check) so that it gets
        retried as soon as its cooldown expires.
        """
        default = self._providers.get(self._default_name)
        others = [p for name, p in self._providers.items() if name != self._default_name]

        if default is None:
            return others

        return [default, *others]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _log_provider_switch(from_name: str, to_name: str) -> None:
    """Emit a provider-switch notice to stderr and the Python logger."""
    message = f"[risedual] LLM provider switch: {from_name!r} → {to_name!r}"
    print(message, file=sys.stderr)
    logger.warning("LLM provider switch: %r → %r", from_name, to_name)
