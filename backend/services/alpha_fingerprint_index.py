"""TTL-scoped index management for the fingerprint dedup collection.

Kept in a separate module (not in ``alpha_fingerprint``) so the
pure-math hashing function stays free of ``motor`` / async
dependencies and can be unit-tested with zero Mongo mocking.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "alpha_intent_fingerprints"
# 24-hour TTL. Fingerprints only matter for the dedup window (default
# 15 minutes) but a 24h buffer gives us history for the admin UI
# without unbounded growth.
TTL_SECONDS = 24 * 3600


async def ensure_indexes(db: Any) -> None:
    if db is None:
        return
    try:
        # TTL — auto-expire old fingerprints.
        await db[COLLECTION].create_index(
            "created_at", expireAfterSeconds=TTL_SECONDS,
        )
        # Compound index for the dedup lookup path.
        await db[COLLECTION].create_index(
            [("fingerprint", 1), ("created_at", -1)],
        )
        # Symbol lookups for the admin UI (which fingerprints did
        # AAPL fire today?).
        await db[COLLECTION].create_index(
            [("symbol", 1), ("created_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_fingerprint_index] index create failed: %s", exc)


__all__ = ["COLLECTION", "TTL_SECONDS", "ensure_indexes"]
