"""In-memory TTL cache for expensive API responses."""
import asyncio
import time
import logging
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple async-safe in-memory cache with per-key TTL and background refresh.

    Beyond the basic hit/miss accounting the admin tile shows, this
    instance also tracks the four diagnostic metrics that become
    critical at scale: ``evictions`` (broken down by source so the
    operator can tell a stale-replacement from a manual flush), a
    rolling ``avg_lookup_ms`` for the read hot-path, a rolling
    ``avg_build_ms`` for cache-miss fetches (catches a slow upstream
    quietly degrading user latency), and a ``largest_keys`` roster
    for memory-pressure awareness.

    All timings use ``time.perf_counter()`` (monotonic, micro-precision).
    Lookup timing wraps the entire ``get_or_fetch`` call so it
    reflects what callers actually feel; build timing isolates the
    upstream ``fetch_fn`` so the average isn't polluted by hits.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._started_at: float = time.monotonic()

        # ── Diagnostic counters added 2026-Q2 ──────────────────────
        # Eviction taxonomy:
        #   manual     — explicit invalidate(key) call (e.g. admin
        #                "Invalidate" button or upstream busts).
        #   clear      — full cache wipe via clear() (admin "Clear
        #                All" button or service restart code-path).
        #   replaced   — TTL elapsed and a fresh fetch overwrote the
        #                stale entry (the closest analogue to a true
        #                TTL eviction in this stale-while-revalidate
        #                cache).
        # We DON'T expose these as a single number because operators
        # care about the mix: 1000 'replaced' is healthy churn,
        # 1000 'manual' on the same window is a hot-key thrashing
        # bug.
        self._evict_manual: int = 0
        self._evict_clear: int = 0
        self._evict_replaced: int = 0

        # Lookup timing — wraps the full get_or_fetch hot-path so the
        # average reflects what callers feel (hit-fast + miss-slow).
        self._lookup_count: int = 0
        self._lookup_ms_total: float = 0.0

        # Build timing — only the upstream fetch_fn wall-time. Caller
        # wraps this around the actual upstream call so a slow API
        # provider stands out without polluting lookup timing.
        self._build_count: int = 0
        self._build_ms_total: float = 0.0

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
        lookup_t0 = time.perf_counter()
        try:
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

                had_prior = entry is not None
                data = await self._safe_fetch(key, fetch_fn)
                self._store[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
                if had_prior:
                    self._evict_replaced += 1
                return data
        finally:
            self._lookup_count += 1
            self._lookup_ms_total += (time.perf_counter() - lookup_t0) * 1000

    async def _refresh(self, key: str, fetch_fn: Callable, ttl: int) -> None:
        """Background refresh — updates cache without blocking callers."""
        try:
            build_t0 = time.perf_counter()
            data = await fetch_fn()
            self._build_ms_total += (time.perf_counter() - build_t0) * 1000
            self._build_count += 1
            had_prior = self._store.get(key) is not None
            self._store[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
            if had_prior:
                self._evict_replaced += 1
            logger.info(f"Cache refreshed: {key}")
        except Exception as e:
            logger.warning(f"Background refresh failed for {key}: {e}")
            entry = self._store.get(key)
            if entry:
                entry["refreshing"] = False

    async def _safe_fetch(self, key: str, fetch_fn: Callable) -> Any:
        """Fetch with error handling — returns stale data on failure.

        Times the upstream fetch wall-time so a slow API provider
        shows up in ``avg_build_ms`` without polluting lookup timing.
        Stale-fallback is NOT counted as a build (no upstream wall
        time was actually consumed).
        """
        build_t0 = time.perf_counter()
        try:
            data = await fetch_fn()
            self._build_ms_total += (time.perf_counter() - build_t0) * 1000
            self._build_count += 1
            return data
        except Exception as e:
            logger.error(f"Cache fetch failed for {key}: {e}")
            entry = self._store.get(key)
            if entry:
                return entry["data"]
            raise

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache. Counted as a manual
        eviction so the dashboard can distinguish operator-driven
        flushes from natural TTL churn."""
        if self._store.pop(key, None) is not None:
            self._evict_manual += 1

    def clear(self) -> None:
        """Clear entire cache. Counted as a single ``clear`` event
        regardless of how many keys were wiped — operators care that
        the bulk-flush happened, not how many keys it touched."""
        if self._store:
            self._evict_clear += 1
        self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics with hit/miss tracking + the
        2026-Q2 diagnostic metrics.

        Shape::

          {
            "entries": [...],            # existing
            "total_keys": int,           # existing
            "hits": int,                 # existing
            "misses": int,               # existing
            "hit_rate": float,           # existing %
            "total_requests": int,       # existing
            "uptime_seconds": float,     # existing
            "evictions": {
              "manual": int, "clear": int, "replaced": int,
              "total": int,
            },
            "avg_lookup_ms": float,
            "avg_build_ms": float,
            "largest_keys": [{"key": str, "size_bytes": int}, ...],
            "expired_vs_manual_invalidations": {
              "expired": int,            # alias of evictions.replaced
              "manual": int,             # invalidate() calls
              "clear": int,              # clear() calls
            },
          }
        """
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
        # Top 5 by size — kept short so the admin tile renders cleanly.
        # Tied sizes are stable-sorted by key for determinism.
        largest = sorted(
            entries, key=lambda e: (-e["size_bytes"], e["key"]),
        )[:5]
        largest_view = [
            {"key": e["key"], "size_bytes": e["size_bytes"]}
            for e in largest
        ]
        evict_total = (
            self._evict_manual + self._evict_clear + self._evict_replaced
        )
        return {
            "entries": entries,
            "total_keys": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total else 0,
            "total_requests": total,
            "uptime_seconds": round(now - self._started_at, 1),
            "evictions": {
                "manual": self._evict_manual,
                "clear": self._evict_clear,
                "replaced": self._evict_replaced,
                "total": evict_total,
            },
            "avg_lookup_ms": (
                round(self._lookup_ms_total / self._lookup_count, 3)
                if self._lookup_count else 0
            ),
            "avg_build_ms": (
                round(self._build_ms_total / self._build_count, 3)
                if self._build_count else 0
            ),
            "largest_keys": largest_view,
            "expired_vs_manual_invalidations": {
                "expired": self._evict_replaced,
                "manual": self._evict_manual,
                "clear": self._evict_clear,
            },
        }


# Global singleton
cache = TTLCache()
