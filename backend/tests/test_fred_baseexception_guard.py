"""Regression test for the BaseException-narrowing bug fix in fred_service.

When ``asyncio.gather(return_exceptions=True)`` surfaces an
``asyncio.CancelledError``, that error IS captured in the results list
(unlike ``SystemExit`` / ``KeyboardInterrupt`` which propagate out of
gather). Since Python 3.8, ``CancelledError`` is a ``BaseException``
subclass, **not** an ``Exception`` subclass.

The previous guard:
    if isinstance(result, Exception) or result is None:
        continue

did NOT narrow ``CancelledError`` — it slipped past and crashed at
``result.get("observations", [])`` with
``AttributeError: 'CancelledError' object has no attribute 'get'``.

The fix changes the narrowing to ``BaseException``. Reachable any time a
concurrent task fetching a FRED series is cancelled mid-flight, e.g.
during FastAPI request shutdown or a manual ``Task.cancel()``.

This test reproduces the crash shape by injecting a real
``CancelledError`` into the gather results via a patched
``asyncio.gather``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from services import fred_service


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _clear_cache() -> None:
    fred_service.CACHE.pop("macro_indicators", None)


# ────────────────────────────────────────────────────────────────────────────
# get_macro_indicators
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_macro_indicators_survives_cancelled_error_in_gather_result(
    monkeypatch,
):
    """Regression: CancelledError (BaseException, not Exception) used to
    slip past the guard and crash. Now skipped cleanly."""
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    _clear_cache()

    # Trim MACRO_SERIES to two entries so we can inject exactly one
    # CancelledError and one normal response.
    monkeypatch.setattr(
        fred_service, "MACRO_SERIES",
        fred_service.MACRO_SERIES[:2],
        raising=False,
    )

    # Patch asyncio.gather to return what a real cancellation would yield.
    async def _fake_gather(*tasks, return_exceptions=False):
        return [asyncio.CancelledError("mid-flight cancel"),
                {"observations": []}]

    with patch.object(asyncio, "gather", _fake_gather):
        result = await fred_service.get_macro_indicators()

    assert isinstance(result, dict)
    assert "indicators" in result
    # Both series drop out cleanly — one was cancelled, the other was empty.
    assert result["indicators"] == []


@pytest.mark.asyncio
async def test_get_macro_indicators_still_handles_plain_exception(monkeypatch):
    """Sanity: widening the guard to BaseException must still skip plain
    `Exception` instances (httpx errors, ValueError, etc)."""
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    _clear_cache()

    monkeypatch.setattr(
        fred_service, "MACRO_SERIES",
        fred_service.MACRO_SERIES[:2],
        raising=False,
    )

    async def _fake_gather(*tasks, return_exceptions=False):
        return [ValueError("upstream 503"),
                {"observations": []}]

    with patch.object(asyncio, "gather", _fake_gather):
        result = await fred_service.get_macro_indicators()

    assert result["indicators"] == []


@pytest.mark.asyncio
async def test_get_macro_indicators_skips_none_results(monkeypatch):
    """Guard also has to short-circuit on `None` — keep that invariant."""
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    _clear_cache()

    monkeypatch.setattr(
        fred_service, "MACRO_SERIES",
        fred_service.MACRO_SERIES[:2],
        raising=False,
    )

    async def _fake_gather(*tasks, return_exceptions=False):
        return [None, {"observations": []}]

    with patch.object(asyncio, "gather", _fake_gather):
        result = await fred_service.get_macro_indicators()

    assert result["indicators"] == []
