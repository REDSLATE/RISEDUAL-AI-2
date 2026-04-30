"""Regression: ``services/research_router.py`` Mongo-tz comparison fix.

The cache-lookup path used to do::

    cached_at = doc.get("cached_at")          # tz stripped by Mongo
    cutoff = datetime.now(timezone.utc) - timedelta(...)
    if cached_at < cutoff:                     # ← TypeError: tz mismatch

The ``<`` form escaped ``test_no_unguarded_mongo_datetime_math.py``
because that guard's regex matches ``mongo_var <op> datetime.now(...)``
directly — it doesn't trace through the ``cutoff = ...`` indirection.
This file pins the fix so the bug can't reappear without test
attention even if the broader guard's heuristic stays the same.

Production impact when broken: every crypto-bot tick on a symbol
with cached web research raises, gets swallowed by the calling
try/except, and forces a re-fetch of Tavily — burning API spend
and printing warning spam (4× per tick observed pre-fix on
ETH/SOL).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_db_with_cached_at(cached_at):
    """Return a fake DB whose ``web_research_cache.find_one`` returns
    a doc with the provided (possibly tz-naive) ``cached_at``."""
    db = MagicMock()
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "symbol": "ETH",
        "cached_at": cached_at,
        "verdict": {"thesis": "neutral", "confidence": 0.4},
    })
    db.__getitem__ = MagicMock(return_value=coll)
    return db


@pytest.mark.asyncio
async def test_get_cached_verdict_handles_tz_naive_mongo_datetime():
    """Mongo strips tzinfo on round-trip. ``get_cached_verdict``
    must wrap the loaded datetime in ``ensure_utc()`` so the
    ``cached_at < cutoff`` comparison doesn't raise."""
    from services.research_router import get_cached_verdict

    naive = datetime.now(timezone.utc).replace(tzinfo=None)  # tz stripped
    db = _make_db_with_cached_at(naive)

    # Must NOT raise. Returns the cached verdict because the
    # naive timestamp is "now" → still inside the TTL window.
    result = await get_cached_verdict(db, "ETH", ttl_seconds=600)
    assert result is not None
    assert result.get("cached") is True
    assert result.get("thesis") == "neutral"


@pytest.mark.asyncio
async def test_get_cached_verdict_returns_none_for_stale_naive_mongo_datetime():
    """The TTL gate still works correctly when the loaded
    datetime is tz-naive — ``ensure_utc()`` re-tags as UTC and
    the comparison runs on equal-tz inputs."""
    from services.research_router import get_cached_verdict

    naive_old = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).replace(tzinfo=None)  # 2 hours ago, tz stripped
    db = _make_db_with_cached_at(naive_old)

    result = await get_cached_verdict(db, "ETH", ttl_seconds=600)
    assert result is None  # stale → cache miss, force re-fetch


@pytest.mark.asyncio
async def test_get_cached_verdict_handles_tz_aware_mongo_datetime():
    """Defensive: if Mongo (or a re-write) ever returns a tz-aware
    datetime, ``ensure_utc()`` is idempotent — no double-tagging,
    no rejection. The existing happy path keeps working."""
    from services.research_router import get_cached_verdict

    aware = datetime.now(timezone.utc)
    db = _make_db_with_cached_at(aware)

    result = await get_cached_verdict(db, "ETH", ttl_seconds=600)
    assert result is not None
    assert result.get("cached") is True
