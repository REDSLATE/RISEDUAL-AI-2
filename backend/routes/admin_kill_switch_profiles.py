"""Admin route — Named Kill-Switch Profiles (2026-02-26, P2).

Read-only surface on top of ``services.kill_switch_profiles``. The
operator can list profiles, dry-run an evaluator against a snapshot
of any account, or query the canonical Warrior Small-Account profile.

Owner-only — same pattern as ``admin_council_policy.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/kill-switch",
    tags=["admin-kill-switch-profiles"],
)


async def _require_owner(request: Request):
    """Owner-only — mirrors the council-policy pattern."""
    from routes.auth import get_current_user

    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── GET /api/admin/kill-switch/profiles ─────────────────────────────


@router.get("/profiles")
async def list_kill_switch_profiles(request: Request) -> dict[str, Any]:
    """Full profile registry — names, rules, source citations."""
    await _require_owner(request)
    from services.kill_switch_profiles import list_profiles

    profiles = list_profiles()
    return {"count": len(profiles), "profiles": profiles}


# ── GET /api/admin/kill-switch/profiles/{key} ───────────────────────


@router.get("/profiles/{profile_key}")
async def get_one_profile(profile_key: str, request: Request) -> dict[str, Any]:
    """Single-profile lookup; 404 if unknown."""
    await _require_owner(request)
    from services.kill_switch_profiles import get_profile

    p = get_profile(profile_key)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_profile", "key": profile_key},
        )
    return {
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "source": p.source,
        "rules": {
            "daily_max_loss_pct": p.daily_max_loss_pct,
            "daily_max_loss_usd": p.daily_max_loss_usd,
            "consecutive_loss_limit": p.consecutive_loss_limit,
            "daily_profit_cap_pct": p.daily_profit_cap_pct,
        },
        "tags": list(p.tags),
    }


# ── POST /api/admin/kill-switch/profiles/{key}/evaluate ─────────────


@router.post("/profiles/{profile_key}/evaluate")
async def evaluate_kill_switch_profile(
    profile_key: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Dry-run the profile against a snapshot of account state.

    Body schema::

        {
          "starting_equity_usd":       1000.00,
          "realized_pnl_usd_today":    -120.00,
          "consecutive_losses_today":  3
        }

    Returns the full evaluation — every rule that tripped, what it
    saw, and a final ``halt`` verdict. Stateless on the server side;
    activating the halt is a separate workflow handled by the
    per-account state tracker (not yet wired — admin write surface
    will land in a follow-up).
    """
    await _require_owner(request)
    from services.kill_switch_profiles import evaluate_profile, get_profile

    if get_profile(profile_key) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_profile", "key": profile_key},
        )

    try:
        starting_equity_usd = float(body.get("starting_equity_usd", 0) or 0)
        realized_pnl_usd_today = float(body.get("realized_pnl_usd_today", 0) or 0)
        consecutive_losses_today = int(body.get("consecutive_losses_today", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_account_stats"},
        )

    if starting_equity_usd < 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "negative_starting_equity",
                "received": starting_equity_usd,
            },
        )
    if consecutive_losses_today < 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "negative_loss_count",
                "received": consecutive_losses_today,
            },
        )

    ev = evaluate_profile(
        profile_key,
        starting_equity_usd=starting_equity_usd,
        realized_pnl_usd_today=realized_pnl_usd_today,
        consecutive_losses_today=consecutive_losses_today,
    )

    return {
        "profile_key": ev.profile_key,
        "halt": ev.halt,
        "triggers": [
            {
                "rule": t.rule,
                "threshold": t.threshold,
                "observed": t.observed,
                "message": t.message,
            }
            for t in ev.triggers
        ],
        "inputs": dict(ev.inputs),
    }


__all__ = ["router"]
