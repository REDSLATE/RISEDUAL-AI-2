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


@router.get("/my-preview")
async def my_digest_preview(request: Request):
    """Return a preview of the fresh digest for the authed user
    WITHOUT actually sending. Used by the on-demand confirm-modal:
    users see what they'd get before pressing "send".

    Not rate-limited (cheap read). Safe for all authed users.
    """
    user = await get_current_user(request)
    from services.digest_service import (
        collect_digest_data,
        get_user_watchlist_intel,
    )
    data = await collect_digest_data(db)
    wl_intel = await get_user_watchlist_intel(db, user.get("_id"))

    # Trim to a compact preview payload. We don't ship the full
    # HTML because the modal renders a native-React summary card
    # — faster paint, easier styling, no iframe sandbox quirks.
    predictions = data.get("predictions") or []
    smart_money = data.get("smart_money") or []
    alerts = data.get("alerts") or []

    def _pred_row(p: dict) -> dict:
        return {
            "ticker": p.get("ticker") or p.get("symbol"),
            "direction": p.get("direction"),
            "confidence": p.get("confidence"),
            "horizon": p.get("horizon") or p.get("time_horizon"),
        }

    def _sm_row(s: dict) -> dict:
        return {
            "ticker": s.get("ticker") or s.get("symbol"),
            "score": s.get("score") or s.get("smart_money_score"),
            "shift": s.get("shift") or s.get("score_shift"),
        }

    return {
        "content_summary": {
            "predictions": len(predictions),
            "smart_money": len(smart_money),
            "alerts": len(alerts),
            "has_overview": bool(data.get("overview")),
            "has_watchlist_intel": wl_intel is not None,
        },
        "preview": {
            "overview_headline": (data.get("overview") or {}).get("summary")
                or (data.get("overview") or {}).get("headline"),
            "top_predictions": [_pred_row(p) for p in predictions[:3]],
            "top_smart_money": [_sm_row(s) for s in smart_money[:3]],
            "alert_titles": [a.get("title") or a.get("subject") for a in alerts[:3]],
        },
        "email": user.get("email"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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

