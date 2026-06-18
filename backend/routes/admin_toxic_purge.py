"""Owner-only toxic-spike purge endpoint (2026-06-16).

Operator directive: "wipe what you can to get rid of them".

Two purge surfaces:
  1. ``POST /api/admin/toxic-purge/chroma``  — wipes Chroma episodes
     tagged ``toxic_lesson`` or ``miss`` with confidence > floor.
  2. ``POST /api/admin/toxic-purge/mongo``   — wipes the matching
     rows in MongoDB's ``predictions`` collection so the autopsy
     view + email-alert pipeline stop re-surfacing them.

Both endpoints take ``?confidence_floor=80.0`` (default 80.0).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/toxic-purge")


async def _require_owner(request: Request):
    """Mirror of admin_runtime_stamp._require_owner — operator JWT,
    role=owner."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


@router.post("/chroma")
async def purge_chroma(
    request: Request,
    confidence_floor: float = Query(80.0, ge=0.0, le=100.0),
) -> dict[str, Any]:
    """Delete toxic-tagged + high-conf-miss rows from ChromaDB."""
    await _require_owner(request)
    try:
        from services.market_memory_service import purge_toxic_lessons
        return await purge_toxic_lessons(confidence_floor=confidence_floor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[toxic-purge/chroma] failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/mongo")
async def purge_mongo(
    request: Request,
    confidence_floor: float = Query(80.0, ge=0.0, le=100.0),
) -> dict[str, Any]:
    """Delete predictions rows that match the toxic shape so the
    autopsy view + email alert pipeline stop counting them."""
    await _require_owner(request)
    if _db is None:
        raise HTTPException(status_code=500, detail="db not bound")

    try:
        # The Toxic Spike alert email queries:
        #   verified_24h.correct = False AND
        #   normalize_confidence(confidence) > floor
        # Mongo stores confidence on mixed 0-1 and 0-100 scales, so
        # we match BOTH ranges. Anything in (0, 1] with floor scaled
        # to a fraction, AND anything in [floor, 100].
        floor_frac = confidence_floor / 100.0
        result = await _db.predictions.delete_many({
            "$and": [
                {"verified_24h.correct": False},
                {"$or": [
                    {"confidence": {"$gt": confidence_floor}},
                    {"confidence": {"$gt": floor_frac, "$lte": 1.0}},
                ]},
            ],
        })
        logger.info(
            "[toxic-purge/mongo] deleted %d predictions rows "
            "(confidence > %.1f) — operator directive 2026-06-16",
            result.deleted_count, confidence_floor,
        )
        return {"ok": True, "deleted": result.deleted_count}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[toxic-purge/mongo] failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/all")
async def purge_all(
    request: Request,
    confidence_floor: float = Query(80.0, ge=0.0, le=100.0),
) -> dict[str, Any]:
    """One-call convenience — purge BOTH Chroma + Mongo. Returns the
    combined report so the operator can verify both sides cleared."""
    await _require_owner(request)
    chroma_out = await purge_chroma(request, confidence_floor)
    mongo_out = await purge_mongo(request, confidence_floor)
    return {
        "ok": chroma_out.get("ok") and mongo_out.get("ok"),
        "chroma": chroma_out,
        "mongo": mongo_out,
    }


__all__ = ["router", "set_db"]
