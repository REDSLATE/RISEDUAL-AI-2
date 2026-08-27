"""Append-only Wave Intelligence observation log (2026-02).

Every Alpha tick that has enough bars to run Wave Intelligence writes
the resulting observation here — mode, sub-scores, bias, reason codes,
symbol, timeframe.

Why persist EVERY observation (not just DANGER_PAUSE / vetoes)?
    We need the full distribution to later ask "did TREND_FOLLOW
    setups pay better than RANGE_GRID ones?" or "how often does
    DANGER_PAUSE resolve back to WAIT vs TREND_FOLLOW?" Those
    questions require the negatives too.

Storage: Mongo collection ``alpha_wave_observations``. 30-day TTL
matches ``alpha_pattern_research`` for join-friendly retention.

Design invariants:
    * Fire-and-forget writer — never blocks or crashes a tick
    * Immutable rows — append-only, no updates
    * Small write cost — one document per (symbol, tick)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "alpha_wave_observations"
TTL_DAYS = 30


async def ensure_indexes(db: Any) -> None:
    if db is None:
        return
    try:
        await db[COLLECTION].create_index(
            "created_at", expireAfterSeconds=TTL_DAYS * 24 * 3600,
        )
        await db[COLLECTION].create_index(
            [("symbol", 1), ("created_at", -1)],
        )
        await db[COLLECTION].create_index(
            [("mode", 1), ("created_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wave_persistence] index create failed: %s", exc)


async def record_observation(
    db: Any,
    *,
    observation: dict,
    tick_id: Optional[str] = None,
) -> bool:
    """Persist a single ``WaveObservation.to_dict()`` payload.

    Returns True on success. Any exception is swallowed and returns
    False — Wave logging must never block a tick.
    """
    if db is None or not observation:
        return False
    try:
        doc = dict(observation)  # shallow copy — safe, keys are primitives
        doc["created_at"] = datetime.now(timezone.utc)
        if tick_id is not None:
            doc["tick_id"] = tick_id
        await db[COLLECTION].insert_one(doc)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wave_persistence] insert failed: %s", exc)
        return False


async def recent(
    db: Any,
    *,
    symbol: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    if db is None:
        return []
    q: dict[str, Any] = {}
    if symbol:
        q["symbol"] = symbol.upper()
    if mode:
        q["mode"] = mode
    limit = max(1, min(int(limit or 100), 1000))
    try:
        cursor = db[COLLECTION].find(q, {"_id": 0}).sort(
            "created_at", -1,
        ).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wave_persistence] read failed: %s", exc)
        return []


async def mode_counts(
    db: Any,
    *,
    since_hours: int = 24,
) -> dict:
    """Roll up mode counts over the last ``since_hours`` for the admin UI."""
    if db is None:
        return {}
    from datetime import timedelta as _td
    since = datetime.now(timezone.utc) - _td(hours=max(1, int(since_hours)))
    try:
        cursor = db[COLLECTION].aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": "$mode", "count": {"$sum": 1}}},
        ])
        rows = await cursor.to_list(length=32)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wave_persistence] rollup failed: %s", exc)
        return {}
    return {row["_id"]: int(row.get("count") or 0) for row in rows if row.get("_id")}


async def danger_leaderboard(
    db: Any,
    *,
    since_hours: int = 4,
    limit: int = 10,
) -> list[dict]:
    """Top symbols by max danger score recently — for the admin dashboard."""
    if db is None:
        return []
    from datetime import timedelta as _td
    since = datetime.now(timezone.utc) - _td(hours=max(1, int(since_hours)))
    try:
        cursor = db[COLLECTION].aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": "$symbol",
                "max_danger": {"$max": "$scores.danger"},
                "latest_mode": {"$last": "$mode"},
                "n": {"$sum": 1},
            }},
            {"$sort": {"max_danger": -1}},
            {"$limit": int(limit)},
        ])
        rows = await cursor.to_list(length=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wave_persistence] leaderboard failed: %s", exc)
        return []
    return [
        {
            "symbol": r.get("_id"),
            "max_danger": float(r.get("max_danger") or 0.0),
            "latest_mode": r.get("latest_mode"),
            "observations": int(r.get("n") or 0),
        }
        for r in rows if r.get("_id")
    ]


__all__ = [
    "COLLECTION",
    "TTL_DAYS",
    "ensure_indexes",
    "record_observation",
    "recent",
    "mode_counts",
    "danger_leaderboard",
]
