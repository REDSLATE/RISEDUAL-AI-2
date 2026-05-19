"""
Stage 3 Decision Pairs Admin API.

Owner-only read endpoints surfacing the Sovereign-vs-Council
side-by-side ledger that ``intent_decision_filer`` builds.

Endpoints
---------
* ``GET  /api/admin/decision-pairs``       — paginated row list.
* ``GET  /api/admin/decision-pairs/stats`` — aggregate accuracy + winners.

Doctrine
--------
* Read-only. This endpoint cannot file pairs or backfill outcomes.
* Mongo ``_id`` excluded on every projection.
* Owner only — non-owner gets 403.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user
from services.decision_outcome_writer import aggregate_stats
from services.intent_decision_filer import fetch_recent_pairs

router = APIRouter(
    prefix="/api/admin/decision-pairs",
    tags=["admin", "decision-pairs", "sovereign", "council"],
)


_db = None  # set via set_db at registry-wire time


def set_db(database: Any) -> None:
    global _db
    _db = database


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("")
async def list_pairs(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    lane: Optional[str] = Query(None, pattern="^(equity|crypto)$"),
    agreement: Optional[str] = Query(None, pattern="^(AGREE|PARTIAL|DISAGREE)$"),
    resolved: Optional[bool] = Query(None),
) -> dict[str, Any]:
    """Recent decision pairs. Newest first."""
    await _require_owner(request)
    if _db is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    rows = await fetch_recent_pairs(
        _db, limit=limit, lane=lane, agreement=agreement, resolved=resolved,
    )
    return {"rows": rows, "count": len(rows)}


@router.get("/stats")
async def pairs_stats(
    request: Request,
    lane: Optional[str] = Query(None, pattern="^(equity|crypto)$"),
    since_days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Aggregate Sovereign vs Council scoreboard."""
    await _require_owner(request)
    if _db is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    return await aggregate_stats(_db, lane=lane, since_days=since_days)


__all__ = ["router", "set_db"]
