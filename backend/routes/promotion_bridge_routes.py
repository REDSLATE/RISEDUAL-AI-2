"""Promotion Bridge admin routes — `/api/admin/bridges/*`.

Read endpoints surface the registered bridges + their active state.
Write endpoints (activate/revoke) are admin-only and require the
``BRIDGE_APPROVAL_TOKEN`` env to be set + matched in the request body.
Audit log is read-only via ``/audit``.

This is the **only public surface** for the Promotion Bridge — there
is no PRD-side endpoint for activation, by spec.
"""
from __future__ import annotations

__domain__ = "BRIDGE"

from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user
from services import promotion_bridge as bridge_mod

router = APIRouter(prefix="/api/admin/bridges", tags=["promotion-bridge"])


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() in ("admin", "owner")


@router.get("")
async def list_bridges(request: Request) -> dict:
    """List every registered bridge with its current active state."""
    await get_current_user(request)
    return {
        "bridges": bridge_mod.list_specs(),
        "active": bridge_mod.active_state(),
    }


@router.post("/{name}/activate")
async def activate_bridge(request: Request, name: str) -> dict:
    """Activate a registered bridge with a calibration value.

    Body: ``{value, approval_token, evidence: {sample_count, oos_window_days, regression_pct}}``.
    Requires admin role + valid BRIDGE_APPROVAL_TOKEN match.
    """
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    value = body.get("value")
    token = body.get("approval_token") or ""
    evidence = body.get("evidence") or {}
    if value is None:
        raise HTTPException(status_code=400, detail="value_required")
    res = await bridge_mod.activate(
        name,
        value=float(value),
        approval_token=token,
        actor=user.get("email") or user.get("id") or "admin",
        evidence=evidence,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "activate_failed")
    return res


@router.post("/{name}/revoke")
async def revoke_bridge(request: Request, name: str) -> dict:
    """Revoke an active bridge. Idempotent."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = body.get("reason", "")
    return await bridge_mod.revoke(
        name,
        actor=user.get("email") or user.get("id") or "admin",
        reason=reason,
    )


@router.get("/audit")
async def audit_log(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"events": await bridge_mod.audit_log(limit=limit)}
