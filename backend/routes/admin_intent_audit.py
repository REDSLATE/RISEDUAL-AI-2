"""Admin Intent Audit — visibility into why the executor skips trades.

Every ``return None`` in ``public_equity_live_executor.maybe_route_live``
now writes a structured skip event to ``intent_skip_log``. Before this
existed, ~100% of scanner targets ended up ``executor_skipped`` with an
empty ``executor_skipped_reason``, and the operator was blind to which
gate was blocking live trades. These endpoints surface that data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/intent-audit", tags=["admin-intent-audit"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/summary")
async def skip_summary(request: Request, hours: int = Query(24, ge=1, le=168)):
    """Aggregate skip reasons over the last ``hours`` window.

    Returns a breakdown by reason + top symbols per reason so the
    operator can see at a glance which gate blocks most trades and
    which symbols are getting rejected repeatedly.
    """
    await _require_admin(request)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {
            "_id": "$reason",
            "count": {"$sum": 1},
            "symbols": {"$addToSet": "$symbol"},
            "last_ts": {"$max": "$ts"},
        }},
        {"$sort": {"count": -1}},
    ]
    reasons: list[dict] = []
    total = 0
    async for row in db.intent_skip_log.aggregate(pipeline):
        total += int(row["count"])
        reasons.append({
            "reason": row["_id"],
            "count": int(row["count"]),
            "symbols": sorted([s for s in (row.get("symbols") or []) if s])[:20],
            "last_ts": (row["last_ts"].isoformat()
                        if isinstance(row.get("last_ts"), datetime) else None),
        })

    # Compare to routed trades in the same window so the operator sees
    # skip-to-fire ratio.
    fires = await db.equity_live_trades.count_documents(
        {"opened_at": {"$gte": since}, "broker_id": "public"},
    )
    return {
        "window_hours": hours,
        "since": since.isoformat(),
        "total_skips": total,
        "total_fires": int(fires),
        "reasons": reasons,
    }


@router.get("/events")
async def skip_events(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    reason: str | None = Query(None),
    symbol: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Paginated raw skip events for post-mortem drill-down."""
    await _require_admin(request)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    q: dict = {"ts": {"$gte": since}}
    if reason:
        q["reason"] = reason
    if symbol:
        q["symbol"] = symbol.upper()
    events: list[dict] = []
    async for row in db.intent_skip_log.find(q, {"_id": 0}).sort("ts", -1).limit(limit):
        if isinstance(row.get("ts"), datetime):
            row["ts"] = row["ts"].isoformat()
        events.append(row)
    return {
        "window_hours": hours,
        "filter": {"reason": reason, "symbol": symbol},
        "count": len(events),
        "events": events,
    }
