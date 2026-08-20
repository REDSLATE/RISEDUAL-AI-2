"""Admin Atlas — read-only diagnostics for the RISEDUAL System Atlas ledger.

Endpoints
---------
* ``GET /api/admin/atlas/status`` — window rollup (intent counts by
  status, terminal-result mix, latency percentiles, storage bytes).
* ``GET /api/admin/atlas/intents`` — latest N intent identities.
* ``GET /api/admin/atlas/traces/{trace_id}`` — one cycle trace.

All endpoints are read-only, off the hot path, and require admin
role. If the Atlas ledger is disabled or failed to initialize, the
endpoints return an ``enabled=false`` diagnostic instead of 500.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/atlas", tags=["admin-atlas"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _get_ledger() -> Any:
    from services.atlas_bridge import atlas_enabled, get_ledger

    if not atlas_enabled():
        return None
    return get_ledger()


@router.get("/status")
async def atlas_status(
    request: Request,
    window_hours: int = Query(24, ge=1, le=168),
):
    await _require_admin(request)
    ledger = _get_ledger()
    if ledger is None:
        return {"enabled": False, "reason": "atlas_disabled_or_not_initialized"}
    try:
        diag = ledger.diagnostics(window_hours=window_hours)
        return {"enabled": True, **diag}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[admin_atlas] diagnostics failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"atlas diagnostics failed: {exc}")


@router.get("/intents")
async def atlas_intents(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
):
    await _require_admin(request)
    ledger = _get_ledger()
    if ledger is None:
        return {"enabled": False, "items": []}
    try:
        items = ledger.latest_intents(limit=limit)
        return {"enabled": True, "count": len(items), "items": items}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[admin_atlas] latest_intents failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"atlas intents failed: {exc}")


@router.get("/traces/{trace_id}")
async def atlas_trace(request: Request, trace_id: str):
    await _require_admin(request)
    ledger = _get_ledger()
    if ledger is None:
        return {"enabled": False, "trace": None}
    try:
        trace = ledger.get_trace(trace_id)
        if not trace:
            raise HTTPException(status_code=404, detail=f"trace {trace_id} not found")
        return {"enabled": True, "trace": trace}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[admin_atlas] get_trace failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"atlas trace failed: {exc}")
