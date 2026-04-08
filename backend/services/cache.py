"""In-memory TTL cache for expensive API responses."""
import asyncio
import time
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple async-safe in-memory cache with per-key TTL and background refresh."""

    def __init__(self):
        self._store: Dict[str, Dict] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

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
        """Return cached value if fresh, otherwise fetch and cache.

        Args:
            key: Cache key string.
            fetch_fn: Async callable that returns the data.
            ttl: Time-to-live in seconds.
            stale_while_revalidate: If True, return stale data immediately
                and refresh in the background. If False, wait for fresh data.
        """
        now = time.monotonic()
        entry = self._store.get(key)

        # Cache hit — still fresh
        if entry and (now - entry["ts"]) < ttl:
            return entry["data"]

        # Cache hit but stale — return stale and refresh in background
        if entry and stale_while_revalidate:
            if not entry.get("refreshing"):
                entry["refreshing"] = True
                asyncio.create_task(self._refresh(key, fetch_fn, ttl))
            return entry["data"]

        # Cache miss or stale without revalidate — fetch synchronously
        lock = self._get_lock(key)
        async with lock:
            # Double-check after acquiring lock
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

    def stats(self) -> Dict:
        """Return cache statistics."""
        now = time.monotonic()
        entries = []
        for key, entry in self._store.items():
            age = round(now - entry["ts"], 1)
            entries.append({"key": key, "age_seconds": age, "refreshing": entry.get("refreshing", False)})
        return {"entries": entries, "total_keys": len(self._store)}


# Global singleton
cache = TTLCache()
