"""Owner-only admin route for the in-process MC sidecar.

Endpoints:
    GET    /api/admin/mc-sidecar/status   → current state + task health
    POST   /api/admin/mc-sidecar/start    → spawn the 3 loops (idempotent)
    POST   /api/admin/mc-sidecar/stop     → cancel the 3 loops (idempotent)

Matches REDEYE's exact API shape so MC's brain-operator dashboard can
hit the same paths on Alpha without conditional branches.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services import mc_sidecar
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/mc-sidecar", tags=["admin", "mc-sidecar"])

# Module-level DB binder (matches the pattern used elsewhere in
# routes/* — wired by server.py at startup).
_db: Any = None


def set_db(db) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    # Canonical check — matches routes/auth.py. Older code paths in
    # this module used user.get("is_owner") which is never set on the
    # user document (the schema uses ``role``); that bug made this
    # endpoint silently unreachable. Fixed 2026-05-17.
    if not user or user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="owner-only endpoint")
    return user


def _require_db():
    if _db is None:
        raise HTTPException(status_code=503, detail="db not wired")
    return _db


@router.get("/status")
async def mc_sidecar_status(request: Request) -> dict[str, Any]:
    """Read-only introspection of the in-process sidecar."""
    await _require_owner(request)
    return await mc_sidecar.status(_require_db())


@router.post("/start")
async def mc_sidecar_start(request: Request) -> dict[str, Any]:
    """Spawn the three loops if they aren't already running. Idempotent."""
    await _require_owner(request)
    return await mc_sidecar.start(_require_db())


@router.post("/stop")
async def mc_sidecar_stop(request: Request) -> dict[str, Any]:
    """Cancel the loops. Idempotent."""
    await _require_owner(request)
    return await mc_sidecar.stop(_require_db())
