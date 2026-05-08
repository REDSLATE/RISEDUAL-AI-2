"""Unit tests for the symbol → sector resolver.

Verifies:
  * Mongo cache hit short-circuits before static map / Finnhub.
  * Static map hit returns the correct sector and writes through
    to the cache.
  * Finnhub fallback returns `finnhubIndustry` when cache and
    static map miss.
  * Unknown symbols degrade to "Unknown" without raising.
  * Stale cache entries (>7d) are treated as cache misses.
  * resolve_sectors_bulk is bounded and returns a dict.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.symbol_sector_resolver import (
    resolve_sector, resolve_sectors_bulk, _STATIC_SECTORS,
)


class _FakeCacheCollection:
    def __init__(self, fixture=None):
        self._fixture = fixture or {}
        self.writes = []

    async def find_one(self, query, projection=None):  # noqa: ARG002
        return self._fixture.get(query.get("_id"))

    async def update_one(self, query, update, upsert=False):  # noqa: ARG002
        self.writes.append((query, update))
        return MagicMock(matched_count=1, upserted_id=None)


class _FakeDb:
    def __init__(self, cache_fixture=None):
        self._coll = _FakeCacheCollection(cache_fixture)

    def __getitem__(self, name):
        assert name == "symbol_sector_cache"
        return self._coll


@pytest.mark.asyncio
async def test_cache_hit_short_circuits():
    fixture = {
        "NVDA": {
            "sector": "Cached Sector",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }
    db = _FakeDb(fixture)
    # If Finnhub were called we'd see it in the mock call log; assert 0 calls.
    with patch("services.symbol_sector_resolver._finnhub_lookup", new=AsyncMock()) as finn:
        sector = await resolve_sector(db, "NVDA")
    assert sector == "Cached Sector"
    finn.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_map_hits_are_cached_writethrough():
    db = _FakeDb()
    with patch("services.symbol_sector_resolver._finnhub_lookup", new=AsyncMock()) as finn:
        sector = await resolve_sector(db, "aapl")  # lowercase → upper internally
    assert sector == _STATIC_SECTORS["AAPL"]
    finn.assert_not_awaited()
    # Writethrough: should have persisted to cache.
    assert len(db._coll.writes) == 1
    assert db._coll.writes[0][0]["_id"] == "AAPL"


@pytest.mark.asyncio
async def test_finnhub_fallback_when_static_miss():
    db = _FakeDb()
    with patch("services.symbol_sector_resolver._finnhub_lookup",
               new=AsyncMock(return_value="Hydrogen Rockets")):
        sector = await resolve_sector(db, "ZZZZZ")
    assert sector == "Hydrogen Rockets"
    assert any(w[0]["_id"] == "ZZZZZ" for w in db._coll.writes)


@pytest.mark.asyncio
async def test_unknown_symbol_returns_unknown():
    db = _FakeDb()
    with patch("services.symbol_sector_resolver._finnhub_lookup",
               new=AsyncMock(return_value=None)):
        sector = await resolve_sector(db, "ZZZZZ")
    assert sector == "Unknown"
    # Unknown should NOT be persisted (would pollute the cache).
    assert db._coll.writes == []


@pytest.mark.asyncio
async def test_stale_cache_is_treated_as_miss():
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fixture = {"NVDA": {"sector": "Stale Sector", "updated_at": stale_ts}}
    db = _FakeDb(fixture)
    # Stale → cache miss → static map hit (NVDA is in static map)
    with patch("services.symbol_sector_resolver._finnhub_lookup", new=AsyncMock()) as finn:
        sector = await resolve_sector(db, "NVDA")
    assert sector == _STATIC_SECTORS["NVDA"]
    finn.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_returns_dict_and_dedupes():
    db = _FakeDb()
    with patch("services.symbol_sector_resolver._finnhub_lookup", new=AsyncMock()):
        out = await resolve_sectors_bulk(db, ["AAPL", "aapl", "MSFT", ""])
    # Empty strings filtered; lowercase folded.
    assert set(out.keys()) == {"AAPL", "MSFT"}
    assert out["AAPL"] == _STATIC_SECTORS["AAPL"]


@pytest.mark.asyncio
async def test_bulk_empty_input():
    out = await resolve_sectors_bulk(None, [])
    assert out == {}


if __name__ == "__main__":
    asyncio.run(test_unknown_symbol_returns_unknown())
    print("OK")
