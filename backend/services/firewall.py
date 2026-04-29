"""DTD → PRD firewall — the only ingress from execution to analytics.

Implements §4 of ``RISEDUAL_DUAL_STACK_SPEC.md``. Three contracts:

1. ``publish_resolved(payload, *, settle_seconds)`` — DTD callers
   (paper_trade_closer, prediction_tracker) cross the boundary by
   calling this function. The function appends to
   ``prd_resolved_outcomes`` with a unique index on ``outcome_id``,
   refuses payloads whose ``resolved_at`` is younger than
   ``settle_seconds`` ago (i.e., not yet settled), and never mutates
   existing rows.

2. ``read_resolved(...)`` — PRD callers (ai_core_autowire) read
   resolved outcomes through this function. No write API is exposed.

3. Cross-domain bypass is impossible: the underlying collection is
   tagged ``PRD`` (so ``DtdClient`` is denied), and the firewall
   itself is tagged ``BRIDGE`` (so it can write through the
   ``BridgeCalibrationClient`` constraints — but the publish path
   uses a dedicated motor collection write since it's a same-domain
   write that PRD owns).

The settlement window prevents a buggy DTD caller from "publishing"
an unresolved trade and contaminating PRD analytics with
not-yet-final state.
"""
from __future__ import annotations

__domain__ = "BRIDGE"

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

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
            await _db["prd_resolved_outcomes"].create_index(
                "outcome_id", unique=True, name="prd_resolved_outcomes_pk",
            )
            await _db["prd_resolved_outcomes"].create_index([("resolved_at", -1)])
            _indexes_ready = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[firewall] index ensure failed: %s", e)


def _to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


def _parse_iso(ts: Any) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ── Publish (DTD → PRD ingress) ──────────────────────────────────────


REQUIRED_FIELDS = ("outcome_id", "source", "symbol", "outcome", "resolved_at")


async def publish_resolved(
    payload: dict,
    *,
    settle_seconds: int = 0,
) -> dict:
    """Append one resolved outcome to the immutable ledger.

    ``payload`` MUST contain:
        * ``outcome_id``  — globally unique key (e.g. trade-id or pred-id)
        * ``source``      — origin module (paper_trades / predictions / ...)
        * ``symbol``
        * ``outcome``     — win|loss|flat (canonical)
        * ``resolved_at`` — ISO timestamp of settlement

    Optional fields are passed through unchanged; the ledger row is
    sealed at insert time and never updated thereafter.

    ``settle_seconds`` is the minimum age of ``resolved_at`` before
    publication is permitted. Default 0 = "as soon as it resolves".
    Callers handling longer-tail resolutions (e.g., 1-week predictions)
    can require a longer cool-off.
    """
    if _db is None:
        return {"ok": False, "reason": "firewall_not_initialised"}
    await _ensure_indexes()

    missing = [k for k in REQUIRED_FIELDS if not payload.get(k)]
    if missing:
        return {"ok": False, "reason": f"missing_fields:{','.join(missing)}"}

    resolved_dt = _parse_iso(payload.get("resolved_at"))
    if resolved_dt is None:
        return {"ok": False, "reason": "invalid_resolved_at"}
    age = (datetime.now(timezone.utc) - resolved_dt).total_seconds()
    if age < settle_seconds:
        return {
            "ok": False,
            "reason": "not_yet_settled",
            "age_seconds": age,
            "required_seconds": settle_seconds,
        }

    doc = dict(payload)
    doc["resolved_at"] = _to_iso(resolved_dt)
    doc["sealed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        await _db["prd_resolved_outcomes"].insert_one(doc.copy())
        return {"ok": True, "deduped": False, "outcome_id": doc["outcome_id"]}
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "duplicate" in msg or "e11000" in msg:
            return {"ok": True, "deduped": True, "outcome_id": doc["outcome_id"]}
        logger.warning("[firewall] publish failed: %s", e)
        return {"ok": False, "reason": str(e)}


# ── Read (PRD egress) ────────────────────────────────────────────────


async def read_resolved(
    *,
    since: Optional[str] = None,
    sources: Optional[Iterable[str]] = None,
    symbols: Optional[Iterable[str]] = None,
    limit: int = 500,
) -> list[dict]:
    """Read resolved outcomes from the immutable ledger.

    No write side is exposed. Filters are AND-combined.
    """
    if _db is None:
        return []
    await _ensure_indexes()
    query: dict[str, Any] = {}
    if since:
        query["resolved_at"] = {"$gte": since}
    if sources:
        query["source"] = {"$in": list(sources)}
    if symbols:
        query["symbol"] = {"$in": [s.upper() for s in symbols]}
    cursor = _db["prd_resolved_outcomes"].find(query, {"_id": 0}).sort(
        "resolved_at", -1,
    ).limit(max(1, min(limit, 5000)))
    return await cursor.to_list(length=limit)


async def stats() -> dict:
    """Lightweight counters for the admin Health/Ops panel."""
    if _db is None:
        return {"ok": False}
    await _ensure_indexes()
    total = await _db["prd_resolved_outcomes"].count_documents({})
    return {"ok": True, "total_outcomes": total}
