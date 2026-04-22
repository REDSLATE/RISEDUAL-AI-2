"""Structured-log helpers.

Thin wrappers around stdlib `logging` that emit a single JSON-like
payload as both a readable message and an ``extra`` dict. Log
aggregators (Datadog, CloudWatch, Elastic) parse the ``extra`` fields
into queryable attributes; the human-readable message stays useful
for tailing local logs.

Also exports :func:`unwrap_gather_result` — the canonical helper for
handling the output of ``asyncio.gather(..., return_exceptions=True)``.
Centralises the three-tier guard so no caller has to remember that
`asyncio.CancelledError` is a `BaseException`, not an `Exception`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar, cast


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

    # Feed ERROR-level events into the rolling in-process counter so
    # `/api/admin/gather-error-rate` can aggregate flakiness by context.
    # Cheap (O(1) lock + append) and deliberately out-of-band from the
    # logger pipeline — metrics must never crash logging.
    if level >= logging.ERROR:
        try:
            from services.error_metrics import record_error
            record_error(
                context=str(payload.get("context", "")),
                err_type=str(payload.get("type", "")),
                note=str(payload.get("note", "")),
                **{k: v for k, v in payload.items()
                   if k not in ("context", "type", "note", "error")},
            )
        except Exception:
            # Never let metric recording break log emission.
            pass


def log_warning(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured WARNING — see module docstring."""
    _emit(logger, logging.WARNING, payload)


def log_error(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured ERROR — see module docstring."""
    _emit(logger, logging.ERROR, payload)


def log_info(logger: logging.Logger, payload: dict[str, Any]) -> None:
    """Structured INFO — see module docstring."""
    _emit(logger, logging.INFO, payload)


# ────────────────────────────────────────────────────────────────────────────
# asyncio.gather result unwrapping
# ────────────────────────────────────────────────────────────────────────────

T = TypeVar("T")


def unwrap_gather_result(
    value: Any,
    fallback: T,
    logger: logging.Logger | None = None,
    context: str = "",
    note: str = "",
    **extra: Any,
) -> T:
    """Canonical three-tier guard for a single ``asyncio.gather(return_exceptions=True)``
    result.

    Rules (evaluated in order):
      1. `asyncio.CancelledError` → return `fallback` **silently** (no
         log). Cancellation is expected during FastAPI shutdown /
         ASGI timeout; logging it would spam operator consoles.
      2. Any other `BaseException` → call :func:`log_error` with the
         exception str/type + `context` + `note` + any `**extra`
         keyword args (e.g. `symbol="AAPL"`), then return `fallback`.
      3. `None` → return `fallback` silently (treated as empty fetch).
      4. Otherwise → return `value` (cast to the fallback's type so
         mypy narrows at call sites).

    The `logger=None` default lets callers opt out of the log path
    (e.g. in tests where observability isn't the concern), but in
    production code the logger should always be passed so real
    errors aren't silently swallowed.

    Rationale for abstracting this:
    `asyncio.CancelledError` is a `BaseException` subclass since
    Python 3.8 — NOT an `Exception`. Every `isinstance(x, Exception)`
    guard after `gather(return_exceptions=True)` is a latent
    `AttributeError` crash waiting for a shutdown event. We found
    three of these in the codebase in one session (FRED,
    war_room, crew_engine). This helper makes the correct pattern
    the easy one to reach for.
    """
    if isinstance(value, asyncio.CancelledError):
        return fallback

    if isinstance(value, BaseException):
        if logger is not None:
            payload: dict[str, Any] = {
                "error": str(value),
                "type": type(value).__name__,
                "context": context,
                "note": note,
            }
            payload.update(extra)
            log_error(logger, payload)
        return fallback

    if value is None:
        return fallback

    # Narrow to the fallback's type so downstream `.get(...)` /
    # `.method()` calls type-check. Runtime-safe because steps 1-3
    # exhaust the exception/None branches.
    return cast(T, value)
