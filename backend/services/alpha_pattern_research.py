"""Append-only pattern-research log (2026-02).

Every ``AlphaPatternEngine`` tick that has enough bars for a
classical assessment writes ALL six pattern verdicts here — bullish
and bearish, including ``blocked`` and ``forming`` states.

Why persist blocked/forming rows too?
    Without them we can't ever answer "how often does a *forming*
    inverse-head-and-shoulders actually confirm?" or "what's the
    invalidation rate on double-bottoms in chop?" Those answers
    are the whole reason to record research.

Storage: Mongo collection ``alpha_pattern_research``. TTL-safe (no
runaway growth on symbols we scan hundreds of times a day) via a
30-day background TTL index. One row per ``(symbol, pattern, tick)``.

Read side: ``GET /api/admin/alpha/pattern-research`` returns the
newest N rows, optionally filtered by symbol/pattern/state.

Design invariants:

* Fire-and-forget writer — logging must NEVER block or crash the
  Alpha tick. All exceptions are swallowed with a debug-level log.
* Immutable rows — no upsert, no update. This is an audit log, not
  a state store.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from services.alpha_classical_patterns import PatternAssessment

logger = logging.getLogger(__name__)

COLLECTION = "alpha_pattern_research"
TTL_DAYS = 30


async def ensure_indexes(db: Any) -> None:
    """Create the indexes we rely on for lookups + TTL.

    Idempotent — safe to call from startup every time.
    """
    if db is None:
        return
    try:
        # TTL — expire rows after 30 days so the collection can't
        # bloat unboundedly.
        await db[COLLECTION].create_index(
            "created_at", expireAfterSeconds=TTL_DAYS * 24 * 3600,
        )
        # Compound index for the read-side filters.
        await db[COLLECTION].create_index(
            [("symbol", 1), ("created_at", -1)],
        )
        await db[COLLECTION].create_index(
            [("pattern", 1), ("state", 1), ("created_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[pattern_research] index create failed: %s", exc)


async def record_assessments(
    db: Any,
    *,
    symbol: str,
    assessments: Iterable[PatternAssessment],
    timeframe: str = "daily",
    tick_id: Optional[str] = None,
) -> int:
    """Persist all ``PatternAssessment`` rows for a single tick.

    Returns the number of rows written (0 on any failure).
    """
    if db is None or not symbol:
        return 0
    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    for a in assessments:
        try:
            docs.append({
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "tick_id": tick_id,
                "pattern": a.pattern,
                "state": a.state,
                "confidence": float(a.confidence),
                "latest_close": float(a.latest_close),
                "neckline_or_support": (
                    float(a.neckline_or_support)
                    if a.neckline_or_support is not None else None
                ),
                "invalidation_level": (
                    float(a.invalidation_level)
                    if a.invalidation_level is not None else None
                ),
                "detail": a.detail,
                "criteria": a.criteria,
                "created_at": now,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[pattern_research] skip malformed assessment: %s", exc)
    if not docs:
        return 0
    try:
        await db[COLLECTION].insert_many(docs, ordered=False)
        return len(docs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[pattern_research] insert failed: %s", exc)
        return 0


async def recent(
    db: Any,
    *,
    symbol: Optional[str] = None,
    pattern: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Read newest research rows (append-only log)."""
    if db is None:
        return []
    q: dict[str, Any] = {}
    if symbol:
        q["symbol"] = symbol.upper()
    if pattern:
        q["pattern"] = pattern
    if state:
        q["state"] = state
    limit = max(1, min(int(limit or 100), 1000))
    try:
        cursor = db[COLLECTION].find(q, {"_id": 0}).sort(
            "created_at", -1,
        ).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[pattern_research] read failed: %s", exc)
        return []


async def counts_by_state(db: Any, *, symbol: Optional[str] = None) -> dict:
    """Group counts by (pattern, state) for quick UI rollups."""
    if db is None:
        return {}
    match: dict[str, Any] = {}
    if symbol:
        match["symbol"] = symbol.upper()
    pipe: list[dict] = []
    if match:
        pipe.append({"$match": match})
    pipe.append({"$group": {
        "_id": {"pattern": "$pattern", "state": "$state"},
        "count": {"$sum": 1},
    }})
    try:
        cursor = db[COLLECTION].aggregate(pipe)
        rows = await cursor.to_list(length=200)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[pattern_research] rollup read failed: %s", exc)
        return {}
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        p = row["_id"].get("pattern", "unknown")
        s = row["_id"].get("state", "unknown")
        out.setdefault(p, {})[s] = int(row.get("count") or 0)
    return out


__all__ = [
    "COLLECTION",
    "TTL_DAYS",
    "ensure_indexes",
    "record_assessments",
    "recent",
    "counts_by_state",
]
