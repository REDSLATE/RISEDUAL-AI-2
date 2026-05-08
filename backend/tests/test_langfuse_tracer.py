"""Tests for ``services.langfuse_tracer``.

The tracer is observability — it must NEVER affect bot
correctness. These tests pin three properties:

1. **No-op when disabled** — missing env vars, ``LANGFUSE_ENABLED=false``,
   or SDK init failure all leave the bot running normally with
   zero traces emitted.
2. **Defensive helpers** — ``span_update`` and ``flush`` accept
   ``None`` and don't raise.
3. **Circuit breaker** — repeated init failures don't hammer a
   dead langfuse-server.

We don't try to test against a live Langfuse instance — that's
covered by the manual smoke test in ``LANGFUSE_SETUP.md``.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def setup_function():
    """Reset the singleton between cases — env mutations from one
    test must not leak into the next."""
    from services.langfuse_tracer import _reset_singleton_for_tests
    _reset_singleton_for_tests()


# ── No-op behaviour ────────────────────────────────────────────────


def test_disabled_via_env_returns_none(monkeypatch):
    """``LANGFUSE_ENABLED=false`` is the operator's master kill
    switch. Even if all the other keys are set, no client should
    be constructed."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3090")

    from services.langfuse_tracer import get_langfuse_client
    assert get_langfuse_client() is None


def test_missing_public_key_returns_none(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3090")

    from services.langfuse_tracer import get_langfuse_client
    assert get_langfuse_client() is None


def test_missing_secret_key_returns_none(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3090")

    from services.langfuse_tracer import get_langfuse_client
    assert get_langfuse_client() is None


def test_missing_host_returns_none(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    from services.langfuse_tracer import get_langfuse_client
    assert get_langfuse_client() is None


# ── Defensive helpers ──────────────────────────────────────────────


def test_span_update_with_none_is_noop():
    """Caller code does ``async with atraced_span(...) as span:
    span_update(span, ...)`` — when tracing is disabled, ``span``
    is None and ``span_update`` must not raise."""
    from services.langfuse_tracer import span_update
    # Should not raise.
    span_update(None, output={"x": 1})
    span_update(None)
    span_update(None, cost_details={"total_cost": 0.01}, level="ERROR")


def test_span_update_swallows_sdk_exceptions():
    """If the underlying SDK call raises (e.g. the span was
    already closed by the context manager), ``span_update`` must
    not propagate."""
    from services.langfuse_tracer import span_update
    bad_span = MagicMock()
    bad_span.update.side_effect = RuntimeError("span already closed")
    # Should not raise.
    span_update(bad_span, output={"x": 1})


def test_flush_with_no_client_is_noop(monkeypatch):
    """Calling ``flush()`` before any client exists must be safe —
    the shutdown hook in tests + scripts shouldn't have to check
    init state itself."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    from services.langfuse_tracer import flush
    flush()  # should not raise


# ── Context managers ───────────────────────────────────────────────


def test_traced_span_yields_none_when_disabled(monkeypatch):
    """Operators must be able to write
    ``with traced_span(...) as span: span_update(span, ...)``
    without checking for ``None`` first. The yielded span is
    ``None`` and the helpers handle it."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    from services.langfuse_tracer import traced_span, span_update

    with traced_span("test_span", input={"a": 1}) as span:
        assert span is None
        # All these must be no-ops:
        span_update(span, output={"b": 2})


@pytest.mark.asyncio
async def test_atraced_span_yields_none_when_disabled(monkeypatch):
    """Async equivalent of the sync test — same null-safety
    contract."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    from services.langfuse_tracer import atraced_span, span_update

    async with atraced_span("test_span", input={"a": 1}) as span:
        assert span is None
        span_update(span, output={"b": 2})


def test_traced_span_does_not_swallow_caller_exception(monkeypatch):
    """Critical: the tracer must not be a try/except black hole
    that hides bot bugs. If the user code inside the with-block
    raises, the exception must propagate to the caller."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    from services.langfuse_tracer import traced_span

    with pytest.raises(ValueError, match="bot bug"):
        with traced_span("test_span"):
            raise ValueError("bot bug")


@pytest.mark.asyncio
async def test_atraced_span_does_not_swallow_caller_exception(monkeypatch):
    """Async version of the same anti-black-hole contract."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    from services.langfuse_tracer import atraced_span

    with pytest.raises(ValueError, match="bot bug"):
        async with atraced_span("test_span"):
            raise ValueError("bot bug")


# ── Circuit breaker ────────────────────────────────────────────────


def test_init_failure_caches_for_cooldown(monkeypatch):
    """If the SDK constructor raises, we cache the failure so
    subsequent calls don't re-attempt. Otherwise a dead
    langfuse-server would have us retrying on every Council
    round (every ~30s)."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://does-not-exist:3090")

    # Patch the SDK constructor to raise.
    with patch("langfuse.Langfuse", side_effect=RuntimeError("network unreachable")):
        from services.langfuse_tracer import get_langfuse_client
        assert get_langfuse_client() is None
        # Second call should NOT re-attempt — the circuit breaker
        # should cache the failure. Verify by re-patching with a
        # tracker.
    with patch("langfuse.Langfuse") as constructor:
        from services.langfuse_tracer import get_langfuse_client
        assert get_langfuse_client() is None
        # The breaker must have suppressed this call.
        assert constructor.call_count == 0


def test_init_success_caches_singleton(monkeypatch):
    """Verify the happy-path cache: one Langfuse() construction
    even across many ``get_langfuse_client()`` calls."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3090")

    fake_client = MagicMock()
    with patch("langfuse.Langfuse", return_value=fake_client) as constructor:
        from services.langfuse_tracer import get_langfuse_client
        c1 = get_langfuse_client()
        c2 = get_langfuse_client()
        c3 = get_langfuse_client()

    assert c1 is c2 is c3 is fake_client
    assert constructor.call_count == 1


# ── End-to-end: span emission with mocked client ───────────────────


def test_span_update_dispatches_to_sdk_when_client_active(monkeypatch):
    """When a client exists and the span is real, ``span_update``
    must call the SDK's update. This pins the contract that
    output / cost_details / metadata flow through."""
    fake_span = MagicMock()
    from services.langfuse_tracer import span_update

    span_update(
        fake_span,
        output={"action": "HOLD"},
        cost_details={"total_cost": 0.011},
        metadata={"votes_ok_count": 3},
    )

    fake_span.update.assert_called_once()
    kwargs = fake_span.update.call_args.kwargs
    assert kwargs["output"] == {"action": "HOLD"}
    assert kwargs["cost_details"] == {"total_cost": 0.011}
    assert kwargs["metadata"] == {"votes_ok_count": 3}


def test_span_update_with_no_kwargs_is_skip(monkeypatch):
    """Calling span_update with only ``span=...`` and no actual
    fields to set must NOT call the SDK — no-op preserves the
    "minimum surface area" contract."""
    fake_span = MagicMock()
    from services.langfuse_tracer import span_update
    span_update(fake_span)
    assert fake_span.update.call_count == 0


# ── Council LLM integration shape ──────────────────────────────────


@pytest.mark.asyncio
async def test_council_llm_runs_when_tracer_disabled(monkeypatch):
    """The biggest risk in a tracing rollout: the wrapper itself
    breaks the function being traced. This test pins that
    ``_run_council_llm`` produces a sane result with tracing
    disabled — the same contract as before instrumentation.

    (We don't test with a live LLM; just verify the no-API-key
    early return still works and shape is unchanged.)
    """
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)

    from services.research_shadow_engines import _run_council_llm

    result = await _run_council_llm({
        "symbol": "BTC", "asset_type": "crypto",
        "action": "LONG", "confidence": 0.7,
        "rsi": 55, "momentum_5b": 0.02, "volume_ratio": 1.1,
        "price": 50000.0,
    })

    # Same shape as before instrumentation.
    assert set(result.keys()) == {"action", "thesis", "confidence", "llm_cost_usd"}
    assert result["action"] == "HOLD"
    assert result["thesis"] == "council_llm_no_api_key"
    assert result["confidence"] == 0.0
    assert result["llm_cost_usd"] == 0.0
