"""Tests for the 2026-Q2 diagnostic metrics added to ``services.cache``.

Each metric gets exercised with a deterministic scenario that
makes the math fall out cleanly (no flaky timing assertions —
build/lookup-ms tests just check ``> 0`` and ``< sane_ceiling``).
"""
from __future__ import annotations

import asyncio

import pytest

from services.cache import TTLCache


# ── eviction taxonomy ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_invalidation_increments_manual_counter():
    c = TTLCache()
    await c.get_or_fetch("k", lambda: _const("a"), ttl=60)
    c.invalidate("k")
    s = c.stats()
    assert s["evictions"]["manual"] == 1
    assert s["evictions"]["clear"] == 0
    assert s["evictions"]["replaced"] == 0


@pytest.mark.asyncio
async def test_invalidate_missing_key_does_not_count():
    """Pop on a never-cached key should NOT count as an eviction —
    keeps the manual counter honest under bursty admin clicks."""
    c = TTLCache()
    c.invalidate("never_cached")
    assert c.stats()["evictions"]["manual"] == 0


@pytest.mark.asyncio
async def test_clear_increments_once_regardless_of_key_count():
    """``clear()`` is a single 'bulk flush' event from the operator's
    POV — counter ticks once per call, not once per key."""
    c = TTLCache()
    await c.get_or_fetch("k1", lambda: _const("a"), ttl=60)
    await c.get_or_fetch("k2", lambda: _const("b"), ttl=60)
    c.clear()
    s = c.stats()
    assert s["evictions"]["clear"] == 1
    # Second clear on empty cache should NOT count.
    c.clear()
    assert c.stats()["evictions"]["clear"] == 1


@pytest.mark.asyncio
async def test_stale_replacement_increments_replaced_counter():
    """A miss-path replace of an existing entry counts as ``replaced``
    — the closest analogue to a TTL eviction in this stale-while-
    revalidate cache."""
    c = TTLCache()
    await c.get_or_fetch("k", lambda: _const("v1"), ttl=0)  # forces stale
    # Force a synchronous miss-path overwrite by disabling stale-
    # while-revalidate so the fresh fetch goes through the lock path.
    await c.get_or_fetch(
        "k", lambda: _const("v2"), ttl=0,
        stale_while_revalidate=False,
    )
    s = c.stats()
    assert s["evictions"]["replaced"] >= 1


# ── timing metrics ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_avg_lookup_ms_populated_after_first_call():
    c = TTLCache()
    await c.get_or_fetch("k", lambda: _const("a"), ttl=60)
    s = c.stats()
    assert s["avg_lookup_ms"] > 0
    assert s["avg_lookup_ms"] < 10_000  # sanity ceiling


@pytest.mark.asyncio
async def test_avg_build_ms_only_counts_actual_fetches():
    """First call → build counts. Second call (hit) should NOT
    contribute to build timing — only to lookup timing."""
    c = TTLCache()
    await c.get_or_fetch("k", lambda: _const("a"), ttl=60)
    s_after_build = c.stats()
    build_ms_after_build = s_after_build["avg_build_ms"]
    build_count_after_build = c._build_count

    await c.get_or_fetch("k", lambda: _const("a"), ttl=60)  # hit
    s_after_hit = c.stats()
    assert s_after_hit["avg_build_ms"] == build_ms_after_build
    assert c._build_count == build_count_after_build


@pytest.mark.asyncio
async def test_avg_metrics_zero_on_empty_cache():
    c = TTLCache()
    s = c.stats()
    assert s["avg_lookup_ms"] == 0
    assert s["avg_build_ms"] == 0


# ── largest keys ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_largest_keys_sorted_desc_by_size():
    c = TTLCache()
    await c.get_or_fetch("small", lambda: _const("a"), ttl=60)
    await c.get_or_fetch("big", lambda: _const("a" * 5000), ttl=60)
    await c.get_or_fetch("med", lambda: _const("a" * 100), ttl=60)
    largest = c.stats()["largest_keys"]
    keys = [e["key"] for e in largest]
    assert keys == ["big", "med", "small"]
    assert largest[0]["size_bytes"] > largest[-1]["size_bytes"]


@pytest.mark.asyncio
async def test_largest_keys_capped_at_five():
    c = TTLCache()
    for i in range(8):
        await c.get_or_fetch(f"k{i}", lambda i=i: _const("a" * (i + 1)), ttl=60)
    largest = c.stats()["largest_keys"]
    assert len(largest) == 5


# ── expired vs manual breakdown ───────────────────────────────


@pytest.mark.asyncio
async def test_expired_vs_manual_breakdown_aliases_replaced_as_expired():
    c = TTLCache()
    await c.get_or_fetch("k", lambda: _const("v1"), ttl=0)
    await c.get_or_fetch(
        "k", lambda: _const("v2"), ttl=0,
        stale_while_revalidate=False,
    )
    c.invalidate("k")
    breakdown = c.stats()["expired_vs_manual_invalidations"]
    assert breakdown["expired"] >= 1
    assert breakdown["manual"] == 1
    assert breakdown["clear"] == 0


# ── helpers ───────────────────────────────────────────────────


async def _const(value):
    """Awaitable that resolves to a constant. Lets a one-shot lambda
    serve as the cache fetch function without a coroutine wrapper at
    each call site."""
    return value
