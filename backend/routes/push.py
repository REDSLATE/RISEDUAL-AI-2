"""Push notification routes: subscribe, unsubscribe, status, test."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timezone
from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api/push")

db = None

def set_db(database):
    global db
    db = database


class PushSubscription(BaseModel):
    subscription: dict


@router.post("/subscribe")
async def subscribe_push(req: PushSubscription, request: Request):
    """Store user's push subscription for notifications."""
    user = await get_current_user(request)
    user_id = user["_id"]
    is_pro = user.get("subscription_status") in ("pro", "trial")

    # Upsert subscription
    await db.push_subscriptions.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "subscription": req.subscription,
            "is_pro": is_pro,
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True
    )
    return {"message": "Push notifications enabled"}


@router.post("/unsubscribe")
async def unsubscribe_push(request: Request):
    """Remove user's push subscription."""
    user = await get_current_user(request)
    await db.push_subscriptions.delete_one({"user_id": user["_id"]})
    return {"message": "Push notifications disabled"}


@router.get("/status")
async def get_push_status(request: Request):
    """Check if user has active push subscription."""
    user = await get_current_user(request)
    sub = await db.push_subscriptions.find_one({"user_id": user["_id"]}, {"_id": 0, "subscription": 0})
    return {
        "subscribed": sub is not None,
        "is_pro": user.get("subscription_status") in ("pro", "trial"),
    }


@router.get("/vapid-key")
async def get_vapid_key():
    """Public: return VAPID public key for frontend subscription."""
    import os
    key = os.environ.get('VAPID_PUBLIC_KEY', '')
    return {"public_key": key}


@router.post("/test")
async def test_push(request: Request):
    """Admin only: send a test push notification."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from services.push_service import send_push
    sub = await db.push_subscriptions.find_one({"user_id": user["_id"]})
    if not sub:
        raise HTTPException(status_code=400, detail="No push subscription found. Enable notifications first.")

    success = await send_push(
        subscription_info=sub["subscription"],
        title="RISEDUAL AI Test",
        body="Push notifications are working!",
        url="/",
        tag="test",
        db=db,
        user_id=user["_id"],
        is_pro=True,
    )
    return {"sent": success}


@router.post("/broadcast")
async def broadcast_push(request: Request):
    """Admin only: broadcast a notification to all subscribers."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()
    title = body.get("title", "RISEDUAL AI Update")
    message = body.get("message", "Check out the latest market signals")

    from services.push_service import broadcast_notification
    result = await broadcast_notification(db, title, message)
    return result
