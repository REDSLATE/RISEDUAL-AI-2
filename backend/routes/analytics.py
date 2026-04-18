"""Referral attribution tracking.

- POST /api/analytics/ref — anonymous pixel-style hit; logs one row per referral
  landing into ``referral_hits``. Dedupes per-visitor per-ref per-day so a reload
  doesn't inflate counts.
- GET /api/analytics/ref-leaderboard — aggregates top 5 sharers.
- GET /api/analytics/ref-me — returns the authenticated user's share stats.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.auth_helpers import get_current_user
from services.referral_rewards import scan_hit_threshold_rewards, scan_monthly_leaderboard_rewards

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

db = None

# Accepted ref pattern: share-u{8 alphanumeric} OR share-anon{8 digits}
REF_RE = re.compile(r"^share-(?:u[a-zA-Z0-9]{1,12}|anon\d{6,10})$")


def set_db(database) -> None:
    global db
    db = database


class RefHit(BaseModel):
    ref: str
    path: Optional[str] = None


def _visitor_hash(request: Request, ref: str) -> str:
    """Compute a per-day dedup key for a single visitor + ref."""
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or request.client.host if request.client else "unknown")
    ua = request.headers.get("user-agent", "")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"{ip}|{ua}|{ref}|{day}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@router.post("/ref")
async def log_ref_hit(payload: RefHit, request: Request) -> dict:
    """Log a single referral landing. Idempotent per visitor per day."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    ref = (payload.ref or "").strip().lower()
    if not ref or not REF_RE.match(ref):
        # Silently ignore invalid refs — don't give attackers feedback
        return {"ok": True, "recorded": False}

    vhash = _visitor_hash(request, ref)
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = await db.referral_hits.update_one(
            {"visitor_hash": vhash},
            {"$setOnInsert": {
                "ref": ref,
                "visitor_hash": vhash,
                "path": (payload.path or "/")[:200],
                "timestamp": now,
            }},
            upsert=True,
        )
        recorded = result.upserted_id is not None
    except Exception as e:
        logger.warning(f"ref log error: {e}")
        recorded = False
    return {"ok": True, "recorded": recorded}


@router.get("/ref-leaderboard")
async def ref_leaderboard(limit: int = 5) -> dict:
    """Top N sharers by unique-visitor hit count over the last 90 days."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    n = max(1, min(limit, 20))
    pipeline = [
        {"$group": {"_id": "$ref", "hits": {"$sum": 1}}},
        {"$sort": {"hits": -1}},
        {"$limit": n},
    ]
    rows = await db.referral_hits.aggregate(pipeline).to_list(n)

    out = []
    rank = 1
    for r in rows:
        ref = r["_id"] or ""
        # Try to resolve the sharer's display name (admin-safe; anonymized otherwise)
        user_suffix = ""
        m = re.match(r"^share-u([a-zA-Z0-9]+)$", ref)
        if m:
            user_suffix = m.group(1)
        out.append({
            "rank": rank,
            "ref": ref,
            "user_suffix": user_suffix,
            "hits": r["hits"],
        })
        rank += 1
    return {"leaderboard": out, "count": len(out)}


@router.get("/ref-me")
async def my_ref_stats(request: Request) -> dict:
    """Return the authenticated user's share stats: their ref code + total hits +
    current rank in the global leaderboard."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Must match the frontend construction: u{sanitized-last-8}
    identifier = user.get("id") or user.get("_id") or user.get("email") or ""
    raw_id = re.sub(r"[^a-zA-Z0-9]", "", str(identifier))[-8:]
    my_ref = f"share-u{raw_id}" if raw_id else None

    if not my_ref:
        return {"ref": None, "hits": 0, "rank": None}

    my_hits = await db.referral_hits.count_documents({"ref": my_ref})

    # Count how many refs have strictly more hits than mine
    pipeline = [
        {"$group": {"_id": "$ref", "hits": {"$sum": 1}}},
        {"$match": {"hits": {"$gt": my_hits}}},
        {"$count": "above"},
    ]
    above = await db.referral_hits.aggregate(pipeline).to_list(1)
    rank = (above[0]["above"] + 1) if above else 1

    # Also surface most-recent reward for the user
    recent_reward = await db.referral_rewards.find_one(
        {"user_id": user["_id"]},
        {"_id": 0, "tier": 1, "kind": 1, "amount": 1, "period": 1, "granted_at": 1, "trial_expires_at": 1},
        sort=[("granted_at", -1)],
    )
    return {"ref": my_ref, "hits": my_hits, "rank": rank, "recent_reward": recent_reward}


@router.post("/ref-scan-hits")
async def trigger_hit_scan(request: Request) -> dict:
    """Admin-only: run the rolling-month hit-threshold reward scan now."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return {"ok": True, "summary": await scan_hit_threshold_rewards(db)}


@router.post("/ref-scan-monthly")
async def trigger_monthly_scan(request: Request) -> dict:
    """Admin-only: run the prior-month leaderboard reward scan now (for testing)."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return {"ok": True, "summary": await scan_monthly_leaderboard_rewards(db)}
