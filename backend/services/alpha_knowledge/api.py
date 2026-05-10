"""FastAPI router for Alpha Knowledge Base. Owner-only."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user
from .ingest import ingest as run_ingest
from .retrieval import retrieve as run_retrieve, status as run_status
from .schemas import IngestRequest, IngestSummary, RetrievalResponse
from .seed_manifest import all_sources, filter_by_category

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/alpha-knowledge",
    tags=["admin", "alpha-knowledge"],
)


_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _get_db():
    if _db is None:
        raise HTTPException(
            status_code=503, detail="alpha-knowledge: db not wired",
        )
    return _db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/status")
async def status(request: Request):
    await _require_owner(request)
    return await run_status(_get_db())


@router.get("/manifest")
async def manifest(request: Request):
    await _require_owner(request)
    sources = all_sources()
    by_cat: dict[str, int] = {}
    for _u, _t, c in sources:
        by_cat[c] = by_cat.get(c, 0) + 1
    return {
        "total_urls": len(sources),
        "by_category": by_cat,
        "categories": sorted(by_cat.keys()),
        "sources": [
            {"url": u, "title": t, "category": c}
            for (u, t, c) in sources
        ],
    }


@router.post("/ingest", response_model=IngestSummary)
async def ingest_endpoint(body: IngestRequest, request: Request):
    await _require_owner(request)
    db = _get_db()
    cats = body.categories or None
    return await run_ingest(db, categories=cats, limit_urls=body.limit_urls)


@router.get("/retrieve", response_model=RetrievalResponse)
async def retrieve_endpoint(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=6, ge=1, le=20),
    category: Optional[str] = None,
):
    await _require_owner(request)
    db = _get_db()
    return await run_retrieve(db, q, limit=limit, category=category)
