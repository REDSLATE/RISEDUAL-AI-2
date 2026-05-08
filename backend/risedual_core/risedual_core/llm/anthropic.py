"""Anthropic Claude LLM provider implementation.

Wraps the official ``anthropic`` async client and maps its response format to
the normalised :class:`~risedual_core.llm.base.LLMResponse` schema.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from risedual_core.llm.base import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


# ── Conversion helpers ────────────────────────────────────────────────────────


def _convert_tools_to_anthropic(tools: list[dict]) -> list[dict[str, Any]]:
    """Convert generic tool definitions to Anthropic's tool schema format.

    The generic format mirrors the OpenAI function-calling schema:

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

    Anthropic uses ``input_schema`` instead of ``parameters``.

    Parameters
    ----------
    tools:
        List of tool definitions in the generic schema format.

    Returns
    -------
    list[dict]
        Tool definitions in Anthropic's native format.
    """
    anthropic_tools: list[dict[str, Any]] = []
    for tool in tools:
        anthropic_tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
        })
    return anthropic_tools


def _extract_system_and_messages(
    messages: list[dict],
) -> tuple[str, list[dict[str, Any]]]:
    """Split a message list into a system prompt and the remaining messages.

    Anthropic requires the system prompt to be passed as a top-level ``system``
    parameter rather than as a message in the conversation list.

    Parameters
    ----------
    messages:
        Conversation history in OpenAI-compatible format.

    Returns
    -------
    tuple[str, list[dict]]
        ``(system_prompt, non_system_messages)`` where ``system_prompt`` is an
        empty string when no system message is present.
    """
    system_parts: list[str] = []
    other_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        system_parts.append(block.get("text", ""))
        else:
            other_messages.append(msg)

    return "\n\n".join(system_parts), other_messages


def _parse_response_content(
    response: Any,
) -> tuple[str | None, list[ToolCall]]:
    """Extract text content and tool calls from an Anthropic message response.

    Iterates over ``response.content`` blocks and separates ``text`` blocks
    from ``tool_use`` blocks.  Tool input is JSON-decoded when the API returns
    it as a string rather than a pre-parsed dict.

    Parameters
    ----------
    response:
        A response object returned by
        :meth:`anthropic.AsyncAnthropic.messages.create`.

    Returns
    -------
    tuple[str | None, list[ToolCall]]
        ``(text_content, tool_calls)`` where ``text_content`` is ``None``
        when no text block is present.
    """
    text_content: str | None = None
    tool_calls: list[ToolCall] = []

    for block in response.content:
        if block.type == "text":
            text_content = (text_content or "") + block.text
        elif block.type == "tool_use":
            raw_input = block.input
            if isinstance(raw_input, str):
                try:
                    parsed_input: dict[str, Any] = json.loads(raw_input)
                except json.JSONDecodeError:
                    parsed_input = {"raw": raw_input}
            else:
                parsed_input = raw_input or {}

            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=parsed_input,
                )
            )

    return text_content, tool_calls


# ── Provider class ────────────────────────────────────────────────────────────


class AnthropicLLM(LLMProvider):
    """LLM provider backed by Anthropic Claude models.

    Parameters
    ----------
    api_key:
        Anthropic API key.  If empty the client falls back to the
        ``ANTHROPIC_API_KEY`` environment variable.
    model:
        Anthropic model identifier (e.g. ``"claude-3-5-sonnet-20241022"``).
    """

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__()
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)
        self._consecutive_failures: int = 0

    # ── LLMProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Return the provider name."""
        return "anthropic"

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send a chat request to Anthropic and return a normalised response.

        System messages are extracted and passed via the ``system`` parameter;
        remaining messages are forwarded in the ``messages`` list.  Tool
        definitions are converted from the generic schema to Anthropic's
        ``input_schema`` format.

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
        anthropic.APIError
            Propagated after marking the provider unhealthy when consecutive
            failures reach the threshold.
        """
        system_prompt, user_messages = _extract_system_and_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": user_messages,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = _convert_tools_to_anthropic(tools)

        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            self._consecutive_failures += 1
            logger.error(
                "Anthropic API error (failure #%d): %s",
                self._consecutive_failures,
                exc,
            )
            if self._consecutive_failures >= 2:
                logger.warning("Marking Anthropic provider unhealthy after repeated failures.")
                self.mark_unhealthy()
            raise

        # Reset failure counter on success
        self._consecutive_failures = 0

        text_content, tool_calls = _parse_response_content(response)

        raw_dict: dict[str, Any] = {
            "id": response.id,
            "type": response.type,
            "role": response.role,
            "model": response.model,
            "stop_reason": response.stop_reason,
            "stop_sequence": response.stop_sequence,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            raw=raw_dict,
            provider=self.name,
            model=response.model,
        )
