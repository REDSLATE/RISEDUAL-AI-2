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


# ── Help Center search telemetry ──
class HelpSearchEvent(BaseModel):
    q: str
    results_count: int = 0
    context_hub: Optional[str] = None


@router.post("/help-search")
async def log_help_search(payload: HelpSearchEvent, request: Request) -> dict:
    """Fire-and-forget telemetry for Help Center searches.

    Tracks every non-trivial query so the admin panel can surface zero-result
    searches — the single best signal for feature gaps / doc gaps.
    """
    if db is None:
        return {"ok": False, "reason": "db_not_ready"}
    q = (payload.q or "").strip().lower()
    if not q or len(q) < 2 or len(q) > 120:
        return {"ok": False, "reason": "skip"}

    user = await get_current_user(request)
    doc = {
        "q": q,
        "results_count": max(0, int(payload.results_count or 0)),
        "context_hub": (payload.context_hub or "").strip()[:24] or None,
        "user_id": str(user["_id"]) if user else None,
        "is_anon": user is None,
        "ts": datetime.now(timezone.utc),
    }
    await db.help_search_events.insert_one(doc)
    return {"ok": True}


@router.get("/help-search/stats")
async def help_search_stats(request: Request, days: int = 30, limit: int = 20) -> dict:
    """Admin-only: top zero-result queries + overall volume for the last N days."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    total = await db.help_search_events.count_documents({"ts": {"$gte": since}})
    zero_total = await db.help_search_events.count_documents(
        {"ts": {"$gte": since}, "results_count": 0}
    )

    # Top zero-result queries
    zero_pipeline = [
        {"$match": {"ts": {"$gte": since}, "results_count": 0}},
        {"$group": {"_id": "$q", "count": {"$sum": 1},
                    "last_seen": {"$max": "$ts"},
                    "hubs": {"$addToSet": "$context_hub"}}},
        {"$sort": {"count": -1, "last_seen": -1}},
        {"$limit": max(1, min(limit, 100))},
    ]
    zero_top = []
    async for row in db.help_search_events.aggregate(zero_pipeline):
        zero_top.append({
            "q": row["_id"],
            "count": row["count"],
            "last_seen": row["last_seen"].isoformat() if row.get("last_seen") else None,
            "hubs": [h for h in (row.get("hubs") or []) if h],
        })

    # Overall top queries (any result count) for context
    top_pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {"_id": "$q", "count": {"$sum": 1},
                    "avg_results": {"$avg": "$results_count"}}},
        {"$sort": {"count": -1}},
        {"$limit": max(1, min(limit, 100))},
    ]
    top_queries = []
    async for row in db.help_search_events.aggregate(top_pipeline):
        top_queries.append({
            "q": row["_id"],
            "count": row["count"],
            "avg_results": round(row.get("avg_results") or 0, 1),
        })

    return {
        "window_days": days,
        "total_events": total,
        "zero_result_events": zero_total,
        "zero_result_rate": round((zero_total / total) if total else 0, 3),
        "zero_result_top": zero_top,
        "top_queries": top_queries,
    }


@router.post("/help-search/send-digest")
async def trigger_help_search_digest(request: Request) -> dict:
    """Admin-only: manually trigger the weekly Help-Search digest email (on-demand)."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    from services.help_search_digest import send_help_search_digest
    return await send_help_search_digest(db)
