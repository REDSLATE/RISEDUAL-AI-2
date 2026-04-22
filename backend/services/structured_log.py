"""Structured-log helpers.

Thin wrappers around stdlib `logging` that emit a single JSON-like
payload as both a readable message and an ``extra`` dict. Log
aggregators (Datadog, CloudWatch, Elastic) parse the ``extra`` fields
into queryable attributes; the human-readable message stays useful
for tailing local logs.

Usage:
    from services.structured_log import log_warning

    log_warning(logger, {
        "error": str(exc),
        "type": type(exc).__name__,
        "context": "fred_fetch",
        "spec_id": spec.get("id"),
    })

The first arg is the module-level `logger` so output is routed
through whatever handler/config that module uses — no hidden
cross-logger surprises. The dict is emitted as-is on the ``extra``
field so downstream processors can index it.
"""
from __future__ import annotations

import logging
from typing import Any


def _emit(logger: logging.Logger, level: int, payload: dict[str, Any]) -> None:
    """Single entry point. Caller passes the level constant."""
    # Build a human-readable one-liner: "context=fred_fetch type=CancelledError error=..."
    # Fields are stable-ordered; `context` leads because tailers filter on it.
    ordered_keys = ["context", "type", "error"] + [
        k for k in payload if k not in ("context", "type", "error")
    ]
    parts = []
    for k in ordered_keys:
        if k not in payload:
            continue
        v = payload[k]
        # Single-line safe — strip newlines from strings so multi-line
        # errors don't break grep-based log parsing.
        if isinstance(v, str):
            v = v.replace("\n", " ").replace("\r", " ")
        parts.append(f"{k}={v}")
    msg = " ".join(parts) if parts else "(empty log payload)"
    # `extra` collides with `LogRecord` reserved names (e.g. 'message'),
    # so namespace the payload under a single dict key.
    logger.log(level, msg, extra={"structured": payload})


def log_warning(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured WARNING — see module docstring."""
    _emit(logger, logging.WARNING, payload)


def log_error(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured ERROR — see module docstring."""
    _emit(logger, logging.ERROR, payload)


def log_info(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured INFO — see module docstring."""
    _emit(logger, logging.INFO, payload)
