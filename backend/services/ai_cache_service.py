"""AICacheService — MongoDB-backed cache for expensive AI results.

Stores predictions, search results, and other AI outputs with TTL expiration.
Survives server restarts and deployments.
"""
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class AICacheService:
    COLLECTION = "ai_cache"

    def __init__(self, db):
        self.db = db

    def build_key(self, endpoint: str, **params) -> str:
        """Build a deterministic cache key from endpoint + params."""
        parts = [endpoint]
        for k in sorted(params.keys()):
            parts.append(f"{k}={params[k]}")
        raw = ":".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    async def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a cached result if not expired."""
        if self.db is None:
            return None
        try:
            doc = await self.db[self.COLLECTION].find_one(
                {"cache_key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc)}},
                {"_id": 0},
            )
            return doc
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
            return None

    async def get_stale(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a cached result even if expired (fallback on error)."""
        if self.db is None:
            return None
        try:
            doc = await self.db[self.COLLECTION].find_one(
                {"cache_key": cache_key},
                {"_id": 0},
                sort=[("created_at", -1)],
            )
            return doc
        except Exception as e:
            logger.warning(f"Cache get_stale error: {e}")
            return None

    async def set(self, cache_key: str, endpoint: str, data: Dict[str, Any],
                  ttl_seconds: int = 300, meta: Optional[Dict] = None):
        """Store a result in cache with TTL."""
        if self.db is None:
            return
        now = datetime.now(timezone.utc)
        doc = {
            "cache_key": cache_key,
            "endpoint": endpoint,
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

    async def invalidate(self, cache_key: str):
        """Remove a cached entry."""
        if self.db is None:
            return
        try:
            await self.db[self.COLLECTION].delete_one({"cache_key": cache_key})
        except Exception as e:
            logger.warning(f"Cache invalidate error: {e}")

    async def cleanup_expired(self):
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

    async def stats(self) -> Dict[str, Any]:
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
