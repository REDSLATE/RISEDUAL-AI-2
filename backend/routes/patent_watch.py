"""Admin routes for Patent Watch.

All endpoints sit under ``/api/admin/patents/`` and require
``admin``/``owner`` role. Read-mostly — POST endpoints are limited
to query CRUD and manual refresh.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.auth_helpers import get_current_user
from services.patent_watch_service import (
    create_query,
    delete_query,
    get_config_status,
    list_queries,
    list_results,
    refresh_query,
    set_db,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/patents", tags=["patent-watch"])


def _is_admin(user: dict) -> bool:
    role = (user.get("role") or "").lower()
    return role in ("admin", "owner")


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Pydantic ──────────────────────────────────────────────────────


class CreateQueryRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    assignee: Optional[str] = Field(None, max_length=200)
    inventor_last: Optional[str] = Field(None, max_length=120)
    keyword: Optional[str] = Field(None, max_length=200)


# ── Routes ────────────────────────────────────────────────────────


@router.get("/queries")
async def get_queries(request: Request) -> dict:
    await _require_admin(request)
    return {"queries": await list_queries()}


@router.get("/config")
async def get_config(request: Request) -> dict:
    """Lightweight config check for the UI setup banner. Never
    returns the API key itself — only whether one is configured."""
    await _require_admin(request)
    return get_config_status()


@router.post("/queries")
async def add_query(request: Request, body: CreateQueryRequest) -> dict:
    await _require_admin(request)
    if not (body.assignee or body.inventor_last or body.keyword):
        raise HTTPException(
            status_code=400,
            detail="At least one of assignee, inventor_last, or keyword required",
        )
    record = await create_query(
        label=body.label,
        assignee=body.assignee,
        inventor_last=body.inventor_last,
        keyword=body.keyword,
    )
    return {"query": record}


@router.delete("/queries/{query_id}")
async def remove_query(request: Request, query_id: str) -> dict:
    await _require_admin(request)
    ok = await delete_query(query_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Query not found")
    return {"deleted": True}


@router.get("/results")
async def get_results(
    request: Request,
    query_id: Optional[str] = None,
    limit: int = 50,
) -> dict:
    await _require_admin(request)
    return {"results": await list_results(query_id=query_id, limit=limit)}


@router.post("/refresh/{query_id}")
async def manual_refresh(request: Request, query_id: str) -> dict:
    """On-demand fetch for a single query. Returns aggregate stats
    so the UI can show 'Fetched 12 patents' inline.

    Daily refresh runs unattended via the APScheduler hook in
    ``server.py::_run_patent_watch_refresh``; this endpoint exists
    for ad-hoc use after adding/editing a query.
    """
    await _require_admin(request)
    return await refresh_query(query_id)


# Re-export for route_registry
__all__ = ["router", "set_db"]
