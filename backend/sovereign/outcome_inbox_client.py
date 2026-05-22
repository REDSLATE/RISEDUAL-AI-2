"""Sovereign Outcome Inbox Client (sidecar-side, 2026-05-22).

The READER half of the outcome bridge. Lives in the sidecar
process and pulls resolved outcomes that ``services/sovereign_
outcome_bridge.py`` enqueued from the backend.

Design:
* Best-effort. If Mongo is unreachable, drain returns ``[]`` and
  the sidecar continues — outcomes will catch up on the next tick.
* Each drained row is stamped ``drained=True`` + ``drained_at`` so
  it isn't re-pulled.
* Capped at ``limit`` rows per call (default 20) to bound the
  tick's wall-time.
* Brain-tagged: a sidecar only drains its own brain's rows.

Minimal motor dependency — the sidecar already has motor available
via the shared /root/.venv. The module is wrapped so a missing
MONGO_URL env never breaks tick().
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("sovereign.outcome_inbox")


COLLECTION = "sovereign_outcomes_inbox"
_CACHED_DB: Any = None


def _get_db() -> Optional[Any]:
    """Lazily build a motor client. Returns ``None`` if the
    required env vars are missing — the sidecar logs and skips."""
    global _CACHED_DB
    if _CACHED_DB is not None:
        return _CACHED_DB
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not db_name:
        logger.debug(
            "[outcome_inbox] MONGO_URL or DB_NAME missing — skipping drain"
        )
        return None
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
        _CACHED_DB = client[db_name]
        return _CACHED_DB
    except Exception as exc:  # noqa: BLE001
        logger.warning("[outcome_inbox] motor init failed: %s", exc)
        return None


async def drain_pending_for_brain(
    brain: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Pull up to ``limit`` un-drained outcomes for ``brain``,
    stamp each as drained, and return the rows in resolution
    order (oldest first).

    Returns ``[]`` if Mongo unreachable / no pending rows.
    """
    db = _get_db()
    if db is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        cursor = db[COLLECTION].find(
            {"brain": brain, "drained": {"$ne": True}},
            {"_id": 0},
        ).sort("resolved_at", 1).limit(int(limit))
        async for r in cursor:
            rows.append(r)
        if not rows:
            return []
        # Stamp drained=True so we don't re-pull. Best-effort; if
        # the stamp fails the row would be re-pulled next tick and
        # LocalState.add_outcome will accept the duplicate
        # (it's an append; capped at _MAX_OUTCOMES).
        now = datetime.now(timezone.utc)
        await db[COLLECTION].update_many(
            {
                "brain": brain,
                "trade_id": {"$in": [r["trade_id"] for r in rows]},
                "drained": {"$ne": True},
            },
            {"$set": {"drained": True, "drained_at": now}},
        )
        logger.info(
            "[outcome_inbox] drained %d outcomes for brain=%s",
            len(rows), brain,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[outcome_inbox] drain failed: %s", exc)
        return []
    return rows


def drain_pending_for_brain_sync(brain: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Synchronous wrapper for the sidecar's sync tick loop.

    Each call creates a fresh event loop AND a fresh motor client.
    Caching motor across loops causes ``Event loop is closed``
    errors because motor pins its sockets to the loop that
    created them. Re-binding per call costs ~5ms and is the
    correct trade-off for a 60-second tick cadence.
    """
    import asyncio
    global _CACHED_DB
    try:
        # Invalidate the cached client so a fresh one is built
        # inside the new event loop.
        _CACHED_DB = None
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                drain_pending_for_brain(brain, limit=limit),
            )
        finally:
            # Tear down the motor client BEFORE closing the loop so
            # its background sockets shut down on the right loop.
            try:
                if _CACHED_DB is not None:
                    client = _CACHED_DB.client
                    client.close()
            except Exception:  # noqa: BLE001
                pass
            _CACHED_DB = None
            loop.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[outcome_inbox] sync wrapper failed: %s", exc)
        return []


__all__ = [
    "COLLECTION",
    "drain_pending_for_brain",
    "drain_pending_for_brain_sync",
]
