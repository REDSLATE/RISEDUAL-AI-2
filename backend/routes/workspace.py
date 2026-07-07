"""Workspace routes: watchlist, hypothesis history, notifications."""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone

from services.auth_helpers import get_current_user, is_pro_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


async def _merge_broker_holdings(user_id: str, current_tickers: list[str]) -> list[str]:
    """Best-effort merge of live broker positions into the watchlist.

    Reads every connected broker for this user, pulls current positions
    via the same client factory the broker routes use, and unions the
    symbols with whatever manual tickers the user already has. If any
    single broker call fails (network, expired key, rate limit) the
    failure is swallowed and the manual list is still returned. Also
    persists any newly-discovered broker symbols so subsequent reads
    are cache-fast even if the broker API is briefly unreachable.
    """
    try:
        from routes import broker as broker_routes
    except Exception:
        return current_tickers

    merged = {(t or "").upper().strip() for t in current_tickers if (t or "").strip()}

    cursor = db.broker_connections.find(
        {"user_id": user_id, "status": {"$in": ["active", "connected"]}},
    )
    conns = await cursor.to_list(length=20)
    if not conns:
        return sorted(merged)

    async def _positions_for(conn: dict) -> list[str]:
        broker_id = conn.get("broker_id") or conn.get("broker") or ""
        try:
            client = await broker_routes._get_or_refresh_client(user_id, broker_id, conn)
            positions = await asyncio.to_thread(client.get_positions)
        except Exception as exc:
            logger.info(f"[watchlist_merge] broker={broker_id} skipped: {exc}")
            return []
        return [
            (p.get("symbol") or "").upper().strip()
            for p in (positions or [])
            if (p.get("symbol") or "").strip()
        ]

    results = await asyncio.gather(
        *[_positions_for(c) for c in conns], return_exceptions=True,
    )

    broker_symbols: set[str] = set()
    for r in results:
        if isinstance(r, list):
            broker_symbols.update(r)

    added = broker_symbols - merged
    merged |= broker_symbols

    if added:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            await db.watchlists.update_one(
                {"user_id": user_id},
                {
                    "$addToSet": {"tickers": {"$each": sorted(added)}},
                    "$set": {"updated_at": now_iso},
                    "$setOnInsert": {"created_at": now_iso},
                },
                upsert=True,
            )
        except Exception as exc:
            logger.warning(f"[watchlist_merge] persist failed (non-fatal): {exc}")

    return sorted(merged)


# --- Watchlist ---
@router.get("/workspace/watchlist")
async def get_watchlist(request: Request):
    """Return the user's watchlist, auto-merged with live broker holdings.

    The stored tickers are the manual entries. Symbols the user
    currently holds in any connected broker are unioned in on every
    read so the UI never shows an empty watchlist while positions
    exist. Newly-discovered broker symbols are persisted back so the
    list survives a broker outage.
    """
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    doc = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0})
    manual = doc.get("tickers", []) if doc else []
    tickers = await _merge_broker_holdings(user_id, manual)
    return {"tickers": tickers}


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
        {
            "user_id": user["_id"],
            # Lifecycle filter — hide rows that have been
            # superseded / resolved / dismissed by the regrade
            # cleanup or by the operator. See
            # ``services/notification_lifecycle.py``.
            "$and": [
                {"$or": [{"resolved": {"$exists": False}},
                         {"resolved": False}]},
                {"$or": [{"status": {"$exists": False}},
                         {"status": {"$nin": [
                             "resolved", "superseded", "dismissed",
                         ]}}]},
            ],
        },
        {"_id": 0, "type": 1, "title": 1, "message": 1, "read": 1, "created_at": 1, "metadata": 1, "symbol": 1, "new_verdict": 1, "old_verdict": 1, "confidence": 1, "in_watchlist": 1}
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
    count = await db.notifications.count_documents({
        "user_id": user["_id"], "read": False,
        "$and": [
            {"$or": [{"resolved": {"$exists": False}},
                     {"resolved": False}]},
            {"$or": [{"status": {"$exists": False}},
                     {"status": {"$nin": [
                         "resolved", "superseded", "dismissed",
                     ]}}]},
        ],
    })
    return {"count": count, "is_pro": True}


@router.post("/notifications/read-all")
async def mark_all_read(request: Request):
    user = await get_current_user(request)
    await db.notifications.update_many(
        {"user_id": user["_id"], "read": False},
        {"$set": {"read": True}}
    )
    return {"message": "All notifications marked as read"}
