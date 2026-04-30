"""Langfuse self-hosted observability — multi-LLM consensus tracing.

This module is a thin, defensive wrapper around the
``langfuse`` Python SDK. It exists to give operators
*post-hoc visibility* into the Council v2 LLM panel
(GPT-5.2 + Claude Sonnet 4.5 + Gemini 2.5 Flash) — what
each model said, what the weighted vote produced, and how
much each call cost — without coupling the live execution
path to the observability backend.

Design discipline (matches ``services/proof_chain.py``):

* **Lazy singleton** — `get_langfuse_client()` constructs
  the client on first call only, so import-time and
  test-time costs are zero.
* **No-op when disabled** — missing env vars or
  ``LANGFUSE_ENABLED=false`` returns ``None``; every
  helper accepts ``None`` and short-circuits. Tests run
  without a Langfuse server.
* **Circuit breaker on init failure** — a 5-minute cooldown
  prevents repeated reconnect storms if the langfuse-server
  container is down.
* **Best-effort everywhere** — every span / observation
  operation is wrapped in ``try/except``. An observability
  outage MUST NOT break the bot loop. Same discipline as
  the proof chain emit on close.

Env contract (all optional, all leave the system in a
no-op state when missing):

* ``LANGFUSE_ENABLED``         — ``"true"`` (default) | ``"false"``
* ``LANGFUSE_HOST``            — e.g. ``http://langfuse-server:3090``
* ``LANGFUSE_PUBLIC_KEY``      — from langfuse-server UI signup
* ``LANGFUSE_SECRET_KEY``      — from langfuse-server UI signup
* ``LANGFUSE_DEBUG``           — ``"true"`` enables SDK-level logs

This module instruments only the LLM path
(``_run_council_llm`` + ``_run_single_council_model``).
The deterministic rule-based v1 council is free
observability that adds no value.
"""
from __future__ import annotations

__domain__ = "PRD"  # Post-Resolution Domain — observability over
                    # already-decided LLM consensus rounds.

import logging
import os
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator, Optional

logger = logging.getLogger(__name__)

# ── Singleton state ────────────────────────────────────────────────

_langfuse_client: Optional[Any] = None
_langfuse_init_attempted: bool = False
_last_init_error_at: Optional[float] = None
_INIT_ERROR_COOLDOWN_S: int = 300  # 5 minutes — circuit breaker


def _flag_enabled(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _has_required_config() -> bool:
    """All three env keys must be set for the SDK to do anything
    useful. Missing any one → no-op."""
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
        and os.environ.get("LANGFUSE_HOST")
    )


def get_langfuse_client() -> Optional[Any]:
    """Return the cached Langfuse client, or None if disabled.

    Cold path on first call: tries to construct the client.
    Hot path: returns the cached singleton.
    Failure path: caches the failure for 5 minutes (circuit
    breaker) so we don't hammer a dead langfuse-server.
    """
    global _langfuse_client, _langfuse_init_attempted, _last_init_error_at

    # Master kill-switch.
    if not _flag_enabled("LANGFUSE_ENABLED", "true"):
        return None

    # Already-constructed client — fast path.
    if _langfuse_client is not None:
        return _langfuse_client

    # Circuit breaker — recent init failure, don't retry yet.
    if (
        _last_init_error_at is not None
        and (time.time() - _last_init_error_at) < _INIT_ERROR_COOLDOWN_S
    ):
        return None

    if not _has_required_config():
        if not _langfuse_init_attempted:
            logger.info(
                "[langfuse] disabled: LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST "
                "missing — set all three to enable tracing."
            )
            _langfuse_init_attempted = True
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
            debug=_flag_enabled("LANGFUSE_DEBUG", "false"),
            timeout=5,  # Hard timeout — never block the bot loop.
        )
        _langfuse_init_attempted = True
        _last_init_error_at = None
        logger.info(
            "[langfuse] connected: %s",
            os.environ.get("LANGFUSE_HOST"),
        )
        return _langfuse_client
    except Exception as exc:  # noqa: BLE001
        # Init failures are benign — we just don't get traces. Cache
        # the failure so the next 300 calls don't all retry.
        _last_init_error_at = time.time()
        _langfuse_init_attempted = True
        logger.warning(
            "[langfuse] init failed (circuit-breaker active for %ds): %s",
            _INIT_ERROR_COOLDOWN_S, exc,
        )
        return None


# ── Context-manager helpers ────────────────────────────────────────

# These wrap the v4.x ``start_as_current_observation`` API in a
# null-safe shim. Callers can write:
#
#     async with traced_span("council_llm_panel", input=signal) as span:
#         ...
#         span_update(span, output={"action": consensus})
#
# When tracing is disabled or fails, the context manager yields
# ``None`` and ``span_update`` is a no-op. Zero ``if span is not
# None`` boilerplate at the call sites.


@contextmanager
def traced_span(
    name: str,
    *,
    as_type: str = "span",
    input: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Iterator[Optional[Any]]:
    """Synchronous context manager for a Langfuse observation.

    Yields the span object on success, ``None`` when tracing is
    disabled. Any exception inside the SDK is logged and silently
    swallowed — caller flow is unaffected.
    """
    client = get_langfuse_client()
    if client is None:
        yield None
        return

    try:
        kwargs: dict[str, Any] = {"name": name, "as_type": as_type}
        if input is not None:
            kwargs["input"] = input
        if metadata is not None:
            kwargs["metadata"] = metadata
        if model is not None:
            kwargs["model"] = model
        with client.start_as_current_observation(**kwargs) as span:
            try:
                yield span
            except Exception:
                # Re-raise after letting the span exit cleanly.
                raise
    except Exception as exc:  # noqa: BLE001
        # SDK failure during span construction — caller continues
        # without tracing. Don't shadow user exceptions.
        logger.debug("[langfuse] traced_span(%s) failed: %s", name, exc)
        yield None


@asynccontextmanager
async def atraced_span(
    name: str,
    *,
    as_type: str = "span",
    input: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> AsyncIterator[Optional[Any]]:
    """Async-friendly version of :func:`traced_span`.

    Yields the span object so async callers can hold it across
    ``await`` boundaries (e.g. surrounding an ``await
    chat.send_message(...)`` call).
    """
    client = get_langfuse_client()
    if client is None:
        yield None
        return

    try:
        kwargs: dict[str, Any] = {"name": name, "as_type": as_type}
        if input is not None:
            kwargs["input"] = input
        if metadata is not None:
            kwargs["metadata"] = metadata
        if model is not None:
            kwargs["model"] = model
        with client.start_as_current_observation(**kwargs) as span:
            yield span
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] atraced_span(%s) failed: %s", name, exc)
        yield None


def span_update(
    span: Optional[Any],
    *,
    output: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    usage_details: Optional[dict[str, int]] = None,
    cost_details: Optional[dict[str, float]] = None,
    level: Optional[str] = None,
    status_message: Optional[str] = None,
) -> None:
    """Update a span's output / metadata / cost. No-op on ``None``.

    The Langfuse v4.x API exposes ``update`` on the live
    observation handle. We accept the kwargs we actually use in
    this codebase (output, metadata, usage_details, cost_details,
    level, status_message) and ignore everything else.
    """
    if span is None:
        return
    try:
        kwargs: dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = output
        if metadata is not None:
            kwargs["metadata"] = metadata
        if usage_details is not None:
            kwargs["usage_details"] = usage_details
        if cost_details is not None:
            kwargs["cost_details"] = cost_details
        if level is not None:
            kwargs["level"] = level
        if status_message is not None:
            kwargs["status_message"] = status_message
        if not kwargs:
            return
        span.update(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] span_update failed: %s", exc)


def flush() -> None:
    """Flush in-flight events. Call before shutdown.

    Usually unnecessary — the SDK auto-flushes on a background
    interval and on process exit. We expose this for tests that
    want deterministic state.
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] flush failed: %s", exc)


# ── Test hooks ─────────────────────────────────────────────────────


def _reset_singleton_for_tests() -> None:
    """Reset the cached client + circuit-breaker state.

    Tests that toggle the env between cases need this — without it
    the first test's "disabled" decision sticks.
    """
    global _langfuse_client, _langfuse_init_attempted, _last_init_error_at
    _langfuse_client = None
    _langfuse_init_attempted = False
    _last_init_error_at = None
