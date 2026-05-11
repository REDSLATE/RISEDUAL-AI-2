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


class PromoteRequest(BaseModel):
    """Operator-submitted correction for a malformed doc.

    Either ``corrected_payload`` (preferred — operator has fixed the
    issue, e.g. supplied a valid ``event_date``) or ``use_raw=True``
    (re-attempt perception on the original raw_payload as-is, useful
    after upstream fixes to ``_normalize_event_date``).
    """
    corrected_payload: Optional[object] = None
    use_raw: bool = False
    source: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "Override the original source label. Defaults to the "
            "malformed row's source if omitted."
        ),
    )
    metadata: Optional[dict] = None


@router.post("/malformed/{doc_number}/promote")
async def promote_malformed_endpoint(
    doc_number: int,
    body: PromoteRequest,
    request: Request,
):
    """Re-perceive a quarantined doc with operator-supplied corrections.

    Doctrine: the malformed row stays in ``shelly_legacy_malformed``
    (numbered audit trail is permanent) — promotion does NOT delete.
    Instead, on a successful memory-lane outcome, we stamp
    ``promoted_to_memory_id`` + ``promoted_at`` on the malformed row
    so the audit trail shows what got rescued and when.

    A re-perception that still lands in the malformed lane (e.g.
    operator submitted yet another bad date) creates a NEW malformed
    doc with its own ``doc_number`` — the original row is left
    untouched. The audit trail is append-only.
    """
    await _require_owner(request)
    db = _get_db()

    # Fetch the malformed row.
    row = await db[MALFORMED_COLLECTION].find_one(
        {"doc_number": int(doc_number)}, {"_id": 0},
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"malformed doc #{doc_number} not found",
        )

    # Resolve payload + source.
    if body.use_raw or body.corrected_payload is None:
        payload = row.get("raw_payload")
    else:
        payload = body.corrected_payload
    source = body.source or row.get("source") or "unknown"

    result = await do_perceive(
        db,
        payload=payload,
        source=source,
        metadata=body.metadata,
    )

    # Stamp the malformed row on successful memory-lane outcome so
    # the operator UI can show "✓ promoted" next to the row.
    if result.get("lane") == "memory":
        memory_id = (result.get("doc") or {}).get("id")
        try:
            from datetime import datetime, timezone
            await db[MALFORMED_COLLECTION].update_one(
                {"doc_number": int(doc_number)},
                {"$set": {
                    "promoted_to_memory_id": memory_id,
                    "promoted_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "shelly: failed to stamp promotion on malformed #%d: %s",
                doc_number, exc,
            )

    return {"promoted_from_doc_number": int(doc_number), **result}
