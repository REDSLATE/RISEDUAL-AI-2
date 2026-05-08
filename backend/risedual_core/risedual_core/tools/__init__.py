"""Tool registry framework for risedual_core.

Re-exports the :class:`ToolRegistry` class and the application-wide
:data:`tool_registry` singleton::

    from risedual_core.tools import tool_registry, ToolRegistry
"""
from __future__ import annotations

from risedual_core.tools.registry import ToolRegistry, tool_registry

__all__ = [
    "ToolRegistry",
    "tool_registry",
]
