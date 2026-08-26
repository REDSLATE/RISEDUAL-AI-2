"""Admin routes for the Operator Watchlist — paste research text →
parse → confirm → store → live coverage against Alpha's predictions.

All endpoints require admin/owner role. The parser is a dry-run
endpoint (no writes) so ops can preview what will be captured
before committing.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from services import operator_watchlist as ow
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/operator-watchlist", tags=["admin-operator-watchlist"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.post("/parse")
async def parse_endpoint(
    request: Request,
    payload: dict[str, Any] = Body(...),
):
    """Dry-run: return parsed candidates without writing."""
    await _require_admin(request)
    text = str(payload.get("text") or "")
    picks = ow.parse_text(text)
    return {"count": len(picks), "picks": picks}


@router.post("")
async def commit_endpoint(
    request: Request,
    payload: dict[str, Any] = Body(...),
):
    """Commit selected picks to the watchlist.

    Payload::

        {
            "picks": [{"symbol": "BE", "trigger": 217.5, ...}, ...],
            "conviction": "watch"|"med"|"high",
            "ttl": "session"|"day"|"week"
        }
    """
    user = await _require_admin(request)
    picks = payload.get("picks") or []
    if not isinstance(picks, list) or not picks:
        raise HTTPException(status_code=400, detail="picks[] required")
    n = await ow.add_picks(
        db, picks,
        actor=user.get("email", "admin"),
        ttl=str(payload.get("ttl") or "session"),
        conviction=str(payload.get("conviction") or "watch"),
    )
    return {"added": n}


@router.get("")
async def list_endpoint(request: Request):
    """List active (non-expired) watchlist entries WITH coverage —
    each entry is annotated with whether Alpha has a fresh prediction."""
    await _require_admin(request)
    rows = await ow.coverage_report(db)
    covered = sum(1 for r in rows if r.get("has_prediction"))
    return {
        "count": len(rows),
        "covered": covered,
        "gaps": len(rows) - covered,
        "items": rows,
    }


@router.delete("/{symbol}")
async def delete_endpoint(request: Request, symbol: str):
    await _require_admin(request)
    n = await ow.remove_symbol(db, symbol)
    if n == 0:
        raise HTTPException(status_code=404, detail=f"symbol {symbol} not in watchlist")
    return {"removed": n}
