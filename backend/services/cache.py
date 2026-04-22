"""In-memory TTL cache for expensive API responses."""
import asyncio
import time
import logging
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple async-safe in-memory cache with per-key TTL and background refresh."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._started_at: float = time.monotonic()

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable,
        ttl: int = 120,
        stale_while_revalidate: bool = True,
    ) -> Any:
        now = time.monotonic()
        entry = self._store.get(key)

        # Cache hit — still fresh
        if entry and (now - entry["ts"]) < ttl:
            self._hits += 1
            return entry["data"]

        # Cache hit but stale — return stale and refresh in background
        if entry and stale_while_revalidate:
            self._hits += 1
            if not entry.get("refreshing"):
                entry["refreshing"] = True
                asyncio.create_task(self._refresh(key, fetch_fn, ttl))
            return entry["data"]

        # Cache miss — fetch synchronously
        self._misses += 1
        lock = self._get_lock(key)
        async with lock:
            entry = self._store.get(key)
            if entry and (now - entry["ts"]) < ttl:
                return entry["data"]

            data = await self._safe_fetch(key, fetch_fn)
            self._store[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
            return data

    async def _refresh(self, key: str, fetch_fn: Callable, ttl: int):
        """Background refresh — updates cache without blocking callers."""
        try:
            data = await fetch_fn()
            self._store[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
            logger.info(f"Cache refreshed: {key}")
        except Exception as e:
            logger.warning(f"Background refresh failed for {key}: {e}")
            entry = self._store.get(key)
            if entry:
                entry["refreshing"] = False

    async def _safe_fetch(self, key: str, fetch_fn: Callable) -> Any:
        """Fetch with error handling — returns stale data on failure."""
        try:
            return await fetch_fn()
        except Exception as e:
            logger.error(f"Cache fetch failed for {key}: {e}")
            entry = self._store.get(key)
            if entry:
                return entry["data"]
            raise

    def invalidate(self, key: str):
        """Remove a specific key from cache."""
        self._store.pop(key, None)

    def clear(self):
        """Clear entire cache."""
        self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics with hit/miss tracking."""
        now = time.monotonic()
        total = self._hits + self._misses
        entries = []
        for key, entry in self._store.items():
            age = round(now - entry["ts"], 1)
            data_size = len(str(entry.get("data", "")))
            entries.append({
                "key": key,
                "age_seconds": age,
                "refreshing": entry.get("refreshing", False),
                "size_bytes": data_size,
            })
        return {
            "entries": entries,
            "total_keys": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total else 0,
            "total_requests": total,
            "uptime_seconds": round(now - self._started_at, 1),
        }


# Global singleton
cache = TTLCache()
