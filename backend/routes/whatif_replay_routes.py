"""What-If replay routes — `/api/admin/replay/*`."""
from __future__ import annotations

__domain__ = "PRD"

from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user
from services.whatif_replay_service import backfill_outcomes, whatif_projection

router = APIRouter(prefix="/api/admin/replay", tags=["whatif-replay"])

_db = None


def set_db(database) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() in ("admin", "owner")


@router.post("/backfill")
async def backfill(request: Request, limit: int = Query(5000, ge=1, le=20000)) -> dict:
    """Publish historical paper_trades + verified predictions into
    the firewall outcome ledger. Idempotent."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await backfill_outcomes(_db, limit=limit)


@router.get("/whatif")
async def whatif(
    request: Request,
    since: str | None = Query(None, description="ISO timestamp lower bound"),
    until: str | None = Query(None, description="ISO timestamp upper bound"),
    engines: str | None = Query(None, description="Comma-separated engine names; default = all"),
    sources: str | None = Query(None, description="Comma-separated outcome sources"),
    dimension: str = Query("agent"),
    min_total: int = Query(30, ge=1),
    limit: int = Query(5000, ge=1, le=20000),
) -> dict:
    """Project every (or requested) engine's schema against resolved
    outcomes in the window. Read-only — does not mutate engine state."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await whatif_projection(
        since=since,
        until=until,
        engines=[e.strip() for e in engines.split(",")] if engines else None,
        sources=[s.strip() for s in sources.split(",")] if sources else None,
        dimension=dimension,
        min_total=min_total,
        limit=limit,
    )
