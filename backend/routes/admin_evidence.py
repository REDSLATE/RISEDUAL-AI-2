"""Admin Evidence Panel — per-strategy attribution + manual recompute.

Owner/admin only. Exposes the ``Evidence Worker`` output to the
Admin UI so operators can see which strategies earn full sizing
and which are size-reduced or blocked.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException

from services.auth_helpers import get_current_user
from services import evidence_worker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/evidence", tags=["admin-evidence"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/scores")
async def evidence_scores(request: Request):
    """Return current per-strategy evidence scores + last-run metadata."""
    await _require_admin(request)
    return await evidence_worker.get_evidence_snapshot(db)


@router.post("/recompute")
async def evidence_recompute(request: Request):
    """Trigger the Evidence Worker on demand.

    Idempotent — upserts current scores. Useful when the operator
    changes ``RISEDUAL_EVIDENCE_WINDOW_DAYS`` or ``MIN_TRADES`` and
    wants the change reflected immediately instead of waiting for
    the nightly sweep.
    """
    user = await _require_admin(request)
    result = await evidence_worker.compute_evidence(db)
    logger.info(
        "[evidence] manual recompute by %s: strategies=%d wrote=%d",
        user.get("email"), result.get("strategies_evaluated", 0),
        result.get("wrote", 0),
    )
    return result
