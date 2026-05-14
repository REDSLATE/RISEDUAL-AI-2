"""In-memory L1 cache in front of AICacheService.

Why this exists:
    AICacheService is MongoDB-backed (durable, survives restarts, has
    TTL). But every cache hit still pays a ~5–15ms round-trip to Mongo.

    For the hottest tickers (top N symbols hit dozens of times per
    minute during market hours), we can layer a tiny in-process LRU
    on top of AICacheService. It stays consistent because writes go
    through both layers; reads check L1 first, fall through to Mongo
    on miss.

Doctrine:
    1. L1 is BEST-EFFORT. Process restarts wipe it; Mongo is canonical.
    2. L1 TTL is shorter than the Mongo TTL by design (60s vs 10min)
       so any cache invalidation that misses L1 expires naturally
       within ~1 min.
    3. L1 is OPT-IN. Routes that want it call ``cache.get_via_l1(...)``;
       the default ``AICacheService.get(...)`` remains Mongo-only.

Public surface:
    InMemoryL1Cache().get(key)
    InMemoryL1Cache().set(key, value, ttl_seconds=60)
    InMemoryL1Cache().invalidate(key)
    InMemoryL1Cache().stats()
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("services.llm_response_cache")

# Defaults tuned for hot-path symbol queries during market hours.
DEFAULT_MAX_ENTRIES = 256
DEFAULT_TTL_SECONDS = 60


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class InMemoryL1Cache:
    """Thread-safe LRU + TTL cache. Singleton-friendly: instantiate once
    at module level in services that want L1 acceleration."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive")
        self._max = max_entries
        self._ttl = default_ttl_seconds
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ── core operations ────────────────────────────────────────────
    def get(self, key: str) -> Any | None:
        """Returns the cached value or None on miss/expiry.

        Expired entries are dropped lazily on access.
        """
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                # Lazy expiry. Drop and treat as miss.
                self._store.pop(key, None)
                self._misses += 1
                return None
            # Move to end (most-recently-used).
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Insert or replace. LRU-evicts the oldest entry if over cap."""
        if not key:
            return
        ttl = int(ttl_seconds if ttl_seconds is not None else self._ttl)
        if ttl <= 0:
            # Calling set() with ttl<=0 is treated as an explicit "do not cache".
            return
        expires_at = time.monotonic() + ttl
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = _Entry(value, expires_at)
                return
            self._store[key] = _Entry(value, expires_at)
            while len(self._store) > self._max:
                self._store.popitem(last=False)
                self._evictions += 1

    def invalidate(self, key: str) -> bool:
        """Returns True if a value was removed."""
        if not key:
            return False
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    # ── observability ──────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        """Returns a non-blocking snapshot."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._store),
                "max_entries": self._max,
                "default_ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": (self._hits / total) if total else 0.0,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# Module-level singleton — services import this directly.
hypothesis_l1_cache = InMemoryL1Cache(
    max_entries=256, default_ttl_seconds=60,
)
