"""Owner-only MC2 diagnostic surface.

Two endpoints:
  * ``GET /api/admin/mc2/state`` — collection volumes + last 10 rows
    per stream. The 'is MC2 alive?' probe.
  * ``GET /api/admin/mc2/scorecard?brain=alpha`` — local scorecard
    rollup. Permanent fix for the ``total_resolved=0`` pain.

Auth: ``_require_owner`` — same gate as ``admin_runtime_stamp.py``
(operator JWT, role=owner).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from services.mc2 import get_scorecard, get_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/mc2")


async def _require_owner(request: Request):
    """Mirror of ``admin_runtime_stamp._require_owner`` — operator
    JWT, role=owner. Duplicated locally so a route-discovery refactor
    can drop this file without losing auth."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/state")
async def mc2_state(request: Request) -> dict[str, Any]:
    """Volumes + tail of every MC2 collection. The operator's first
    stop when triaging 'did MC2 receive my emission?'."""
    await _require_owner(request)
    return await get_state()


@router.get("/scorecard")
async def mc2_scorecard(
    request: Request,
    brain: str = Query("alpha", min_length=1, max_length=64),
) -> dict[str, Any]:
    """All-time win/loss/flat rollup for ``brain`` from
    ``mc2_outcomes``. Returns the zeroed shape if no outcomes are
    on file yet."""
    await _require_owner(request)
    return await get_scorecard(brain=brain)


__all__ = ["router"]
