"""Workspace routes: watchlist, hypothesis history, notifications."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone

from services.auth_helpers import get_current_user, is_pro_user

router = APIRouter(prefix="/api")

# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


# --- Watchlist ---
@router.get("/workspace/watchlist")
async def get_watchlist(request: Request):
    user = await get_current_user(request)
    doc = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0})
    return {"tickers": doc.get("tickers", []) if doc else []}


FREE_WATCHLIST_LIMIT = 3
FREE_CHAT_DAILY_LIMIT = 5


@router.post("/workspace/watchlist/add")
async def add_to_watchlist(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    ticker = body.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker required")
    # Enforce watchlist cap for free users
    if not is_pro_user(user):
        doc = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0, "tickers": 1})
        current = doc.get("tickers", []) if doc else []
        if ticker not in current and len(current) >= FREE_WATCHLIST_LIMIT:
            raise HTTPException(status_code=403, detail=f"Free accounts are limited to {FREE_WATCHLIST_LIMIT} watchlist tickers. Upgrade to Pro for unlimited.")
    await db.watchlists.update_one(
        {"user_id": user["_id"]},
        {"$addToSet": {"tickers": ticker}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    return {"message": f"{ticker} added to watchlist"}


@router.post("/workspace/watchlist/remove")
async def remove_from_watchlist(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    ticker = body.get("ticker", "").upper().strip()
    await db.watchlists.update_one(
        {"user_id": user["_id"]},
        {"$pull": {"tickers": ticker}}
    )
    return {"message": f"{ticker} removed from watchlist"}


# --- Hypothesis History ---
@router.get("/workspace/history")
async def get_hypothesis_history(request: Request):
    user = await get_current_user(request)
    cursor = db.hypothesis_history.find(
        {"user_id": user["_id"]}, {"_id": 0, "symbol": 1, "verdict": 1, "confidence": 1, "searched_at": 1}
    ).sort("searched_at", -1).limit(20)
    history = []
    async for doc in cursor:
        history.append(doc)
    return {"history": history}


@router.post("/workspace/history/save")
async def save_hypothesis_history(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    symbol = body.get("symbol", "").upper().strip()
    verdict = body.get("verdict", "")
    confidence = body.get("confidence", 0)
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")
    await db.hypothesis_history.insert_one({
        "user_id": user["_id"],
        "symbol": symbol,
        "verdict": verdict,
        "confidence": confidence,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"message": "Saved to history"}


# --- Notifications (Pro Only) ---
@router.get("/notifications")
async def get_notifications(request: Request):
    user = await get_current_user(request)
    if not is_pro_user(user):
        return {"notifications": [], "unread_count": 0, "is_pro": False}
    cursor = db.notifications.find(
        {"user_id": user["_id"]}, {"_id": 0, "type": 1, "title": 1, "message": 1, "read": 1, "created_at": 1, "metadata": 1, "symbol": 1, "new_verdict": 1, "old_verdict": 1, "confidence": 1, "in_watchlist": 1}
    ).sort("created_at", -1).limit(30)
    notifications = []
    async for doc in cursor:
        notifications.append(doc)
    unread = sum(1 for n in notifications if not n.get("read"))
    return {"notifications": notifications, "unread_count": unread, "is_pro": True}


@router.get("/notifications/unread-count")
async def get_unread_count(request: Request):
    user = await get_current_user(request)
    if not is_pro_user(user):
        return {"count": 0, "is_pro": False}
    count = await db.notifications.count_documents({"user_id": user["_id"], "read": False})
    return {"count": count, "is_pro": True}


@router.post("/notifications/read-all")
async def mark_all_read(request: Request):
    user = await get_current_user(request)
    await db.notifications.update_many(
        {"user_id": user["_id"], "read": False},
        {"$set": {"read": True}}
    )
    return {"message": "All notifications marked as read"}
