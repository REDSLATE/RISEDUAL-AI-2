"""Cache-contract regression tests for AICacheService.

Prevents the bug:
    first call works (cache miss path) → second call hits cache → 500

Root cause it pins:
    ``AICacheService.get()`` returns the *inner* data dict (see
    ``services/ai_cache_service.py:65`` — ``return doc.get("data")``).
    Endpoint callers must use ``cached`` directly. The historic bug was
    callers writing ``cached["data"]`` which raised ``KeyError: 'data'``
    on every cache hit, manifesting as an HTTP 500.

These tests deliberately stay at the unit level (no FastAPI client, no
network) so they catch the regression in <1 second on every run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ai_cache_service import AICacheService


# ─── helpers ─────────────────────────────────────────────────────────


def _service_with_doc(doc):
    """Build an AICacheService whose underlying collection.find_one()
    returns ``doc``. update_one is also mocked so sliding-TTL writes
    don't blow up.
    """
    fake_collection = MagicMock()
    fake_collection.find_one = AsyncMock(return_value=doc)
    fake_collection.update_one = AsyncMock(return_value=None)

    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    return AICacheService(fake_db), fake_collection


# ─── get() shape contract ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_inner_data_dict_not_wrapper():
    """The fundamental contract that the historic bug violated."""
    inner = {"symbol": "AAPL", "verdict": "BUY", "confidence": 0.82}
    wrapper = {
        "cache_key": "k",
        "data": inner,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "namespace": "hypothesis",
    }
    service, _ = _service_with_doc(wrapper)

    result = await service.get("k")

    # Contract: caller receives the inner data dict directly.
    assert result == inner
    # Wrapper fields must NOT leak through.
    assert "data" not in result
    assert "created_at" not in result
    assert "expires_at" not in result
    assert "cache_key" not in result


@pytest.mark.asyncio
async def test_get_double_unwrap_pattern_is_a_bug():
    """The exact pattern that caused the production 500 — written down
    so any future "convenience" refactor that re-introduces it fails
    here first."""
    inner = {"symbol": "AAPL", "verdict": "BUY"}
    wrapper = {"data": inner}
    service, _ = _service_with_doc(wrapper)

    cached = await service.get("k")

    assert cached == inner
    # Endpoints that try ``cached["data"]`` raise KeyError — exactly
    # what manifested as an HTTP 500 in routes/ai.py before the fix.
    with pytest.raises(KeyError):
        _ = cached["data"]


@pytest.mark.asyncio
async def test_get_returns_none_on_cache_miss():
    service, _ = _service_with_doc(None)
    assert await service.get("missing") is None


@pytest.mark.asyncio
async def test_get_returns_none_when_db_handle_missing():
    """Defensive path — service must not raise when wired without a
    DB handle (happens in some test contexts)."""
    service = AICacheService(None)
    assert await service.get("anything") is None


# ─── TTL filter is applied at query time (not endpoint-time) ─────────


@pytest.mark.asyncio
async def test_get_filters_expired_documents_via_query():
    """The endpoint never sees expired payloads because the cache
    service queries with ``expires_at > now``. Pin that here so a
    future refactor doesn't accidentally remove the filter and rely
    on caller-side expiry checks."""
    service, fake_collection = _service_with_doc({"data": {"x": 1}})

    await service.get("k")

    fake_collection.find_one.assert_awaited_once()
    args, kwargs = fake_collection.find_one.call_args
    query = args[0]
    assert "expires_at" in query
    assert "$gt" in query["expires_at"]


@pytest.mark.asyncio
async def test_get_max_age_adds_created_at_floor_to_query():
    service, fake_collection = _service_with_doc({"data": {"x": 1}})

    await service.get("k", max_age_seconds=60)

    args, _ = fake_collection.find_one.call_args
    query = args[0]
    assert "created_at" in query
    assert "$gt" in query["created_at"]


@pytest.mark.asyncio
async def test_get_sliding_ttl_extends_expires_at_on_read():
    service, fake_collection = _service_with_doc({"data": {"x": 1}})

    await service.get("k", ttl_seconds=300, sliding=True)

    fake_collection.update_one.assert_awaited_once()
    args, _ = fake_collection.update_one.call_args
    update_filter, update_doc = args
    assert update_filter == {"cache_key": "k"}
    assert "$set" in update_doc
    assert "expires_at" in update_doc["$set"]
