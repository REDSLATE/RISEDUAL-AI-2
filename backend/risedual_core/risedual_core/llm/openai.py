"""OpenAI GPT LLM provider implementation.

Wraps the official ``openai`` async client and maps its response format to
the normalised :class:`~risedual_core.llm.base.LLMResponse` schema.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import openai

from risedual_core.llm.base import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


# ── Conversion helpers ────────────────────────────────────────────────────────


def _convert_tools_to_openai(tools: list[dict]) -> list[dict[str, Any]]:
    """Convert generic tool definitions to OpenAI's function-calling format.

    The generic format:

    .. code-block:: json

        {
            "name": "get_price",
            "description": "Fetch the current price of a ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock symbol."}
                },
                "required": ["ticker"]
            }
        }

    OpenAI wraps each tool in a ``{"type": "function", "function": {...}}``
    envelope.

    Parameters
    ----------
    tools:
        List of tool definitions in the generic schema format.

    Returns
    -------
    list[dict]
        Tool definitions in OpenAI's native format.
    """
    openai_tools: list[dict[str, Any]] = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get(
                    "parameters",
                    {"type": "object", "properties": {}},
                ),
            },
        })
    return openai_tools


# ── Provider class ────────────────────────────────────────────────────────────


class OpenAILLM(LLMProvider):
    """LLM provider backed by OpenAI GPT models.

    Parameters
    ----------
    api_key:
        OpenAI API key.  If empty the client falls back to the
        ``OPENAI_API_KEY`` environment variable.
    model:
        OpenAI model identifier (e.g. ``"gpt-4o"``).
    """

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__()
        self._model = model
        self._client = openai.AsyncOpenAI(api_key=api_key or None)
        self._consecutive_failures: int = 0

    # ── LLMProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Return the provider name."""
        return "openai"

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send a chat request to OpenAI and return a normalised response.

        System messages are forwarded as-is (OpenAI natively supports them in
        the messages list).  Tool definitions are converted from the generic
        schema to OpenAI's function-calling format.

        Parameters
        ----------
        messages:
            Conversation history in OpenAI-compatible format.
        tools:
            Optional tool definitions in the generic schema format.

        Returns
        -------
        LLMResponse
            Normalised response with extracted text content and tool calls.

        Raises
        ------
        openai.OpenAIError
            Propagated after marking the provider unhealthy when consecutive
            failures reach the threshold.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = _convert_tools_to_openai(tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            self._consecutive_failures += 1
            logger.error(
                "OpenAI API error (failure #%d): %s",
                self._consecutive_failures,
                exc,
            )
            if self._consecutive_failures >= 2:
                logger.warning("Marking OpenAI provider unhealthy after repeated failures.")
                self.mark_unhealthy()
            raise

        # Reset failure counter on success
        self._consecutive_failures = 0

        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LLMResponse:
        """Normalise an OpenAI ``ChatCompletion`` into an ``LLMResponse``.

        Extracts the first-choice message content and tool calls, then builds
        the generic response shape shared by all provider adapters.
        """
        choice = response.choices[0]
        message = choice.message

        text_content: str | None = message.content

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                raw_args = tc.function.arguments
                try:
                    parsed_args: dict[str, Any] = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    parsed_args = {"raw": raw_args}

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=parsed_args,
                    )
                )

        usage = response.usage
        raw_dict: dict[str, Any] = {
            "id": response.id,
            "object": response.object,
            "model": response.model,
            "finish_reason": choice.finish_reason,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None,
            },
        }

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            raw=raw_dict,
            provider=self.name,
            model=response.model,
        )
