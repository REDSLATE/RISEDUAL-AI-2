"""Hypothesis Cache (2026-05-22, P1 Layer 1).

Mongo-backed TTL cache for ``multi_model_hypothesis_service``.
Production motivation: the Emergent LLM key budget was exhausted
in prod, taking the Hypothesis tab down. Caching gives us two
levers:

* Symbol re-queries within the TTL never spend a token.
* When Layer 2 (direct API key fallback) kicks in, the cache
  absorbs the burst so we don't burn through the BYO key either.

Doctrine
--------
* TTL is short (default 10 min) — hypothesis quality decays as
  the market moves. Operators can tune via
  ``HYPOTHESIS_CACHE_TTL_SECONDS``.
* Keys include the brain ``model_key`` AND a data fingerprint
  (news count + first headline hash, etc.) so a stale-data hit
  on the data pipeline doesn't poison the cache.
* Cache misses are silent (just call the LLM). Cache *errors*
  (Mongo down) are also silent — we never block the hypothesis
  on cache health.
* TTL is enforced server-side (Mongo TTL index). The collection
  is created lazily and the index too; the helper is idempotent
  so we won't double-create the index across worker restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "hypothesis_cache"
DEFAULT_TTL_SECONDS = 600  # 10 minutes
_INDEX_ENSURED = False


def _ttl_seconds() -> int:
    try:
        v = int(os.environ.get("HYPOTHESIS_CACHE_TTL_SECONDS", "").strip()
                or DEFAULT_TTL_SECONDS)
        return max(60, min(v, 3600))  # clamp 1m..1h
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def _data_fingerprint(data: dict) -> str:
    """Lightweight stable fingerprint of the macro-data bundle.

    We don't hash the whole bundle (huge, noisy) — we extract a
    few high-signal fields. A cache hit on a stale fingerprint is
    fine; the TTL still bounds staleness.
    """
    try:
        sig = {
            "news_n": len(data.get("news") or []),
            "news_first": ((data.get("news") or [{}])[0].get("title") or "")[:80],
            "events_n": len((data.get("world_events") or {}).get("high_impact_events") or []),
            "social_n": len(data.get("social") or []),
        }
        return hashlib.sha256(
            json.dumps(sig, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "nofingerprint"


def cache_key(symbol: str, model_key: str, data: dict) -> str:
    """Stable cache key. ``symbol`` is normalised upper, model_key
    is the BRAINS entry key, fingerprint scopes the bundle."""
    parts = (
        (symbol or "").upper().strip(),
        (model_key or "").lower().strip(),
        _data_fingerprint(data),
    )
    return ":".join(parts)


async def _ensure_index(db: Any) -> None:
    global _INDEX_ENSURED
    if _INDEX_ENSURED:
        return
    try:
        await db[COLLECTION].create_index(
            "expires_at", expireAfterSeconds=0,
        )
        await db[COLLECTION].create_index("key", unique=True)
        _INDEX_ENSURED = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hypothesis_cache] index ensure failed: %s", exc)


async def get(db: Any, *, key: str) -> Optional[dict]:
    """Return cached hypothesis dict for ``key`` or None.

    Best-effort: any DB error returns None so the caller falls
    through to the LLM call.
    """
    if db is None or not key:
        return None
    try:
        await _ensure_index(db)
        row = await db[COLLECTION].find_one(
            {"key": key}, {"_id": 0, "payload": 1, "expires_at": 1},
        )
        if not row:
            return None
        exp = row.get("expires_at")
        # Defensive: Mongo TTL is "background"; serve stale-safe.
        # ``ensure_utc`` normalises a possibly-naive datetime from Mongo
        # before comparing against ``datetime.now(timezone.utc)`` (the
        # CI invariant ``test_no_unguarded_mongo_datetime_math``).
        from services.datetime_utils import ensure_utc
        exp = ensure_utc(exp)
        if exp and exp < datetime.now(timezone.utc):
            return None
        return row.get("payload")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hypothesis_cache] get failed: %s", exc)
        return None


async def put(db: Any, *, key: str, payload: dict) -> None:
    """Store ``payload`` under ``key`` with the configured TTL.

    Idempotent (upserts). Best-effort — cache failures are
    swallowed so a wedged Mongo never breaks the hypothesis
    response."""
    if db is None or not key or not payload:
        return
    try:
        await _ensure_index(db)
        now = datetime.now(timezone.utc)
        ttl = _ttl_seconds()
        from datetime import timedelta
        expires_at = now + timedelta(seconds=ttl)
        await db[COLLECTION].update_one(
            {"key": key},
            {"$set": {
                "key": key,
                "payload": payload,
                "stored_at": now,
                "expires_at": expires_at,
                "ttl_seconds": ttl,
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hypothesis_cache] put failed: %s", exc)


__all__ = [
    "COLLECTION",
    "DEFAULT_TTL_SECONDS",
    "cache_key",
    "get",
    "put",
]
