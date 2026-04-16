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
ENTERPRISE_DAILY_LIMIT = 50000

TIER_CONFIG = {
    "free": {
        "daily_limit": FREE_DAILY_LIMIT,
        "endpoints": ["watchlist", "predictions", "quote", "market", "headlines"],
        "label": "Free",
    },
    "pro": {
        "daily_limit": PRO_DAILY_LIMIT,
        "endpoints": ["watchlist", "predictions", "quote", "market", "headlines",
                       "signals", "hypothesis", "war_room", "research", "search",
                       "ml_signal", "sectors", "provider_status"],
        "label": "Pro",
    },
    "enterprise": {
        "daily_limit": ENTERPRISE_DAILY_LIMIT,
        "endpoints": ["watchlist", "predictions", "quote", "market", "headlines",
                       "signals", "hypothesis", "war_room", "research", "search",
                       "ml_signal", "sectors", "provider_status",
                       "paper_trades", "ml_stats", "backtest", "batch_signals"],
        "label": "Enterprise",
    },
}


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


def _get_tier(user: dict) -> str:
    """Determine API tier from user's subscription status."""
    sub = user.get("subscription_status", "")
    if sub == "enterprise":
        return "enterprise"
    if sub in ("pro", "active"):
        return "pro"
    return "free"


async def _check_rate_limit(user_id: str, tier: str) -> bool:
    """Check and increment daily rate limit. Returns True if allowed."""
    if db is None:
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = TIER_CONFIG.get(tier, TIER_CONFIG["free"])["daily_limit"]

    doc = await db.api_usage.find_one(
        {"user_id": user_id, "date": today},
        {"_id": 0}
    )

    current_count = doc.get("count", 0) if doc else 0
    if current_count >= limit:
        return False

    await db.api_usage.update_one(
        {"user_id": user_id, "date": today},
        {"$inc": {"count": 1}, "$set": {"limit": limit, "tier": tier}},
        upsert=True
    )
    return True


async def _auth_via_key(request: Request, x_api_key: Optional[str] = None) -> dict:
    """Authenticate via API key header. Returns user dict with tier info."""
    api_key = x_api_key or request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_doc = await _resolve_api_key(api_key)
    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    user = key_doc["user"]
    user_id = str(user["_id"])
    tier = _get_tier(user)

    allowed = await _check_rate_limit(user_id, tier)
    if not allowed:
        limit = TIER_CONFIG[tier]["daily_limit"]
        raise HTTPException(
            status_code=429,
            detail=f"Daily rate limit exceeded ({limit} calls/day on {tier} tier). Resets at midnight UTC."
        )

    # Track usage on key
    await db.api_keys.update_one(
        {"key": api_key},
        {"$inc": {"total_calls": 1}, "$set": {"last_used": datetime.now(timezone.utc).isoformat()}}
    )

    return {"user": user, "user_id": user_id, "tier": tier, "is_pro": tier in ("pro", "enterprise")}


def _require_tier(auth: dict, min_tier: str) -> None:
    """Raise 403 if user's tier is below the required minimum."""
    tier_order = {"free": 0, "pro": 1, "enterprise": 2}
    user_level = tier_order.get(auth["tier"], 0)
    required_level = tier_order.get(min_tier, 0)
    if user_level < required_level:
        raise HTTPException(
            status_code=403,
            detail=f"This endpoint requires {min_tier} tier. Current tier: {auth['tier']}."
        )


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
    tier = _get_tier(user)

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

    config = TIER_CONFIG[tier]
    used = usage_today.get("count", 0) if usage_today else 0

    return {
        "today": used,
        "limit": config["daily_limit"],
        "remaining": max(0, config["daily_limit"] - used),
        "tier": tier,
        "tier_label": config["label"],
        "endpoints_available": config["endpoints"],
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
    _require_tier(auth, "pro")

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
    _require_tier(auth, "pro")

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
async def api_market_sectors(request: Request):
    """Get sector heatmap data."""
    await _auth_via_key(request)

    from services.sector_service import get_sector_heatmap
    data = await get_sector_heatmap()
    return data


@router.get("/quote/{symbol}")
async def api_quote(symbol: str, request: Request):
    """Get real-time stock quote."""
    await _auth_via_key(request)

    from services.price_provider import get_quote
    quote = await get_quote(symbol.upper())
    if not quote:
        raise HTTPException(status_code=404, detail=f"No quote data for {symbol}")
    return quote


@router.get("/research/{symbol}")
async def api_research(symbol: str, request: Request):
    """Get AI company research. Pro only."""
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")

    cached = await db.research_cache.find_one({"symbol": symbol.upper()}, {"_id": 0})
    if cached:
        return cached
    return {"symbol": symbol.upper(), "status": "no_cached_research", "hint": "Run research via the dashboard first"}


@router.get("/search")
async def api_search(request: Request, q: str = "", symbol: str = ""):
    """Run a War Room search query. Pro only."""
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    from services.search_war_room.orchestrator import run_search
    result = await run_search(query=q, symbol=symbol or None, mode="auto")
    return result.model_dump()


@router.get("/headlines")
async def api_headlines(request: Request, hours: int = 24, limit: int = 50):
    """Get recent scraped headlines."""
    await _auth_via_key(request)

    from services.headlines_pipeline import HeadlinesPipeline
    pipeline = HeadlinesPipeline(db)
    headlines = await pipeline.get_recent(hours=min(hours, 168), limit=min(limit, 200))
    return {"headlines": headlines, "count": len(headlines)}


@router.get("/provider-status")
async def api_provider_status(request: Request):
    """Get provider health summary. Pro only."""
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")

    from services.providerrouter import ProviderRouter
    return {"lanes": ProviderRouter.snapshot()}



# ── ML Signal Endpoints (Pro + Enterprise) ────────────────────────────────────


@router.get("/ml-signal/{ticker}")
async def api_ml_signal(ticker: str, request: Request):
    """Get ML signal prediction for a ticker.

    Returns direction (up/down/flat), confidence, feature importance,
    detected patterns, and model version. Pro tier required.

    This is the core ML prediction endpoint — trained on 276K+ snapshots
    with 62% accuracy and Sharpe 1.56.
    """
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")

    from routes.signal import get_ai_signal
    return await get_ai_signal(ticker, request)


@router.post("/ml-signal/batch")
async def api_ml_signal_batch(request: Request):
    """Batch ML signal predictions for multiple tickers. Enterprise only.

    Body: {"tickers": ["AAPL", "NVDA", "TSLA"]}
    Max 20 tickers per request.
    """
    auth = await _auth_via_key(request)
    _require_tier(auth, "enterprise")

    body = await request.json()
    tickers = body.get("tickers", [])
    if not tickers or len(tickers) > 20:
        raise HTTPException(status_code=400, detail="Provide 1-20 tickers in 'tickers' array")

    from routes.signal import get_ai_signal

    signals = []
    for ticker in tickers:
        try:
            result = await get_ai_signal(ticker.upper(), request)
            signals.append(result)
        except Exception as exc:
            signals.append({"ticker": ticker.upper(), "error": str(exc)})

    return {"signals": signals, "count": len(signals)}


@router.get("/ml-stats")
async def api_ml_stats(request: Request):
    """Get ML pipeline status — data coverage, model stats, tier gates. Pro only."""
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")

    from routes.ml_orchestrator import get_gate_status, get_ml_stats
    gate = await get_gate_status()
    stats = await get_ml_stats()
    return {"gate_status": gate, "stats": stats}


@router.get("/paper-trades")
async def api_paper_trades(request: Request, limit: int = 50):
    """Get recent ML paper trade history. Enterprise only.

    Returns trade log with entry/exit, P&L, confidence, pattern data.
    """
    auth = await _auth_via_key(request)
    _require_tier(auth, "enterprise")

    from routes.ml_orchestrator import get_ml_paper_trades
    return await get_ml_paper_trades(limit=min(limit, 200))


@router.get("/sectors")
async def api_sectors_pro(request: Request, period: str = "1d"):
    """Get sector heatmap data with period filter. Pro only."""
    auth = await _auth_via_key(request)
    _require_tier(auth, "pro")

    valid_periods = ["1d", "1w", "1m", "3m", "ytd"]
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"Invalid period. Use: {valid_periods}")

    from services.sector_service import get_sector_heatmap
    data = await get_sector_heatmap()
    return {"period": period, "sectors": data}


# ── API Info & Documentation ──────────────────────────────────────────────────


@router.get("/info")
async def api_info():
    """Public endpoint — returns API version and available tier details."""
    return {
        "api": "RISEDUAL AI Public API",
        "version": "1.0.0",
        "docs": "https://risedual.ai/api-docs",
        "tiers": {
            tier: {
                "daily_limit": cfg["daily_limit"],
                "endpoints": cfg["endpoints"],
                "label": cfg["label"],
            }
            for tier, cfg in TIER_CONFIG.items()
        },
        "authentication": "Include 'X-API-Key: <your_key>' header in all requests",
        "base_url": "/api/v1",
    }
