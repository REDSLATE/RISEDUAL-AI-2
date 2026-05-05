"""AICacheService — MongoDB-backed cache with sliding TTL for expensive AI results.

Supports:
- Sliding TTL: extends expiration on each read
- Max age: hard expiration regardless of reads
- Stale fallback: returns expired data on error
- Survives server restarts and deployments
"""
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Union

logger = logging.getLogger(__name__)


class AICacheService:
    COLLECTION = "ai_cache"

    def __init__(self, db):
        self.db = db

    @classmethod
    async def ensure_indexes(cls, db) -> None:
        """Create the indexes the cache hot-path depends on.

        Each index is created in its own try/except so a conflict on
        one (e.g. an old non-TTL ``expires_at_1`` left over from a
        prior deployment) doesn't short-circuit the rest.

        Indexes created:

        * **Unique on ``cache_key``** (``cache_key_1``) — every read
          does ``find_one({"cache_key": ...})`` and every write does
          ``replace_one({"cache_key": ...}, upsert=True)``. Without
          uniqueness, two concurrent writes for the same key can race
          and produce duplicate documents.
        * **TTL on ``expires_at``** (``expires_at_ttl``) — Mongo
          background-deletes expired entries with no app cooperation.
          ``expireAfterSeconds=0`` means "delete as soon as the
          stored timestamp is in the past" (the timestamp itself is
          the deadline). Any pre-existing non-TTL ``expires_at_*``
          index is dropped first so the upgrade is idempotent.
        * **``created_at`` descending** (``created_at_desc``) — used
          by ``get_stale`` (sorted fallback) and the
          ``max_age_seconds`` filter.

        Also opportunistically drops the stale ``endpoint_1`` index
        from a prior schema if it lingers in the deployed cluster.
        """
        if db is None:
            return
        collection = db[cls.COLLECTION]

        # 1. unique index on cache_key. Use the conventional Mongo
        # default name so re-running matches whatever a prior deploy
        # auto-named via ``create_index("cache_key", unique=True)``.
        try:
            await collection.create_index(
                "cache_key", unique=True, name="cache_key_1",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AICacheService cache_key index: {e}")

        # 2. TTL index on expires_at — drop any non-TTL variant first
        # so the upgrade is safe on existing clusters.
        try:
            info = await collection.index_information()
            for name, spec in info.items():
                if name == "_id_":
                    continue
                key = spec.get("key") or []
                if (
                    key == [("expires_at", 1)]
                    and "expireAfterSeconds" not in spec
                ):
                    await collection.drop_index(name)
            await collection.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="expires_at_ttl",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AICacheService expires_at TTL index: {e}")

        # 3. created_at descending — for get_stale + max_age_seconds.
        try:
            await collection.create_index(
                [("created_at", -1)], name="created_at_desc",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AICacheService created_at index: {e}")

        # 4. Best-effort cleanup of stale-schema indexes.
        try:
            info = await collection.index_information()
            for stale in ("endpoint_1",):
                if stale in info:
                    await collection.drop_index(stale)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AICacheService stale-index cleanup: {e}")


    def build_key(self, namespace: str, params: Union[dict, None] = None, **kwargs) -> str:
        """Build a deterministic cache key from namespace + params."""
        merged = {**(params or {}), **kwargs}
        parts = [namespace]
        for k in sorted(merged.keys()):
            parts.append(f"{k}={merged[k]}")
        raw = ":".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    async def get(self, cache_key: str, ttl_seconds: int = 300,
                  sliding: bool = False, max_age_seconds: int = 0) -> Optional[dict[str, Any]]:
        """Retrieve a cached result. Supports sliding TTL and max age.

        Args:
            cache_key: The cache key to look up
            ttl_seconds: TTL for sliding window (only used if sliding=True)
            sliding: If True, extend expires_at on each read
            max_age_seconds: Hard max age from created_at (0 = no limit)
        """
        if self.db is None:
            return None
        try:
            now = datetime.now(timezone.utc)
            query = {"cache_key": cache_key, "expires_at": {"$gt": now}}

            # If max_age set, also enforce hard creation time limit
            if max_age_seconds > 0:
                oldest = now - timedelta(seconds=max_age_seconds)
                query["created_at"] = {"$gt": oldest}

            doc = await self.db[self.COLLECTION].find_one(query, {"_id": 0})
            if not doc:
                return None

            # Sliding TTL: extend expiration on read
            if sliding and ttl_seconds > 0:
                new_expires = now + timedelta(seconds=ttl_seconds)
                await self.db[self.COLLECTION].update_one(
                    {"cache_key": cache_key},
                    {"$set": {"expires_at": new_expires}},
                )

            return doc.get("data")
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
            return None

    async def get_stale(self, cache_key: str) -> Optional[dict[str, Any]]:
        """Retrieve a cached result even if expired (fallback on error)."""
        if self.db is None:
            return None
        try:
            doc = await self.db[self.COLLECTION].find_one(
                {"cache_key": cache_key},
                {"_id": 0},
                sort=[("created_at", -1)],
            )
            return doc.get("data") if doc else None
        except Exception as e:
            logger.warning(f"Cache get_stale error: {e}")
            return None

    async def set(self, cache_key: str, namespace: str, data: dict[str, Any],
                  ttl_seconds: int = 300, meta: Optional[dict] = None) -> None:
        """Store a result in cache with TTL."""
        if self.db is None:
            return
        now = datetime.now(timezone.utc)
        doc = {
            "cache_key": cache_key,
            "namespace": namespace,
            "data": data,
            "created_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
            "meta": meta or {},
        }
        try:
            await self.db[self.COLLECTION].replace_one(
                {"cache_key": cache_key},
                doc,
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Cache set error: {e}")

    async def invalidate(self, cache_key: str) -> None:
        """Remove a cached entry."""
        if self.db is None:
            return
        try:
            await self.db[self.COLLECTION].delete_one({"cache_key": cache_key})
        except Exception as e:
            logger.warning(f"Cache invalidate error: {e}")

    async def cleanup_expired(self) -> int:
        """Remove all expired entries."""
        if self.db is None:
            return 0
        try:
            result = await self.db[self.COLLECTION].delete_many(
                {"expires_at": {"$lt": datetime.now(timezone.utc)}}
            )
            return result.deleted_count
        except Exception as e:
            logger.warning(f"Cache cleanup error: {e}")
            return 0

    async def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        if self.db is None:
            return {"total": 0, "active": 0}
        try:
            total = await self.db[self.COLLECTION].count_documents({})
            active = await self.db[self.COLLECTION].count_documents(
                {"expires_at": {"$gt": datetime.now(timezone.utc)}}
            )
            return {"total": total, "active": active}
        except Exception:
            return {"total": 0, "active": 0}
