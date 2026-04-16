"""Tool registry framework for risedual_core.

Provides a central store for AI-callable tool definitions and a dispatcher
that executes the correct async handler by name.

Surface-specific tools (CLI tools, web API tools) register against the shared
:data:`tool_registry` singleton in their own modules — this module contains
the framework only, with no market tools pre-registered.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any


# Type alias for async tool handler functions.
Handler = Callable[..., Coroutine[Any, Any, Any]]


class ToolRegistry:
    """Registry that stores tool definitions and dispatches execution.

    Usage
    -----
    Register a tool with the :meth:`register` method or use it as a
    decorator factory::

        @tool_registry.register(
            name="my_tool",
            description="Does something useful",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        )
        async def _my_tool_handler(query: str) -> str:
            return f"Result for {query}"

    Retrieve all schemas for an LLM call::

        schemas = tool_registry.get_schemas()

    Dispatch a tool call returned by the LLM::

        result = await tool_registry.execute("my_tool", {"query": "hello"})
    """

    def __init__(self) -> None:
        # Maps tool name → {"name", "description", "parameters", "handler"}
        self._tools: dict[str, dict[str, Any]] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Handler | None = None,
    ) -> Any:
        """Register a tool.

        Can be called directly with a *handler* function::

            tool_registry.register(
                name="...", description="...", parameters={...}, handler=my_fn
            )

        Or used as a decorator factory (omit *handler*)::

            @tool_registry.register(name="...", description="...", parameters={...})
            async def my_fn(...): ...

        Parameters
        ----------
        name:
            Unique tool identifier used by the LLM.  Must be unique across
            all registered tools.
        description:
            Human-readable description of the tool's purpose and when to use it.
        parameters:
            JSON Schema object describing the accepted arguments (``type``,
            ``properties``, ``required``).
        handler:
            Async callable that implements the tool.  When ``None``, returns a
            decorator that accepts the handler function.

        Returns
        -------
        Handler
            The handler function (either directly or via the decorator path),
            so the decorated function remains usable in the module scope.
        """
        def _store(fn: Handler) -> Handler:
            self._tools[name] = {
                "name": name,
                "description": description,
                "parameters": parameters,
                "handler": fn,
            }
            return fn

        if handler is not None:
            _store(handler)
            return handler

        # Decorator factory path
        return _store

    # ── Schema access ─────────────────────────────────────────────────────────

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return all tool schemas in a generic JSON-compatible format.

        Each schema dict has the form::

            {
                "name": str,
                "description": str,
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...],
                },
            }

        This format is compatible with OpenAI, Anthropic, and similar LLM
        tool-calling APIs.

        Returns
        -------
        list[dict]
            One schema dict per registered tool.
        """
        return [
            {
                "name": entry["name"],
                "description": entry["description"],
                "parameters": entry["parameters"],
            }
            for entry in self._tools.values()
        ]

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute the tool identified by *name* with *arguments*.

        Parameters
        ----------
        name:
            Tool name as registered.
        arguments:
            Key/value arguments matching the tool's parameter schema.

        Returns
        -------
        str
            A string representation of the tool's result.  Dicts and lists
            are JSON-serialised; strings are returned as-is; all other types
            are converted via :func:`str`.

        Raises
        ------
        KeyError
            If *name* is not registered in the registry.
        """
        if name not in self._tools:
            available = ", ".join(self._tools.keys()) or "(none)"
            raise KeyError(
                f"Tool {name!r} is not registered. Available tools: {available}"
            )

        handler: Handler = self._tools[name]["handler"]
        result = await handler(**arguments)

        if isinstance(result, (dict, list)):
            return json.dumps(result, indent=2, default=str)
        if isinstance(result, str):
            return result
        return str(result)

    # ── Introspection ─────────────────────────────────────────────────────────

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """Return ``True`` if *name* is a registered tool."""
        return name in self._tools

    def __repr__(self) -> str:
        names = list(self._tools.keys())
        return f"ToolRegistry(tools={names!r})"


# ── Global singleton ──────────────────────────────────────────────────────────

#: The application-wide tool registry.  Import and use this instance to
#: register and execute tools throughout the codebase.
#:
#: Surface-specific tools (CLI, web) register against this singleton in their
#: own modules.  No market tools are registered here — this is the framework
#: only.
tool_registry = ToolRegistry()
