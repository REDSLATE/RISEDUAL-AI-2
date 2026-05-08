"""Alpha decision log — 8-stage NO_TRADE receipts.

Every signal that flows through the ML pipeline produces exactly one
receipt. Receipts that ended in NO_TRADE carry a ``blocked_at`` field
naming the gate (one of :data:`services.ml.contracts.DECISION_STAGES`).
Approved signals carry ``decision=APPROVED`` and ``blocked_at=None``.

Storage: Mongo collection ``alpha_decision_log`` with a 30-day TTL
index. Schema is intentionally sparse — receipts are observational,
not authoritative.

Public API:
  * :func:`record_decision` — async write of a single receipt
  * :func:`record_pipeline_decision` — convenience wrapper that takes
    a :class:`PipelineDecision` and resolves the right stage / reason
  * :func:`ensure_indexes` — create the TTL index (idempotent)
  * :func:`summary` — operator-facing counts by stage + reason
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.ml.contracts import DECISION_STAGES, Verdict

logger = logging.getLogger(__name__)

COLLECTION = "alpha_decision_log"
TTL_DAYS = 30


# ── Index ────────────────────────────────────────────────────────


async def ensure_indexes(db) -> None:
    """Idempotent. Safe to call on every boot."""
    if db is None:
        return
    try:
        coll = db[COLLECTION]
        await coll.create_index(
            "created_at",
            expireAfterSeconds=TTL_DAYS * 24 * 3600,
            name="alpha_decision_log_ttl",
        )
        await coll.create_index([("symbol", 1), ("created_at", -1)],
                                name="alpha_decision_symbol_ts")
        await coll.create_index([("blocked_at", 1), ("created_at", -1)],
                                name="alpha_decision_stage_ts")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_decision_log] ensure_indexes failed: %s", exc)


# ── Validation ───────────────────────────────────────────────────


def _validate_blocked_at(value: Optional[str]) -> Optional[str]:
    """``blocked_at`` is None for approved trades, otherwise must be
    one of the canonical 8 gates."""
    if value is None:
        return None
    if value not in DECISION_STAGES:
        raise ValueError(
            f"blocked_at={value!r} not in canonical stages: {DECISION_STAGES}"
        )
    return value


# ── Write API ────────────────────────────────────────────────────


async def record_decision(
    db,
    *,
    symbol: str,
    lane: str,
    decision: str,                          # "APPROVED" | "NO_TRADE"
    blocked_at: Optional[str] = None,       # None when decision == APPROVED
    reason: Optional[str] = None,
    confidence: float = 0.0,
    trail: Optional[List[Dict[str, Any]]] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Persist one receipt. Returns the inserted _id as str on success,
    None on any failure. NEVER raises.

    Validation:
      * ``decision in {APPROVED, NO_TRADE}``
      * ``blocked_at in DECISION_STAGES`` when present
      * ``decision=NO_TRADE`` REQUIRES ``blocked_at`` (a NO_TRADE with
        no stage is a bug — receipts must be auditable).
    """
    if db is None:
        return None
    try:
        if decision not in ("APPROVED", "NO_TRADE"):
            raise ValueError(f"decision={decision!r} not in {{APPROVED, NO_TRADE}}")
        blocked_at = _validate_blocked_at(blocked_at)
        if decision == "NO_TRADE" and blocked_at is None:
            raise ValueError("NO_TRADE receipt requires blocked_at")
        if decision == "APPROVED" and blocked_at is not None:
            raise ValueError("APPROVED receipt must have blocked_at=None")

        doc = {
            "created_at": datetime.now(timezone.utc),
            "symbol": symbol,
            "lane": lane,
            "decision": decision,
            "blocked_at": blocked_at,
            "reason": reason,
            "confidence": float(confidence),
            "trail": trail or [],
            "extras": extras or {},
            "schema_version": 1,
        }
        result = await db[COLLECTION].insert_one(doc)
        return str(result.inserted_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_decision_log] record_decision failed: %s", exc)
        return None


async def record_pipeline_decision(db, pipeline_decision) -> Optional[str]:
    """Convenience wrapper. Accepts a :class:`PipelineDecision`."""
    if pipeline_decision is None:
        return None
    final = pipeline_decision.final
    is_approved = (
        pipeline_decision.blocked_at is None
        and final.decision in (Verdict.BUY.value, Verdict.SELL.value)
    )
    decision = "APPROVED" if is_approved else "NO_TRADE"
    return await record_decision(
        db,
        symbol=pipeline_decision.symbol,
        lane=pipeline_decision.lane,
        decision=decision,
        blocked_at=pipeline_decision.blocked_at,
        reason=final.reason,
        confidence=final.confidence,
        trail=[v.as_dict() for v in pipeline_decision.trail],
        extras={
            "final_decision": final.decision,
            "final_layer": final.layer,
        },
    )


# ── Read API ─────────────────────────────────────────────────────


async def summary(db, *, days: int = 7) -> Dict[str, Any]:
    """Count receipts by ``blocked_at`` for the last ``days``.

    The ``approved`` bucket is the count of ``decision=APPROVED``
    (which carries ``blocked_at=None``)."""
    if db is None:
        return {"approved": 0, "by_stage": {}, "total": 0}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db[COLLECTION].aggregate([
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"blocked_at": "$blocked_at", "decision": "$decision"},
                "count": {"$sum": 1},
            }},
        ])
        approved = 0
        by_stage: Dict[str, int] = {s: 0 for s in DECISION_STAGES}
        total = 0
        async for row in cursor:
            count = int(row.get("count") or 0)
            total += count
            decision = row["_id"].get("decision")
            blocked_at = row["_id"].get("blocked_at")
            if decision == "APPROVED":
                approved += count
            elif blocked_at in by_stage:
                by_stage[blocked_at] += count
        return {"approved": approved, "by_stage": by_stage, "total": total}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_decision_log] summary failed: %s", exc)
        return {"approved": 0, "by_stage": {}, "total": 0, "error": str(exc)}
