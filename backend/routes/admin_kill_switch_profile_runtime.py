"""Admin routes — Kill-Switch Profile runtime control (2026-02-26).

Extends ``admin_kill_switch_profiles`` with WRITE surfaces so the
operator can activate / deactivate a named profile against the
equity or crypto core, plus inspect the live session-stats + halt
verdict.

Owner-only — same auth pattern.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/kill-switch/runtime",
    tags=["admin-kill-switch-runtime"],
)

db: Any = None


def set_db(database) -> None:
    global db
    db = database


_ALLOWED_ASSETS = ("equity", "crypto")


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _validate_asset(asset_type: str) -> str:
    a = (asset_type or "").strip().lower()
    if a not in _ALLOWED_ASSETS:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_asset_type", "allowed": list(_ALLOWED_ASSETS)},
        )
    return a


# ── POST activate ────────────────────────────────────────────────


@router.post("/{asset_type}/activate")
async def activate_profile(
    asset_type: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Activate a profile for an asset_type. Body schema::

        {
          "profile_key": "small_account_warrior",
          "starting_equity_usd": 1000.0,
          "note": "small-account discipline overlay for Q1"
        }
    """
    user = await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    asset = _validate_asset(asset_type)

    profile_key = (body.get("profile_key") or "").strip().lower()
    if not profile_key:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_profile_key"},
        )

    from services.kill_switch_profiles import get_profile
    if get_profile(profile_key) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_profile", "key": profile_key},
        )

    try:
        starting_equity_usd = float(body.get("starting_equity_usd", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_starting_equity_usd"},
        )
    if starting_equity_usd <= 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "non_positive_starting_equity",
                "received": starting_equity_usd,
            },
        )

    from services.kill_switch_profile_runtime import set_active_profile_config

    return await set_active_profile_config(
        db,
        asset_type=asset,
        profile_key=profile_key,
        starting_equity_usd=starting_equity_usd,
        set_by=user.get("email") or "owner",
        note=body.get("note"),
    )


# ── DELETE deactivate ────────────────────────────────────────────


@router.delete("/{asset_type}/active")
async def deactivate_profile(
    asset_type: str,
    request: Request,
) -> dict[str, Any]:
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    asset = _validate_asset(asset_type)

    from services.kill_switch_profile_runtime import clear_active_profile_config

    deleted = await clear_active_profile_config(db, asset)
    return {"asset_type": asset, "cleared": bool(deleted)}


# ── GET status (read-only snapshot) ──────────────────────────────


@router.get("/{asset_type}/status")
async def get_profile_status(
    asset_type: str,
    request: Request,
) -> dict[str, Any]:
    """Live readout: active profile config + session stats + halt verdict."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    asset = _validate_asset(asset_type)

    from services.kill_switch_profile_runtime import (
        check_session_halt,
        compute_session_stats,
        get_active_profile_config,
    )

    config = await get_active_profile_config(db, asset)
    if not config:
        return {
            "asset_type": asset,
            "active": False,
            "config": None,
            "session": None,
            "halt": False,
            "triggers": [],
        }

    stats = await compute_session_stats(
        db,
        asset_type=asset,
        starting_equity_usd=float(config.get("starting_equity_usd") or 0.0),
    )
    ev = await check_session_halt(db, asset)
    return {
        "asset_type": asset,
        "active": True,
        "config": config,
        "session": stats,
        "halt": bool(ev and ev.halt),
        "triggers": (
            [
                {"rule": t.rule, "threshold": t.threshold,
                 "observed": t.observed, "message": t.message}
                for t in (ev.triggers if ev else ())
            ]
        ),
    }


__all__ = ["router", "set_db"]
