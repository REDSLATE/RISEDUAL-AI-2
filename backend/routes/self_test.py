"""RISEDUAL AI — Self-Test admin route.

Exposes `/api/admin/self-test` so the CLI script and the Admin UI panel
can both trigger the same check battery on demand.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from services.self_test_service import run_self_test

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["self-test"])

db = None
_scheduler = None


def set_db(database) -> None:
    global db
    db = database


def set_scheduler(scheduler) -> None:
    """Called from server.py once the APScheduler instance is started."""
    global _scheduler
    _scheduler = scheduler


async def _require_admin(request: Request):
    from routes.auth import get_current_user

    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/self-test")
async def get_self_test(request: Request):
    """Run the self-test battery and return a JSON report."""
    await _require_admin(request)
    return await run_self_test(db, _scheduler)


@router.post("/self-test")
async def trigger_self_test(request: Request):
    """Alias of GET — kept so the CLI script and UI button can POST."""
    await _require_admin(request)
    return await run_self_test(db, _scheduler)
