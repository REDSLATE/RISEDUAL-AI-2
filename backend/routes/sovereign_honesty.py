"""Honesty Mirror — local proxy to MC's intent honesty aggregate.

Surface the same intent-audit view that lives at MC's
``/api/admin/intents/honesty?stack=alpha&hours=24``, but inside the
RISEDUAL admin UI. The operator should never have to leave the brain
to see their own honesty receipts.

Auth: owner-only (mirrors the rest of the admin routes).

Why a proxy + not direct browser-to-MC: the operator's session token
is a RISEDUAL admin JWT, not an MC runtime token. The brain has the
runtime token in `.env`; the browser must not.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sovereign", tags=["sovereign"])


# ── helpers ────────────────────────────────────────────────────────────


def _require_owner(user: dict) -> None:
    """The honesty mirror is owner-only. Free users see the public
    receipt on the hypothesis page; this aggregate stays internal.

    Canonical role check — matches routes/auth.py. The earlier
    ``user.get("is_owner")`` check was a bug (the schema uses
    ``role``); fixed 2026-05-17.
    """
    if not user or user.get("role") != "owner":
        raise HTTPException(
            status_code=403, detail="owner-only endpoint",
        )


# ── route ──────────────────────────────────────────────────────────────


@router.get("/honesty-mirror")
async def honesty_mirror(
    request: Request,
    stack: str = Query("alpha", description="brain stack key"),
    hours: int = Query(24, ge=1, le=168, description="lookback window, hours"),
) -> dict[str, Any]:
    """Owner-only proxy of MC's ``/api/admin/intents/honesty``.

    Returns the same shape MC emits — total intents, blocked-directional
    count, top hold_reasons, council_penalty distribution — plus a
    ``mc_status`` field so the operator can see at a glance whether
    the data is live or MC is unreachable.
    """
    user = await get_current_user(request)
    _require_owner(user)

    base = os.environ.get("MC_BASE_URL", "").rstrip("/")
    token = os.environ.get(f"{stack.upper()}_INGEST_TOKEN", "")
    if not base:
        return {
            "mc_status": "unconfigured",
            "stack": stack, "hours": hours,
            "total_intents": 0, "blocked_directional": 0,
            "by_reason": {}, "penalty_distribution": [],
            "note": "MC_BASE_URL not set in this pod's environment.",
        }

    url = f"{base}/api/admin/intents/honesty"
    headers = {"X-Runtime-Token": token} if token else {}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=6.0, write=5.0, pool=2.0),
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            r = await client.get(
                url, params={"stack": stack, "hours": hours}, headers=headers,
            )
        if r.status_code == 200:
            payload = r.json() or {}
            payload.setdefault("stack", stack)
            payload.setdefault("hours", hours)
            payload["mc_status"] = "live"
            return payload
        # Surface MC's status code so the operator can diagnose
        # auth / route mismatches without grep-ing logs.
        return {
            "mc_status": f"mc_returned_{r.status_code}",
            "stack": stack, "hours": hours,
            "total_intents": 0, "blocked_directional": 0,
            "by_reason": {}, "penalty_distribution": [],
            "note": (r.text or "")[:200],
        }
    except httpx.HTTPError as exc:
        logger.warning("honesty_mirror MC fetch failed: %s", exc)
        return {
            "mc_status": "unreachable",
            "stack": stack, "hours": hours,
            "total_intents": 0, "blocked_directional": 0,
            "by_reason": {}, "penalty_distribution": [],
            "note": f"MC unreachable: {type(exc).__name__}",
        }
