"""Digest routes: admin trigger, user opt-in/out, preview, on-demand send."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from services.auth_helpers import get_current_user
from bson import ObjectId

router = APIRouter(prefix="/api/digest")

db = None

# Rate-limit: one on-demand digest per user per hour.
ON_DEMAND_COOLDOWN = timedelta(hours=1)


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


@router.post("/send-now")
async def send_on_demand_digest(request: Request):
    """Send a fresh market digest to the authenticated user's inbox now.

    Rate-limited to one send per hour per user. Bypasses the opt-out flag
    because the user is explicitly asking for this single email.
    """
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)

    last = user.get("last_on_demand_digest_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(last) if isinstance(last, str) else last
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt < ON_DEMAND_COOLDOWN:
                retry_after = int((ON_DEMAND_COOLDOWN - (now - last_dt)).total_seconds())
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {retry_after // 60} min before requesting another digest.",
                    headers={"Retry-After": str(retry_after)},
                )
        except HTTPException:
            raise
        except Exception:
            # Corrupted timestamp — treat as if never sent and move on.
            pass

    from services.digest_service import send_digest_to_user
    result = await send_digest_to_user(db, user)

    if result.get("sent"):
        await db.users.update_one(
            {"_id": ObjectId(user["_id"])},
            {"$set": {"last_on_demand_digest_at": now.isoformat()}},
        )
        return {"status": "sent", **result}
    # Surface a helpful reason instead of a 500.
    reason = result.get("reason", "unknown")
    raise HTTPException(status_code=503, detail=f"Digest could not be sent ({reason}). Please try again later.")

