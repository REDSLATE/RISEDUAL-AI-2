"""DTD decision replay channel — append-only audit + benchmark mirror.

Every DTD decision (Strategist proposal → Commander → Council → Regime
weights → final fill) is mirrored into ``dtd_decision_replay``. PRD
reads it through the read-only client; nothing in PRD may write back.

Used for:
* perfect audit trails (legal defensibility)
* deterministic backtesting (replay engine consumes this stream)
* candidate-engine benchmarking (re-run the same input tape against
  a different schema)

The mirror is intentionally lossy on internal scratch state but
captures every observable decision-time fact that influenced the fill.
"""
from __future__ import annotations

__domain__ = "DTD"

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db: Any = None
_indexes_ready = False
_index_lock: Optional[asyncio.Lock] = None


def set_db(db: Any) -> None:
    global _db, _index_lock
    _db = db
    _index_lock = asyncio.Lock()


async def _ensure_indexes() -> None:
    global _indexes_ready
    if _indexes_ready or _db is None or _index_lock is None:
        return
    async with _index_lock:
        if _indexes_ready:
            return
        try:
            await _db["dtd_decision_replay"].create_index(
                "decision_id", unique=True, name="dtd_decision_replay_pk",
            )
            await _db["dtd_decision_replay"].create_index([("emitted_at", -1)])
            _indexes_ready = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[dtd-replay] index ensure failed: %s", e)


REQUIRED = ("decision_id", "stage", "symbol")


async def record_decision(payload: dict) -> dict:
    """Append one decision row. Same decision_id twice = no-op.

    payload required: ``decision_id``, ``stage`` (strategist/commander/
    council/regime/fill), ``symbol``. All other keys passed through.
    """
    if _db is None:
        return {"ok": False, "reason": "replay_not_initialised"}
    await _ensure_indexes()
    missing = [k for k in REQUIRED if not payload.get(k)]
    if missing:
        return {"ok": False, "reason": f"missing_fields:{','.join(missing)}"}
    doc = dict(payload)
    doc["emitted_at"] = doc.get("emitted_at") or datetime.now(timezone.utc).isoformat()
    try:
        await _db["dtd_decision_replay"].insert_one(doc.copy())
        return {"ok": True, "deduped": False, "decision_id": doc["decision_id"]}
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "duplicate" in msg or "e11000" in msg:
            return {"ok": True, "deduped": True, "decision_id": doc["decision_id"]}
        logger.warning("[dtd-replay] record failed: %s", e)
        return {"ok": False, "reason": str(e)}


async def read_replay(
    *,
    since: Optional[str] = None,
    stages: Optional[list[str]] = None,
    symbols: Optional[list[str]] = None,
    limit: int = 500,
) -> list[dict]:
    """Read-only replay stream for PRD consumers."""
    if _db is None:
        return []
    await _ensure_indexes()
    query: dict[str, Any] = {}
    if since:
        query["emitted_at"] = {"$gte": since}
    if stages:
        query["stage"] = {"$in": stages}
    if symbols:
        query["symbol"] = {"$in": [s.upper() for s in symbols]}
    cursor = _db["dtd_decision_replay"].find(query, {"_id": 0}).sort(
        "emitted_at", -1,
    ).limit(max(1, min(limit, 5000)))
    return await cursor.to_list(length=limit)
