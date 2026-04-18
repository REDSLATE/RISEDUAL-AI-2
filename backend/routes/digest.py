"""Digest routes: admin trigger, user opt-in/out, preview."""
import logging
from fastapi import APIRouter, HTTPException, Request
from services.auth_helpers import get_current_user
from bson import ObjectId

router = APIRouter(prefix="/api/digest")

db = None

def set_db(database):
    global db
    db = database


@router.post("/trigger")
async def trigger_digest(request: Request):
    """Admin/owner only: manually trigger the daily digest (for testing)."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from services.digest_service import send_daily_digest
    result = await send_daily_digest(db)
    return result


@router.post("/opt-out")
async def opt_out_digest(request: Request):
    """User opts out of daily digest emails."""
    user = await get_current_user(request)
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"digest_opt_out": True}}
    )
    return {"message": "Unsubscribed from daily digest"}


@router.post("/opt-in")
async def opt_in_digest(request: Request):
    """User opts back in to daily digest emails."""
    user = await get_current_user(request)
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"digest_opt_out": False}}
    )
    return {"message": "Subscribed to daily digest"}


@router.get("/status")
async def get_digest_status(request: Request):
    """Check if user is subscribed to digest."""
    user = await get_current_user(request)
    return {"subscribed": not user.get("digest_opt_out", False)}


@router.get("/preview")
async def preview_digest(request: Request):
    """Admin/owner: preview digest HTML without sending."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from services.digest_service import collect_digest_data, build_digest_html, get_user_watchlist_intel
    data = await collect_digest_data(db)
    is_pro = user.get("subscription_status") in ("pro", "trial")
    name = user.get("name", user.get("email", "").split("@")[0])
    wl_intel = await get_user_watchlist_intel(db, user.get("_id"))
    html = build_digest_html(data, is_pro, name, watchlist_intel=wl_intel)
    return {"html": html, "data_summary": {
        "predictions": len(data.get("predictions") or []),
        "smart_money": len(data.get("smart_money") or []),
        "alerts": len(data.get("alerts") or []),
        "has_overview": bool(data.get("overview")),
        "has_watchlist_intel": wl_intel is not None,
    }}
