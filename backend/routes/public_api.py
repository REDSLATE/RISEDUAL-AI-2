"""Public API routes — user-facing API with key-based auth and rate limiting.

Free users: watchlist + predictions, 100 calls/day
Pro users: watchlist + AI signals (War Room, Hypothesis, Predictions), 5,000 calls/day
"""
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Header
from bson import ObjectId

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["public-api"])
key_router = APIRouter(prefix="/api/developer", tags=["developer"])

db = None

FREE_DAILY_LIMIT = 100
PRO_DAILY_LIMIT = 5000


def set_db(database):
    global db
    db = database


def _generate_api_key() -> str:
    """Generate a unique API key: rsd_live_XXXX..."""
    return f"rsd_live_{secrets.token_hex(24)}"


async def _resolve_api_key(api_key: str) -> Optional[dict]:
    """Look up an API key and return the user + key doc."""
    if db is None:
        return None
    key_doc = await db.api_keys.find_one(
        {"key": api_key, "is_active": True},
        {"_id": 0}
    )
    if not key_doc:
        return None

    user = await db.users.find_one({"_id": ObjectId(key_doc["user_id"])})
    if not user:
        return None

    key_doc["user"] = user
    return key_doc


async def _check_rate_limit(user_id: str, is_pro: bool) -> bool:
    """Check and increment daily rate limit. Returns True if allowed."""
    if db is None:
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = PRO_DAILY_LIMIT if is_pro else FREE_DAILY_LIMIT

    doc = await db.api_usage.find_one(
        {"user_id": user_id, "date": today},
        {"_id": 0}
    )

    current_count = doc.get("count", 0) if doc else 0
    if current_count >= limit:
        return False

    await db.api_usage.update_one(
        {"user_id": user_id, "date": today},
        {"$inc": {"count": 1}, "$set": {"limit": limit}},
        upsert=True
    )
    return True


async def _auth_via_key(request: Request, x_api_key: Optional[str] = None) -> dict:
    """Authenticate via API key header. Returns user dict with _is_pro flag."""
    api_key = x_api_key or request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_doc = await _resolve_api_key(api_key)
    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    user = key_doc["user"]
    user_id = str(user["_id"])
    is_pro = user.get("subscription_status") == "pro"

    allowed = await _check_rate_limit(user_id, is_pro)
    if not allowed:
        limit = PRO_DAILY_LIMIT if is_pro else FREE_DAILY_LIMIT
        raise HTTPException(
            status_code=429,
            detail=f"Daily rate limit exceeded ({limit} calls/day). Resets at midnight UTC."
        )

    # Track usage on key
    await db.api_keys.update_one(
        {"key": api_key},
        {"$inc": {"total_calls": 1}, "$set": {"last_used": datetime.now(timezone.utc).isoformat()}}
    )

    return {"user": user, "user_id": user_id, "is_pro": is_pro}


# ══════════════════════════════════════════════════
#  KEY MANAGEMENT (cookie-auth — from dashboard)
# ══════════════════════════════════════════════════

@key_router.post("/keys/generate")
async def generate_api_key(request: Request):
    """Generate a new API key for the logged-in user."""
    user = await get_current_user(request)
    user_id = str(user["_id"])

    # Limit to 3 active keys per user
    active_count = await db.api_keys.count_documents({"user_id": user_id, "is_active": True})
    if active_count >= 3:
        raise HTTPException(status_code=400, detail="Maximum 3 active API keys. Revoke one first.")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    label = body.get("label", "Default")
    key = _generate_api_key()

    await db.api_keys.insert_one({
        "user_id": user_id,
        "key": key,
        "label": label,
        "is_active": True,
        "total_calls": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used": None,
    })

    return {"key": key, "label": label, "message": "Save this key — it won't be shown again in full."}


@key_router.get("/keys")
async def list_api_keys(request: Request):
    """List all API keys for the logged-in user (masked)."""
    user = await get_current_user(request)
    user_id = str(user["_id"])

    cursor = db.api_keys.find({"user_id": user_id}, {"_id": 0})
    keys = []
    async for doc in cursor:
        full_key = doc["key"]
        keys.append({
            "key_preview": full_key[:12] + "..." + full_key[-4:],
            "key_id": full_key[-8:],
            "label": doc.get("label", ""),
            "is_active": doc.get("is_active", True),
            "total_calls": doc.get("total_calls", 0),
            "created_at": doc.get("created_at"),
            "last_used": doc.get("last_used"),
        })

    return {"keys": keys}


@key_router.delete("/keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an API key by its last 8 chars."""
    user = await get_current_user(request)
    user_id = str(user["_id"])

    result = await db.api_keys.update_one(
        {"user_id": user_id, "key": {"$regex": f"{key_id}$"}},
        {"$set": {"is_active": False, "revoked_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"success": True}


@key_router.get("/usage")
async def get_api_usage(request: Request):
    """Get API usage stats for the logged-in user."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    is_pro = user.get("subscription_status") == "pro"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage_today = await db.api_usage.find_one(
        {"user_id": user_id, "date": today},
        {"_id": 0}
    )

    # Last 7 days
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    cursor = db.api_usage.find(
        {"user_id": user_id, "date": {"$gte": week_ago}},
        {"_id": 0}
    ).sort("date", -1)
    history = []
    async for doc in cursor:
        history.append(doc)

    limit = PRO_DAILY_LIMIT if is_pro else FREE_DAILY_LIMIT
    used = usage_today.get("count", 0) if usage_today else 0

    return {
        "today": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "tier": "pro" if is_pro else "free",
        "history": history,
    }


# ══════════════════════════════════════════════════
#  PUBLIC API ENDPOINTS (key-auth)
# ══════════════════════════════════════════════════

@router.get("/watchlist")
async def api_get_watchlist(request: Request):
    """Get user's watchlist tickers and prices."""
    auth = await _auth_via_key(request)
    user_id = auth["user_id"]

    doc = await db.watchlists.find_one({"user_id": user_id}, {"_id": 0})
    tickers = doc.get("tickers", []) if doc else []

    # Fetch live prices for watchlist
    prices = []
    if tickers:
        from services.price_provider import get_quote
        import asyncio
        results = await asyncio.gather(
            *[get_quote(t) for t in tickers[:20]],
            return_exceptions=True
        )
        for t, r in zip(tickers[:20], results):
            if isinstance(r, dict) and r.get("price"):
                prices.append(r)
            else:
                prices.append({"symbol": t, "price": None, "source": "unavailable"})

    return {
        "tickers": tickers,
        "count": len(tickers),
        "prices": prices,
    }


@router.get("/predictions")
async def api_get_predictions(request: Request):
    """Get recent AI market predictions."""
    await _auth_via_key(request)

    cursor = db.predictions.find(
        {},
        {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1, "reasoning": 1,
         "created_at": 1, "verified_24h": 1, "target_price": 1}
    ).sort("created_at", -1).limit(20)

    predictions = []
    async for p in cursor:
        predictions.append(p)

    return {"predictions": predictions, "count": len(predictions)}


@router.get("/predictions/{symbol}")
async def api_get_symbol_predictions(symbol: str, request: Request):
    """Get predictions for a specific symbol."""
    await _auth_via_key(request)

    cursor = db.predictions.find(
        {"symbol": symbol.upper()},
        {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1, "reasoning": 1,
         "created_at": 1, "verified_24h": 1, "target_price": 1}
    ).sort("created_at", -1).limit(10)

    predictions = []
    async for p in cursor:
        predictions.append(p)

    return {"symbol": symbol.upper(), "predictions": predictions, "count": len(predictions)}


@router.get("/signals/war-room/{symbol}")
async def api_war_room(symbol: str, request: Request):
    """Get AI War Room analysis for a symbol. Pro only."""
    auth = await _auth_via_key(request)
    if not auth["is_pro"]:
        raise HTTPException(status_code=403, detail="AI signals require Pro subscription")

    # Check cache first
    cached = await db.war_room_cache.find_one(
        {"symbol": symbol.upper()},
        {"_id": 0}
    )
    if cached:
        return cached

    return {"symbol": symbol.upper(), "status": "no_cached_analysis", "hint": "Run analysis via the dashboard first"}


@router.get("/signals/hypothesis/{symbol}")
async def api_hypothesis(symbol: str, request: Request):
    """Get AI Investment Hypothesis for a symbol. Pro only."""
    auth = await _auth_via_key(request)
    if not auth["is_pro"]:
        raise HTTPException(status_code=403, detail="AI signals require Pro subscription")

    cursor = db.hypothesis_history.find(
        {"symbol": symbol.upper()},
        {"_id": 0}
    ).sort("created_at", -1).limit(5)

    results = []
    async for h in cursor:
        results.append(h)

    return {"symbol": symbol.upper(), "hypotheses": results, "count": len(results)}


@router.get("/market/fear-greed")
async def api_fear_greed(request: Request):
    """Get current Fear & Greed index."""
    await _auth_via_key(request)

    from services.fear_greed_service import FearGreedService
    fg = FearGreedService()
    data = await fg.get_current()
    return data


@router.get("/market/sectors")
async def api_sectors(request: Request):
    """Get sector heatmap data."""
    await _auth_via_key(request)

    from services.sector_service import get_sector_heatmap
    data = await get_sector_heatmap()
    return data
