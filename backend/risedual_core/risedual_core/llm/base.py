"""Abstract base types for LLM provider implementations.

All concrete providers (Anthropic, OpenAI, …) must subclass :class:`LLMProvider`
and implement the abstract interface defined here.  Health tracking with a
configurable cooldown window is provided by the base class and shared by all
implementations.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

# ── Constants ─────────────────────────────────────────────────────────────────

_UNHEALTHY_COOLDOWN_SECONDS: int = 60

# ── Data models ───────────────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """A single tool/function call requested by the LLM.

    Attributes
    ----------
    id:
        Unique identifier for this tool call (provider-assigned).
    name:
        Name of the tool to invoke (must match a registered tool name).
    arguments:
        Parsed JSON arguments for the tool as a plain dict.
    """

    id: str = Field(description="Unique identifier for this tool call.")
    name: str = Field(description="Name of the tool to invoke.")
    arguments: dict = Field(
        default_factory=dict,
        description="Parsed JSON arguments for the tool.",
    )


class LLMResponse(BaseModel):
    """Normalised response returned by every LLM provider.

    Provides a common schema regardless of which backend (Anthropic, OpenAI,
    etc.) produced the response.

    Attributes
    ----------
    content:
        Text content of the assistant turn, or ``None`` when the model only
        produced tool calls.
    tool_calls:
        Tool calls requested by the model in this turn.
    raw:
        Raw API response payload serialised to a plain dict.
    provider:
        Name of the provider that produced this response (e.g. ``"anthropic"``).
    model:
        Model identifier used for this response (e.g. ``"claude-3-5-sonnet-20241022"``).
    """

    content: str | None = Field(
        default=None,
        description="Text content of the assistant turn, if any.",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="Tool calls requested by the model in this turn.",
    )
    raw: dict = Field(
        default_factory=dict,
        description="Raw API response payload (serialised to dict).",
    )
    provider: str = Field(description="Name of the provider that produced this response.")
    model: str = Field(description="Model identifier used for this response.")


# ── Abstract base class ───────────────────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract base class for all LLM backend providers.

    Concrete subclasses must implement :meth:`chat` and the :attr:`name`
    property.  Health tracking (60-second cooldown logic) is provided by
    this base class and shared by all implementations.
    """

    def __init__(self) -> None:
        self._unhealthy_since: float | None = None

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the LLM backend.

        Parameters
        ----------
        messages:
            A list of message dicts following the OpenAI message schema
            (``{"role": "...", "content": "..."}``).  System messages are
            supported; concrete providers handle mapping to their native format.
        tools:
            Optional list of tool definitions in a generic JSON-schema format.
            Providers are responsible for converting these to their native
            function/tool schema.

        Returns
        -------
        LLMResponse
            Normalised response including text content and any tool calls.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name that uniquely identifies this provider instance."""
        ...

    # ── Health tracking ───────────────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        """Return ``True`` when the provider is available for requests.

        A provider is considered unhealthy while it is within its cooldown
        window (``_UNHEALTHY_COOLDOWN_SECONDS`` after the last failure).
        Once the cooldown expires the provider is automatically restored to
        healthy status so the router will retry it on the next request.
        """
        if self._unhealthy_since is None:
            return True
        elapsed = time.monotonic() - self._unhealthy_since
        if elapsed >= _UNHEALTHY_COOLDOWN_SECONDS:
            self._unhealthy_since = None
            return True
        return False

    def mark_unhealthy(self) -> None:
        """Mark this provider as unhealthy, starting the 60-second cooldown timer."""
        self._unhealthy_since = time.monotonic()

    def mark_healthy(self) -> None:
        """Clear any unhealthy state, making the provider immediately available."""
        self._unhealthy_since = None
