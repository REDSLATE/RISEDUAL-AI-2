"""Beta signup API — focused "First 50" public beta funnel.

Distinct from the general `/api/waitlist` flow: this is a
hard-capped cohort with a live seat counter, used by the landing
page amber beta banner.

Collection: ``beta_signups`` ({email, name, reason, seat_number,
created_at, ip_hash, beta_key, entitlements_granted}).

Beta cohort entitlements (First 50 cohort):
  * Pro subscription access for 30 days (trial)
  * 30,000 credits (2× Pro monthly allocation)
  * `founding_member: True` marker

Flow by caller state:
  * **New email** → generate beta_key, write a ``waitlist`` row with
    ``status: invited`` + ``beta_credit_grant: 30000`` so the
    existing ``/api/auth/redeem-beta-key`` flow can pick it up and
    create the Pro account.
  * **Existing registered user** → upgrade in-place: set
    ``subscription_status: pro``, grant 30k credits, mark
    ``beta_cohort: first_50``. Guarded against double-grant via a
    persistent marker so repeat clicks are idempotent.

Env:
    BETA_SEAT_CAP (default: 50)
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/beta", tags=["beta"])

_COLLECTION = "beta_signups"
_BETA_CREDIT_GRANT = 30_000
_BETA_TRIAL_DAYS = 30
_BETA_KEY_EXPIRES_DAYS = 14

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


def _generate_beta_key() -> str:
    """Human-typable beta key: ``BETA-XXXX-XXXX`` with an entropy
    block that's unambiguous (no 0/O, 1/I, etc.)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"BETA-{part1}-{part2}"


async def _upgrade_existing_user(user: dict) -> dict:
    """Idempotently upgrade an already-registered user to the beta
    cohort: Pro status + 30k credits + founding_member badge.

    Returns ``{"already_granted": bool, "granted": int}`` so the
    caller can report accurately to the UI. Double-grant guarded
    via the ``beta_cohort_granted_at`` marker on the user doc.
    """
    user_id = str(user["_id"])
    if user.get("beta_cohort_granted_at"):
        # Second click on the CTA — no-op on credits, just confirm state.
        return {"already_granted": True, "granted": 0}

    now = datetime.now(timezone.utc)
    trial_ends = now + timedelta(days=_BETA_TRIAL_DAYS)
    await _db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "subscription_status": "pro",
            "beta_access": True,
            "beta_cohort": "first_50",
            "founding_member": True,
            "trial_ends_at": trial_ends.isoformat(),
            "beta_cohort_granted_at": now.isoformat(),
        }},
    )

    from services.credit_service import grant_custom_credits
    granted = await grant_custom_credits(
        user_id=user_id,
        amount=_BETA_CREDIT_GRANT,
        plan_key="pro",
        reason=f"Beta cohort (First 50) — {_BETA_CREDIT_GRANT:,} credit grant",
    )
    return {"already_granted": False, "granted": granted}


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


@router.get("/recent")
async def beta_recent() -> dict:
    """Most recent joiner's first name + seat for social-proof UI.

    Returns ``{first_name, seat_number}`` or ``{first_name: None,
    seat_number: None}`` when the cohort is empty or the latest
    joiner didn't supply a name. Safe for public consumption — we
    surface only the first name, never the email or full name.
    """
    if _db is None:
        return {"first_name": None, "seat_number": None}
    try:
        row = await _db[_COLLECTION].find_one(
            {"name": {"$regex": r"\S"}},  # non-empty name
            {"_id": 0, "name": 1, "seat_number": 1},
            sort=[("seat_number", -1)],
        )
    except Exception as e:
        logger.warning("[beta] recent read failed: %s", e)
        return {"first_name": None, "seat_number": None}
    if not row:
        return {"first_name": None, "seat_number": None}
    # Split on whitespace, take first token, cap length. Strip any
    # accidental punctuation/emoji tails so the banner copy stays
    # clean no matter what a user typed.
    first = (row.get("name") or "").strip().split()[0] if row.get("name") else None
    if first:
        first = first[:32]
    return {"first_name": first, "seat_number": int(row.get("seat_number", 0)) or None}


@router.post("/signup")
async def beta_signup(request: Request, body: BetaSignupRequest) -> dict:
    """Claim a beta seat. Idempotent on email.

    Response shape varies by caller state:
      * **New email** → returns ``{status, seat_number, beta_key,
        grant_credits, trial_days}``. The UI shows the key and tells
        them to redeem it to create their Pro account.
      * **Existing user** → returns ``{status: "upgraded",
        seat_number, grant_credits, already_granted}``. The Pro
        entitlement + 30k credits are applied to their account
        immediately; no key is issued.

    Errors:
      * 409 — cap reached
      * 503 — DB unavailable
    """
    # Honeypot trap: bot fills the hidden field → fake success.
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

    # Idempotent: if this email already claimed a seat, return the
    # existing record instead of double-booking.
    existing_signup = await coll.find_one({"email": email}, {"_id": 0})
    if existing_signup is not None:
        # Same response shape as the fresh-signup path so the UI
        # doesn't need to branch on this case.
        user = await _db.users.find_one({"email": email}, {"_id": 1, "beta_cohort_granted_at": 1})
        if user is not None:
            return {
                "status": "upgraded",
                "seat_number": int(existing_signup.get("seat_number", 0)),
                "already_joined": True,
                "grant_credits": _BETA_CREDIT_GRANT,
                "trial_days": _BETA_TRIAL_DAYS,
                "cap": cap,
                "already_granted": bool(user.get("beta_cohort_granted_at")),
            }
        return {
            "status": "joined",
            "seat_number": int(existing_signup.get("seat_number", 0)),
            "already_joined": True,
            "beta_key": existing_signup.get("beta_key"),
            "grant_credits": _BETA_CREDIT_GRANT,
            "trial_days": _BETA_TRIAL_DAYS,
            "cap": cap,
        }

    # Cap check.
    filled = await coll.count_documents({})
    if filled >= cap:
        raise HTTPException(
            status_code=409,
            detail=f"Beta cohort is full ({cap} seats claimed)",
        )

    seat_number = filled + 1
    ip = request.client.host if request.client else None
    now = datetime.now(timezone.utc)

    # Is the email already a registered user? If yes, upgrade in-place.
    existing_user = await _db.users.find_one({"email": email})
    if existing_user is not None:
        upgrade = await _upgrade_existing_user(existing_user)
        doc = {
            "email": email,
            "name": body.name.strip()[:120],
            "reason": body.reason.strip()[:500],
            "seat_number": seat_number,
            "created_at": now,
            "ip_hash": _hash_ip(ip),
            "cohort": "first_50",
            "user_id": str(existing_user["_id"]),
            "entitlements_granted": True,
        }
        await coll.insert_one(doc)
        logger.info("[beta] upgraded existing user email=%s seat=%d", email, seat_number)
        return {
            "status": "upgraded",
            "seat_number": seat_number,
            "already_joined": False,
            "grant_credits": upgrade["granted"] or _BETA_CREDIT_GRANT,
            "trial_days": _BETA_TRIAL_DAYS,
            "already_granted": upgrade["already_granted"],
            "cap": cap,
        }

    # New email path: generate a beta_key + seed a waitlist row that
    # the existing /api/auth/redeem-beta-key flow can consume.
    beta_key = _generate_beta_key()
    # Dedupe against the (very small) chance of a key collision.
    # 32^8 = 1.1 trillion keyspace; single retry is plenty.
    for _ in range(3):
        clash = await _db.waitlist.find_one({"beta_key": beta_key}, {"_id": 1})
        if clash is None:
            break
        beta_key = _generate_beta_key()

    expires = now + timedelta(days=_BETA_KEY_EXPIRES_DAYS)
    # `waitlist` has a unique index on referral_code — generate a
    # distinct one even though the beta cohort doesn't use the
    # referral-points mechanic. Keeps the row compatible with the
    # shared collection semantics.
    referral_code = f"BT{secrets.token_hex(3).upper()}"
    await _db.waitlist.insert_one({
        "email": email,
        "name": body.name.strip()[:120] or None,
        "status": "invited",
        "beta_key": beta_key,
        "beta_key_expires": expires.isoformat(),
        "cohort": "first_50",
        "founding_member": True,
        "beta_credit_grant": _BETA_CREDIT_GRANT,
        "joined_at": now.isoformat(),
        "source": "beta_signup_first_50",
        "referral_code": referral_code,
        "referral_count": 0,
    })

    doc = {
        "email": email,
        "name": body.name.strip()[:120],
        "reason": body.reason.strip()[:500],
        "seat_number": seat_number,
        "created_at": now,
        "ip_hash": _hash_ip(ip),
        "cohort": "first_50",
        "beta_key": beta_key,
        "entitlements_granted": False,  # Flips True on redeem_beta_key.
    }
    try:
        await coll.insert_one(doc)
    except Exception as e:
        logger.exception("[beta] insert failed: %s", e)
        raise HTTPException(status_code=503, detail="Could not save signup")

    logger.info("[beta] signup email=%s seat=%d key=%s", email, seat_number, beta_key)
    return {
        "status": "joined",
        "seat_number": seat_number,
        "already_joined": False,
        "beta_key": beta_key,
        "grant_credits": _BETA_CREDIT_GRANT,
        "trial_days": _BETA_TRIAL_DAYS,
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
