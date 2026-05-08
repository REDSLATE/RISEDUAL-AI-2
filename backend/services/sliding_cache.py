"""
Sliding-TTL cache — thread-safe, process-local, O(1) get/set.

Semantics:
    - On set(key, value): stores value with expiry = now + ttl
    - On get(key): if still alive, RESETS expiry to now + ttl and returns value.
      If expired or absent, returns None (caller re-fetches).

This matches "keep-alive while being used" cache semantics: as long as any
caller keeps pulling the same symbol at least once per TTL window, the cached
price lives forever without ever re-hitting the upstream API. Once nobody
touches it for `ttl` seconds, it ages out.

Used by price_provider for quotes, daily bars, and crypto. Shared between
async (`get_quote`) and sync (`get_quote_sync`) variants so the 5-minute
window applies no matter which code path pulled it.

Why in-memory instead of Mongo: Mongo cache already exists for cross-restart
persistence with a fixed TTL. Sliding behaviour needs to be cheap on every
read (we can't afford a Mongo round-trip per quote check), so it lives in
process memory. A single backend process is sufficient for current load;
horizontal scale would need Redis with a per-access EXPIRE refresh.
"""
import threading
import time
from typing import Any, Optional


class SlidingCache:
    """Process-local sliding-TTL cache with optional reset cap.

    `max_resets=None` → unlimited extensions (pure sliding).
    `max_resets=N`    → after N successful get()-induced expiry resets, the
                        entry still serves reads but stops extending its
                        expiry. It ages out at its current `expires_at`
                        timestamp. This puts a soft ceiling on total
                        lifetime: roughly `(max_resets + 1) * ttl`.

    Why the cap: without it, a steadily-polled key lives forever and can
    mask upstream price drift or a stale dataset. The cap guarantees every
    entry eventually refreshes even under continuous load.
    """

    def __init__(self, default_ttl_seconds: float = 300.0,
                 max_entries: int = 5000,
                 max_resets: Optional[int] = None):
        # key -> (value, expires_at_monotonic, reset_count)
        self._store: dict[str, tuple[Any, float, int]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl_seconds
        self._max = max_entries
        self._max_resets = max_resets

    def get(self, key: str, ttl_seconds: Optional[float] = None) -> Optional[Any]:
        """Return value. Refreshes expiry unless the reset cap has been hit."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at, resets = entry
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            if self._max_resets is not None and resets >= self._max_resets:
                # Cap reached — serve the cached value but don't extend expiry.
                # The entry will die at `expires_at`, forcing a fresh fetch.
                return value
            # Bump expiry (sliding TTL on access) + increment reset counter
            self._store[key] = (value, now + ttl, resets + 1)
            return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        """Store value with a fresh TTL window and reset counter of 0."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = time.monotonic()
        with self._lock:
            if len(self._store) >= self._max and key not in self._store:
                # Simple eviction: drop the soonest-expiring entry
                oldest_key = min(self._store.items(), key=lambda kv: kv[1][1])[0]
                self._store.pop(oldest_key, None)
            self._store[key] = (value, now + ttl, 0)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def stats(self) -> dict:
        with self._lock:
            now = time.monotonic()
            alive = sum(1 for _, exp, _ in self._store.values() if exp > now)
            at_cap = 0
            if self._max_resets is not None:
                at_cap = sum(
                    1 for _, exp, rc in self._store.values()
                    if exp > now and rc >= self._max_resets
                )
            return {
                "size": len(self._store),
                "alive": alive,
                "at_reset_cap": at_cap,
                "ttl_seconds": self._default_ttl,
                "max_resets": self._max_resets,
            }


# Singleton for market data: 5 min sliding window, max 2 resets.
# Total lifetime ≈ 3 × 5 = 15 min under continuous polling (one initial
# fetch + 2 resets, then expires). Forces a refresh before stale prices
# leak into decisions.
price_cache = SlidingCache(
    default_ttl_seconds=300.0,
    max_entries=5000,
    max_resets=2,
)
