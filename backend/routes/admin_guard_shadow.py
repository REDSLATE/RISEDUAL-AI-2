"""Admin route for the Decision Pipeline Guard shadow-mode log.

Surfaces:

  * GET /api/admin/guard-shadow/summary   — counts + would-block rate
  * GET /api/admin/guard-shadow/decisions — paginated raw rows

Both owner-only. Used during the guard rollout (Step 5 of the
implementation plan): operators compare "would have blocked" against
"actually executed" before flipping ``GUARD_SHADOW_MODE`` off.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user
from services.guard_shadow_log import COLLECTION, is_shadow_mode_enabled

router = APIRouter(prefix="/api/admin/guard-shadow", tags=["admin", "guard-shadow"])

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/summary")
async def shadow_summary(request: Request, hours: int = Query(168, ge=1, le=720)):
    """Aggregate over the last ``hours``: total / would-allow / would-block,
    plus per-source breakdown and the top blocking reasons."""
    await _require_owner(request)
    if _db is None:
        return {
            "shadow_mode_enabled": is_shadow_mode_enabled(),
            "window_hours": hours,
            "total": 0,
            "would_allow": 0,
            "would_block": 0,
            "by_source": {},
            "top_block_reasons": [],
        }

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    coll = _db[COLLECTION]
    match = {"created_at": {"$gte": since}}

    total = await coll.count_documents(match)
    allow = await coll.count_documents({**match, "would_allow": True})
    block = await coll.count_documents({**match, "would_allow": False})

    by_source: dict[str, dict[str, int]] = {}
    pipeline_src = [
        {"$match": match},
        {"$group": {
            "_id": {"source": "$source", "would_allow": "$would_allow"},
            "n": {"$sum": 1},
        }},
    ]
    async for row in coll.aggregate(pipeline_src):
        src = row["_id"]["source"] or "unknown"
        bucket = by_source.setdefault(src, {"would_allow": 0, "would_block": 0})
        if row["_id"]["would_allow"]:
            bucket["would_allow"] = row["n"]
        else:
            bucket["would_block"] = row["n"]

    # Top reasons across blocked rows only (those drive enforcement
    # decisions; allow-rows mostly say "ok").
    reason_counts: dict[str, int] = {}
    async for row in coll.find(
        {**match, "would_allow": False}, {"_id": 0, "reasons": 1},
    ).limit(2000):
        for r in (row.get("reasons") or [])[:3]:
            if not r:
                continue
            reason_counts[r] = reason_counts.get(r, 0) + 1
    top_reasons = sorted(
        ({"reason": k, "count": v} for k, v in reason_counts.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "shadow_mode_enabled": is_shadow_mode_enabled(),
        "window_hours": hours,
        "total": total,
        "would_allow": allow,
        "would_block": block,
        "would_block_rate": round(block / total, 4) if total else 0.0,
        "by_source": by_source,
        "top_block_reasons": top_reasons,
    }


@router.get("/decisions")
async def list_decisions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    source: Optional[str] = None,
    only_blocked: bool = False,
    entity_substr: Optional[str] = None,
):
    """Paginated raw shadow-log feed, newest-first. Filters AND-combine."""
    await _require_owner(request)
    if _db is None:
        return {"decisions": [], "total": 0}

    match: dict = {}
    if source:
        match["source"] = source
    if only_blocked:
        match["would_allow"] = False
    if entity_substr:
        match["entity_id"] = {"$regex": entity_substr, "$options": "i"}

    rows: list[dict] = []
    cursor = (
        _db[COLLECTION]
        .find(match, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    async for r in cursor:
        ca = r.get("created_at")
        if isinstance(ca, datetime):
            r["created_at"] = ca.isoformat()
        rows.append(r)

    return {
        "decisions": rows,
        "limit": limit,
        "filters": {
            "source": source,
            "only_blocked": only_blocked,
            "entity_substr": entity_substr,
        },
    }
