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
    "orchestrator_bias_feedback",
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


# ─── Feedback Loop ──────────────────────────────────────────────────────────
# Default thresholds for "suppress this (asset, direction) for a bit" logic:
# an (asset, direction) pair that's been rejected at least N times AND for
# at least FRACTION% of its recent signals is flagged. These defaults are
# deliberately conservative — the nightly ML retrain is the place we change
# the MODEL; this layer is the faster feedback loop that suppresses known
# bad patterns between retrains.
BIAS_MIN_SAMPLES = 10
BIAS_MIN_REJECTION_RATE = 0.70       # 70% rejection rate = suppress
BIAS_WINDOW_DAYS = 7


async def compute_rejection_bias(
    days: int = BIAS_WINDOW_DAYS,
    min_samples: int = BIAS_MIN_SAMPLES,
    min_rate: float = BIAS_MIN_REJECTION_RATE,
) -> list[dict]:
    """Return (asset, direction) pairs the pipeline has consistently rejected.

    Logic (in Mongo-aggregation terms):
      1. Count rejections per (asset, direction) in the last `days` days.
      2. Count total predictions per (asset, direction) over the same
         window (the denominator — how many times did we consider the
         setup at all).
      3. Flag pairs where rejections/total >= `min_rate` and total >=
         `min_samples`.

    Output rows look like::

        {"asset": "TSLA", "direction": "up",
         "rejections": 24, "attempts": 30, "rate": 0.80,
         "top_source": "ai_signal_validator",
         "top_reason": "ai_verdict=hold (conf=42)"}
    """
    if _db is None:
        return []
    try:
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(days=days)
        since_iso = since.isoformat()

        # ── Rejections by (asset, direction) with dominant source/reason ──
        rej_pipeline = [
            {"$match": {"logged_at": {"$gte": since}, "asset": {"$ne": ""}}},
            {"$group": {
                "_id": {"asset": "$asset", "direction": "$direction"},
                "rejections": {"$sum": 1},
                "sources": {"$push": "$source"},
                "reasons": {"$push": "$reason"},
            }},
        ]
        rej_rows = {}
        async for row in _db.rejected_signals.aggregate(rej_pipeline):
            k = (row["_id"]["asset"], row["_id"]["direction"])
            srcs = row["sources"]
            top_src = max(set(srcs), key=srcs.count) if srcs else None
            rsns = row["reasons"]
            top_reason = max(set(rsns), key=rsns.count) if rsns else None
            rej_rows[k] = {
                "rejections": row["rejections"],
                "top_source": top_src,
                "top_reason": (top_reason or "")[:120],
            }

        # ── Total attempts per (asset, direction) — predictions table ──
        att_pipeline = [
            {"$match": {"created_at": {"$gte": since_iso}}},
            {"$group": {
                "_id": {"asset": "$ticker", "direction": "$direction"},
                "attempts": {"$sum": 1},
            }},
        ]
        flagged = []
        async for row in _db.predictions.aggregate(att_pipeline):
            asset = (row["_id"].get("asset") or "").upper()
            direction = row["_id"].get("direction")
            attempts = row["attempts"]
            rej = rej_rows.get((asset, direction))
            if not rej or attempts < min_samples:
                continue
            rate = rej["rejections"] / attempts
            if rate >= min_rate:
                flagged.append({
                    "asset": asset,
                    "direction": direction,
                    "rejections": rej["rejections"],
                    "attempts": attempts,
                    "rate": round(rate, 3),
                    "top_source": rej["top_source"],
                    "top_reason": rej["top_reason"],
                })

        flagged.sort(key=lambda r: (-r["rate"], -r["attempts"]))
        return flagged
    except Exception as e:
        logger.warning(f"[rejection_log] compute_rejection_bias failed: {e}")
        return []


# Tiny LRU-ish cache so the orchestrator hook doesn't re-aggregate on
# every signal. Recomputed every 15 minutes which is more than fast
# enough for a "trend" signal like this.
_bias_cache: dict = {"snapshot_at": None, "flagged": set()}
_BIAS_CACHE_TTL_SECONDS = 15 * 60


async def get_flagged_pairs() -> set:
    """Fast-path set of `(asset, direction)` tuples the orchestrator can use
    for O(1) suppression checks. Cached for 15 minutes."""
    now = datetime.now(timezone.utc)
    snap = _bias_cache.get("snapshot_at")
    if snap and (now - snap).total_seconds() < _BIAS_CACHE_TTL_SECONDS:
        return _bias_cache["flagged"]
    flagged = await compute_rejection_bias()
    _bias_cache["flagged"] = {(r["asset"], r["direction"]) for r in flagged}
    _bias_cache["snapshot_at"] = now
    return _bias_cache["flagged"]
