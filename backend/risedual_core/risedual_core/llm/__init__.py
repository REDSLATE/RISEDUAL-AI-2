"""LLM provider abstraction layer for risedual_core.

Re-exports all public LLM types for convenient top-level imports::

    from risedual_core.llm import LLMRouter, AnthropicLLM, OpenAILLM
    from risedual_core.llm import LLMProvider, LLMResponse, ToolCall
"""
from __future__ import annotations

from risedual_core.llm.anthropic import AnthropicLLM
from risedual_core.llm.base import LLMProvider, LLMResponse, ToolCall
from risedual_core.llm.openai import OpenAILLM
from risedual_core.llm.router import LLMRouter

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "AnthropicLLM",
    "OpenAILLM",
    "LLMRouter",
]
