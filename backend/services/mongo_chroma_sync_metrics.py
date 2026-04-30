"""Mongo→Chroma sync observability.

Two surfaces:

1. **Skip counters** (in-process) — how many save attempts were
   skipped, broken down by reason. Resets on restart; that's
   acceptable since they're an operational smoke signal, not a
   long-term metric. The drift detector itself measures absolute
   state (Mongo count vs Chroma count) so regressions surface even
   after a restart.

2. **Last-rebuild metadata** (in-process *and* Mongo) — single-doc
   collection ``mongo_chroma_sync_state`` so the timestamp
   *survives backend restarts*. The drift detector + alert watcher
   need this to distinguish "8% drift right after rebuild =
   expected" from "8% drift one hour after rebuild = actively
   broken". Without persistence, every hot-reload wiped the
   timestamp and the alert-watcher's "time since rebuild"
   reasoning was unreliable across deploys.

The ``record_skip(reason)`` helper is callable from anywhere on the
sync path — every site that previously had ``except Exception:
skipped += 1`` now logs at WARN and bumps the counter so the next
"ChromaDB drifted from MongoDB" incident shows up in observability
instead of in a screenshot four months later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)


# ── Storage layout ─────────────────────────────────────────────────

# Single-document collection: ``{"_id": "mongo_chroma", ...}``. One
# row, upserted on every rebuild. Cheap to read; survives restarts.
_STATE_COLLECTION = "mongo_chroma_sync_state"
_STATE_DOC_ID = "mongo_chroma"


def _get_db():
    """Lazy-import the Mongo client. Avoids a circular import on
    module load (server.py imports several things from services/)
    and makes this module testable without spinning up the FastAPI
    app."""
    try:
        from server import db
        return db
    except Exception:  # noqa: BLE001
        return None


# ── Skip counters (in-process) ─────────────────────────────────────

_skipped_total: dict[str, int] = {}


def record_skip(reason: str, *, doc_id: Optional[str] = None,
                exc: Optional[BaseException] = None) -> None:
    """Bump the skip counter and log at WARN.

    ``reason`` is a short stable label — keep the cardinality low
    (handful of values) so the counter remains useful. Examples:
    ``"chroma_upsert_failed"``, ``"missing_symbol"``,
    ``"date_coerce_failed"``.
    """
    _skipped_total[reason] = _skipped_total.get(reason, 0) + 1
    logger.warning(
        "[mongo_chroma_sync] skipped reason=%s doc_id=%s exc=%s/%s",
        reason,
        (doc_id or "")[:32],
        type(exc).__name__ if exc else "-",
        (str(exc)[:120] if exc else "-"),
    )


def get_skip_counters() -> dict[str, int]:
    """Read-only snapshot for the drift endpoint."""
    return dict(_skipped_total)


def reset_counters() -> None:
    """Reset counters — used by tests; not exposed via HTTP."""
    _skipped_total.clear()


# ── Last-rebuild metadata (Mongo-backed + in-process cache) ────────

# In-process cache. Read-through populated from Mongo on first
# ``get_last_rebuild()`` after a process restart so subsequent
# reads don't hammer Mongo for what's essentially a configuration
# value. Invalidated on every ``mark_rebuild`` write.
_cache: Optional[dict[str, Any]] = None


async def mark_rebuild(*, rebuilt: int, skipped: int,
                       since: Optional[str] = None) -> None:
    """Stamp the last-rebuild metadata.

    Writes to both the in-process cache (for warm reads in the
    same process) and the Mongo state doc (so the timestamp
    survives backend restarts and is visible to other workers).
    """
    global _cache
    now = datetime.now(timezone.utc)
    payload = {
        "last_rebuild_at": now,
        "last_rebuild_summary": {
            "rebuilt": int(rebuilt),
            "skipped": int(skipped),
            "since": since,
        },
    }
    _cache = payload

    db = _get_db()
    if db is None:
        # No Mongo connection — fall through with cache only. The
        # drift endpoint will still return the in-process value.
        logger.warning(
            "[mongo_chroma_sync] mark_rebuild: db unavailable, "
            "stamp held in-process only"
        )
        return
    try:
        await db[_STATE_COLLECTION].update_one(
            {"_id": _STATE_DOC_ID},
            {"$set": {**payload, "_id": _STATE_DOC_ID}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Persistence is best-effort — the rebuild has already
        # succeeded by the time we're called, so a Mongo write
        # failure must not raise. The in-process cache still
        # serves the current process; the next successful rebuild
        # will overwrite the missing row.
        logger.warning(
            "[mongo_chroma_sync] mark_rebuild persist failed: %s", exc
        )


async def get_last_rebuild() -> dict[str, Any]:
    """Read the last-rebuild metadata.

    Prefers the in-process cache; on cold start (cache miss) reads
    from Mongo and warms the cache. Returns ISO-formatted strings
    so the response is JSON-serializable straight to the wire.
    """
    global _cache
    if _cache is None:
        # Cold start — try Mongo.
        db = _get_db()
        if db is not None:
            try:
                doc = await db[_STATE_COLLECTION].find_one(
                    {"_id": _STATE_DOC_ID}, {"_id": 0}
                )
                if doc:
                    # Mongo strips tzinfo on round-trip; ensure_utc
                    # re-tags before we serialise.
                    raw_at = doc.get("last_rebuild_at")
                    at = ensure_utc(raw_at)
                    _cache = {
                        "last_rebuild_at": at,
                        "last_rebuild_summary": doc.get(
                            "last_rebuild_summary"
                        ),
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[mongo_chroma_sync] last_rebuild read failed: %s", exc
                )

    if _cache is None:
        return {"last_rebuild_at": None, "last_rebuild_summary": None}

    at = _cache.get("last_rebuild_at")
    return {
        "last_rebuild_at": at.isoformat() if isinstance(at, datetime) else None,
        "last_rebuild_summary": (
            dict(_cache["last_rebuild_summary"])
            if _cache.get("last_rebuild_summary") else None
        ),
    }


def _reset_cache_for_tests() -> None:
    """Test-only — invalidate the in-process cache so the next
    read goes through Mongo (or the absence of Mongo)."""
    global _cache
    _cache = None
