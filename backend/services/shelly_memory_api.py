"""Owner-only API for Shelly's durable memory write surface."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from .shelly_memory import (
    MALFORMED_COLLECTION,
    MEMORY_COLLECTION,
    count_by_regime,
    list_malformed as do_list_malformed,
    perceive as do_perceive,
    recall as do_recall,
    remember as do_remember,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/shelly-memory",
    tags=["admin", "shelly-memory"],
)


_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _get_db():
    if _db is None:
        raise HTTPException(
            status_code=503, detail="shelly-memory: db not wired",
        )
    return _db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


class RememberRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)
    metadata: Optional[dict] = None
    memory_id: Optional[str] = Field(default=None, max_length=128)


@router.post("/remember")
async def remember_endpoint(body: RememberRequest, request: Request):
    await _require_owner(request)
    db = _get_db()
    try:
        return await do_remember(
            db,
            text=body.text,
            metadata=body.metadata,
            memory_id=body.memory_id,
        )
    except ValueError as exc:
        # Toxic-spike fail-loud — bad event_date is HTTP 422.
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/recall")
async def recall_endpoint(
    request: Request,
    min_event_date: Optional[str] = None,
    max_event_date: Optional[str] = None,
    include_legacy: bool = True,
    limit: int = Query(default=50, ge=1, le=500),
):
    await _require_owner(request)
    db = _get_db()
    try:
        rows = await do_recall(
            db,
            min_event_date=min_event_date,
            max_event_date=max_event_date,
            include_legacy=include_legacy,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"memories": rows, "count": len(rows)}


@router.get("/status")
async def status_endpoint(request: Request):
    await _require_owner(request)
    db = _get_db()
    counts = await count_by_regime(db)
    return {
        "collection": MEMORY_COLLECTION,
        "malformed_collection": MALFORMED_COLLECTION,
        "embedding_version": "minilm-l6-v2-default",
        **counts,
    }


class PerceiveRequest(BaseModel):
    payload: object = Field(..., description="Raw inbound information")
    source: str = Field(..., min_length=1, max_length=128)
    text: Optional[str] = Field(default=None, max_length=20_000)
    metadata: Optional[dict] = None


@router.post("/perceive")
async def perceive_endpoint(body: PerceiveRequest, request: Request):
    """Doctrine v2 — Shelly perception. Never 5xx's: malformed
    payloads are quarantined with legacy/date/time/id labels and
    a sequential ``doc_number``."""
    await _require_owner(request)
    db = _get_db()
    return await do_perceive(
        db,
        payload=body.payload,
        source=body.source,
        text=body.text,
        metadata=body.metadata,
    )


@router.get("/malformed")
async def malformed_endpoint(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    min_doc_number: Optional[int] = Query(default=None, ge=1),
):
    """Operator audit of the malformed-quarantine bin, sorted in
    arrival order (``doc_number`` ascending)."""
    await _require_owner(request)
    db = _get_db()
    rows = await do_list_malformed(
        db, limit=limit, min_doc_number=min_doc_number,
    )
    return {"rows": rows, "count": len(rows)}
