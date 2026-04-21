"""Rejection Log — captures every signal the pipeline rejects.

Every rejection point in the trading pipeline (ML gate, calibration stats,
tier locks, AI Auditor veto, risk guards) can call :func:`log_rejected`
with a structured reason so:

  1. The nightly ML retrain can consume these as "hard negatives" — trades
     the current system filtered out that we can later verify as right or
     wrong calls.
  2. The admin dashboard can surface "why did X get rejected today?"
  3. Pattern drift is visible — a sudden spike in rejections from one
     source usually means something upstream broke.

Unlike the original `log_rejected` snippet (in-memory list), this writes
to MongoDB with a TTL so the collection stays lean without manual cleanup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db = None

# Retention: 90 days. Rejections older than that rarely inform retraining
# (market regime has already shifted) so we let MongoDB age them out.
RETENTION_DAYS = 90

# Known rejection sources — kept explicit so downstream queries can filter
# without having to grep every call-site.
SOURCES = frozenset({
    "orchestrator_no_model",
    "orchestrator_no_calibration",
    "orchestrator_tier_locked",
    "ai_signal_validator",
    "risk_guards_veto",
    "risk_circuit_breaker",
    "broker_reject",
    "other",
})


def set_db(database) -> None:
    global _db
    _db = database


async def ensure_indexes() -> None:
    """Create indexes. Idempotent — safe to call on every startup."""
    if _db is None:
        return
    try:
        # TTL expires documents RETENTION_DAYS after `logged_at`.
        await _db.rejected_signals.create_index(
            "logged_at",
            expireAfterSeconds=RETENTION_DAYS * 24 * 3600,
            name="logged_at_ttl",
        )
        await _db.rejected_signals.create_index(
            [("source", 1), ("logged_at", -1)],
            name="source_logged_at",
        )
        await _db.rejected_signals.create_index(
            [("asset", 1), ("logged_at", -1)],
            name="asset_logged_at",
        )
    except Exception as e:
        logger.warning(f"[rejection_log] index setup failed: {e}")


async def log_rejected(
    asset: str,
    direction: Optional[str],
    reason: str,
    source: str,
    meta: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> None:
    """Persist a single rejection. Never raises.

    Args:
        asset:     Ticker or instrument symbol (e.g. ``"AAPL"``).
        direction: ``"up"`` / ``"down"`` / ``"hold"`` / ``None``.
        reason:    Short human-readable explanation.
        source:    One of ``SOURCES`` — the pipeline stage that rejected it.
        meta:      Free-form extras (regime, confidence, features, ...).
        user_id:   Attribution for per-user retraining; None = system level.
    """
    if _db is None:
        return

    if source not in SOURCES:
        logger.warning(f"[rejection_log] unknown source={source!r} — falling back to 'other'")
        source = "other"

    try:
        await _db.rejected_signals.insert_one({
            "asset": (asset or "").upper(),
            "direction": direction,
            "reason": reason,
            "source": source,
            "meta": meta or {},
            "user_id": user_id,
            "logged_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        # Swallow — logging failures must never break the trading pipeline.
        logger.warning(f"[rejection_log] write failed: {e}")


async def recent_rejections(
    limit: int = 50,
    source: Optional[str] = None,
    asset: Optional[str] = None,
) -> list[dict]:
    """Return the most-recent rejections, newest first. Safe for admin UI."""
    if _db is None:
        return []
    query: dict[str, Any] = {}
    if source:
        query["source"] = source
    if asset:
        query["asset"] = asset.upper()
    try:
        cursor = _db.rejected_signals.find(
            query, {"_id": 0}
        ).sort("logged_at", -1).limit(limit)
        return [doc async for doc in cursor]
    except Exception as e:
        logger.warning(f"[rejection_log] recent() failed: {e}")
        return []


async def rejection_stats(hours: int = 24) -> dict:
    """Aggregate rejection counts by source over the past N hours."""
    if _db is None:
        return {"total": 0, "by_source": {}, "window_hours": hours}
    try:
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        pipeline = [
            {"$match": {"logged_at": {"$gte": since}}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]
        by_source: dict[str, int] = {}
        total = 0
        async for row in _db.rejected_signals.aggregate(pipeline):
            by_source[row["_id"]] = row["n"]
            total += row["n"]
        return {"total": total, "by_source": by_source, "window_hours": hours}
    except Exception as e:
        logger.warning(f"[rejection_log] stats() failed: {e}")
        return {"total": 0, "by_source": {}, "window_hours": hours, "error": str(e)}
