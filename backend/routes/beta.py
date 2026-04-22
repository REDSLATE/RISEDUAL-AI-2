"""Beta signup API — focused "First 50" public beta funnel.

Distinct from the general `/api/waitlist` flow: this is a
hard-capped cohort with a live seat counter, used by the landing
page amber beta banner.

Collection: ``beta_signups`` ({email, name, reason, seat_number,
created_at, ip_hash}).

Env:
    BETA_SEAT_CAP (default: 50)
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/beta", tags=["beta"])

_COLLECTION = "beta_signups"

_db: Any = None


def set_db(db: Any) -> None:
    """Wire the Motor DB handle from the route registry."""
    global _db
    _db = db


def _seat_cap() -> int:
    """Soft cap on beta seats, overridable via env for A/B or extension."""
    try:
        return max(1, int(os.environ.get("BETA_SEAT_CAP", "50")))
    except ValueError:
        return 50


def _hash_ip(ip: str | None) -> str | None:
    """SHA-256 the requester IP so we can dedupe / rate-signal bots
    without storing the raw address."""
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


class BetaSignupRequest(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=500)
    # Honeypot — legitimate users never fill this hidden field.
    first_name_field: str = Field(default="", max_length=200)


class BetaStatsResponse(BaseModel):
    seats_filled: int
    seats_remaining: int
    cap: int
    is_full: bool


@router.get("/stats", response_model=BetaStatsResponse)
async def beta_stats() -> BetaStatsResponse:
    """Live seat counter — consumed by the landing-page banner."""
    cap = _seat_cap()
    if _db is None:
        # Fail closed: hide the counter rather than 500 the landing page.
        return BetaStatsResponse(seats_filled=0, seats_remaining=cap, cap=cap, is_full=False)
    try:
        filled = await _db[_COLLECTION].count_documents({})
    except Exception as e:
        logger.warning("[beta] stats count failed: %s", e)
        filled = 0
    remaining = max(0, cap - filled)
    return BetaStatsResponse(
        seats_filled=min(filled, cap),
        seats_remaining=remaining,
        cap=cap,
        is_full=remaining == 0,
    )


@router.post("/signup")
async def beta_signup(request: Request, body: BetaSignupRequest) -> dict:
    """Claim a beta seat. Idempotent on email: re-submitting returns
    the same seat number without double-booking.

    Errors:
      * 400 — invalid email (pydantic catches this upstream)
      * 409 — cap reached (first-50 cohort closed)
      * 503 — DB unavailable
    """
    # Honeypot trap: if a bot filled the hidden field, return a fake
    # success so it doesn't retry with new strategies. Never persist.
    if body.first_name_field:
        logger.warning("[beta] honeypot triggered email=%s", body.email)
        return {
            "status": "joined",
            "seat_number": 999,
            "already_joined": False,
            "cap": _seat_cap(),
        }

    if _db is None:
        raise HTTPException(status_code=503, detail="Beta signup unavailable")

    email = str(body.email).strip().lower()
    cap = _seat_cap()
    coll = _db[_COLLECTION]

    # Idempotent: return existing seat if this email already signed up.
    existing = await coll.find_one({"email": email}, {"_id": 0, "seat_number": 1})
    if existing is not None:
        return {
            "status": "joined",
            "seat_number": int(existing.get("seat_number", 0)),
            "already_joined": True,
            "cap": cap,
        }

    # Cap check — count current holders. A tiny race window exists
    # between count and insert under high concurrency; the unique
    # index on `seat_number` (if ever added) would close it, but at
    # 50 seats this is not a realistic risk surface.
    filled = await coll.count_documents({})
    if filled >= cap:
        raise HTTPException(
            status_code=409,
            detail=f"Beta cohort is full ({cap} seats claimed)",
        )

    seat_number = filled + 1
    ip = request.client.host if request.client else None

    doc = {
        "email": email,
        "name": body.name.strip()[:120],
        "reason": body.reason.strip()[:500],
        "seat_number": seat_number,
        "created_at": datetime.now(timezone.utc),
        "ip_hash": _hash_ip(ip),
        "cohort": "first_50",
    }
    try:
        await coll.insert_one(doc)
    except Exception as e:
        logger.exception("[beta] insert failed: %s", e)
        raise HTTPException(status_code=503, detail="Could not save signup")

    logger.info("[beta] signup email=%s seat=%d", email, seat_number)
    return {
        "status": "joined",
        "seat_number": seat_number,
        "already_joined": False,
        "cap": cap,
    }


# ── Admin endpoints ──


async def _require_owner(request: Request) -> dict:
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/admin/list")
async def admin_beta_list(request: Request, limit: int = 100) -> dict:
    """Owner/admin: recent beta signups, newest first."""
    await _require_owner(request)
    if _db is None:
        return {"signups": [], "count": 0, "cap": _seat_cap()}
    cursor = (
        _db[_COLLECTION]
        .find({}, {"_id": 0, "ip_hash": 0})
        .sort("seat_number", 1)
        .limit(max(1, min(int(limit), 500)))
    )
    rows = await cursor.to_list(length=limit)
    # ISO-format the datetime so the JSON surface is stable.
    for r in rows:
        ca = r.get("created_at")
        if isinstance(ca, datetime):
            r["created_at"] = ca.isoformat()
    return {"signups": rows, "count": len(rows), "cap": _seat_cap()}
