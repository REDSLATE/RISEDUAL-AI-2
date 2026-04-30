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
from datetime import datetime, timedelta, timezone
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


# ── Drift history (Mongo-backed time-series for sparkline) ─────────

# A tiny circular log of drift snapshots so the operator dashboard
# can show "drift over the last 24 hours" rather than just the
# instantaneous value. The 5-minute alert watcher writes one row
# per tick; a TTL index expires rows after 7 days so the collection
# stays bounded (~2k docs steady-state).
_HISTORY_COLLECTION = "mongo_chroma_drift_history"
_HISTORY_TTL_DAYS = 7


async def ensure_history_indexes() -> None:
    """Best-effort index creation for the drift-history collection.

    Two indexes:
    * ``ts`` descending — sparkline queries scan the recent tail.
    * ``ts`` TTL (7 days) — bounds the collection without an
      explicit cleanup job.
    """
    db = _get_db()
    if db is None:
        return
    try:
        await db[_HISTORY_COLLECTION].create_index(
            [("ts", -1)], name="drift_history_ts_desc"
        )
        await db[_HISTORY_COLLECTION].create_index(
            "ts",
            expireAfterSeconds=_HISTORY_TTL_DAYS * 24 * 3600,
            name="drift_history_ts_ttl",
        )
    except Exception as exc:  # noqa: BLE001
        # Index conflicts (e.g. the TTL was created with a different
        # ``expireAfterSeconds``) are non-fatal — the collection
        # still works without the perfect index, and a manual drop
        # is the right fix path. Don't break startup.
        logger.warning(
            "[mongo_chroma_sync] drift history index ensure failed: %s",
            exc,
        )


async def record_drift_sample(
    *,
    drift_pct: float,
    drift: int,
    mongo_total: int,
    chroma_total: int,
    recommendation: Optional[str] = None,
) -> None:
    """Persist a single drift snapshot for the dashboard sparkline.

    Called by the 5-minute alert watcher tick. Failure is non-fatal
    — the watcher must keep running even if Mongo write fails.
    """
    db = _get_db()
    if db is None:
        return
    try:
        await db[_HISTORY_COLLECTION].insert_one({
            "ts": datetime.now(timezone.utc),
            "drift_pct": float(drift_pct),
            "drift": int(drift),
            "mongo_total": int(mongo_total),
            "chroma_total": int(chroma_total),
            "recommendation": recommendation,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[mongo_chroma_sync] drift history record failed: %s", exc
        )


async def get_drift_history(hours: int = 24) -> list[dict[str, Any]]:
    """Read drift snapshots over the lookback window.

    Returns rows newest-first with ISO-formatted timestamps so the
    response is JSON-serializable straight to the wire. Bounded
    result size; the TTL index keeps the collection capped.
    """
    db = _get_db()
    if db is None:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        cursor = (
            db[_HISTORY_COLLECTION]
            .find({"ts": {"$gte": cutoff}}, {"_id": 0})
            .sort("ts", 1)  # ascending so the sparkline reads left→right
        )
        out: list[dict[str, Any]] = []
        async for doc in cursor:
            ts = ensure_utc(doc.get("ts"))
            out.append({
                "ts": ts.isoformat() if isinstance(ts, datetime) else None,
                "drift_pct": float(doc.get("drift_pct", 0.0)),
                "drift": int(doc.get("drift", 0)),
                "mongo_total": int(doc.get("mongo_total", 0)),
                "chroma_total": int(doc.get("chroma_total", 0)),
                "recommendation": doc.get("recommendation"),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[mongo_chroma_sync] drift history read failed: %s", exc
        )
        return []
