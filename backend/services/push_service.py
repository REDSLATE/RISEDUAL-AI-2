"""Push notification service using Web Push (VAPID)."""
import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pywebpush import webpush, WebPushException

logger = logging.getLogger(__name__)

VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_CLAIMS = {"sub": "mailto:noreply@risedual.ai"}

FREE_DAILY_LIMIT = 1

# Notification types
NOTIF_PREDICTION_FLIP = "prediction_flip"
NOTIF_DARK_POOL_SPIKE = "dark_pool_spike"
NOTIF_WATCHLIST_ALERT = "watchlist_alert"
NOTIF_MARKET_SIGNAL = "market_signal"


def _is_configured():
    return bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)


async def send_push(subscription_info: dict, title: str, body: str, url: str = "/", tag: str = "risedual", db=None, user_id: str = None, is_pro: bool = True) -> bool:
    """Send a push notification to a single subscription."""
    if not _is_configured():
        logger.info(f"Push skipped (no VAPID keys): {title}")
        return False

    # Free user rate limit check
    if not is_pro and db and user_id:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = await db.push_log.count_documents({
            "user_id": user_id,
            "date": today,
        })
        if count >= FREE_DAILY_LIMIT:
            logger.debug(f"Push skipped (free limit): {user_id}")
            return False

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
        "icon": "/logo192.png",
        "badge": "/logo192.png",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        # Log for rate limiting
        if db and user_id:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await db.push_log.insert_one({
                "user_id": user_id,
                "date": today,
                "title": title,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })
        return True
    except WebPushException as e:
        logger.error(f"Push send failed: {e}")
        # If subscription expired (410 Gone), clean it up
        if hasattr(e, 'response') and e.response and e.response.status_code == 410:
            if db:
                await db.push_subscriptions.delete_one({"endpoint": subscription_info.get("endpoint")})
                logger.info("Removed expired push subscription")
        return False
    except Exception as e:
        logger.error(f"Push error: {e}")
        return False


async def broadcast_notification(db, title: str, body: str, url: str = "/", tag: str = "risedual", notif_type: str = "general"):
    """Send push notification to all subscribed users."""
    if not _is_configured():
        logger.info(f"Push broadcast skipped (no VAPID keys): {title}")
        return {"sent": 0, "skipped": True}

    cursor = db.push_subscriptions.find({})
    sent = 0
    errors = 0

    async for sub_doc in cursor:
        user_id = sub_doc.get("user_id", "")
        is_pro = sub_doc.get("is_pro", False)
        subscription = sub_doc.get("subscription", {})

        success = await send_push(
            subscription_info=subscription,
            title=title,
            body=body,
            url=url,
            tag=tag,
            db=db,
            user_id=user_id,
            is_pro=is_pro,
        )
        if success:
            sent += 1
        else:
            errors += 1

    logger.info(f"Push broadcast '{notif_type}': {sent} sent, {errors} errors")
    return {"sent": sent, "errors": errors, "skipped": False}


async def notify_prediction_flip(db, ticker: str, old_verdict: str, new_verdict: str, confidence: int):
    """Trigger push for AI prediction verdict change."""
    emoji = "📈" if "BULL" in new_verdict.upper() else "📉" if "BEAR" in new_verdict.upper() else "➡️"
    await broadcast_notification(
        db,
        title=f"{emoji} {ticker} Prediction Flipped",
        body=f"{old_verdict} → {new_verdict} ({confidence}% confidence)",
        url="/#predictions",
        tag=f"prediction-{ticker}",
        notif_type=NOTIF_PREDICTION_FLIP,
    )


async def notify_dark_pool_spike(db, ticker: str, volume: str, sentiment: str):
    """Trigger push for dark pool volume spike."""
    await broadcast_notification(
        db,
        title=f"Dark Pool Alert: {ticker}",
        body=f"Unusual volume: {volume} — Sentiment: {sentiment}",
        url="/#dark-pool",
        tag=f"darkpool-{ticker}",
        notif_type=NOTIF_DARK_POOL_SPIKE,
    )


async def notify_watchlist_alert(db, user_id: str, ticker: str, change_pct: float, price: float):
    """Trigger push for watchlist price movement >5%."""
    direction = "up" if change_pct > 0 else "down"
    sub = await db.push_subscriptions.find_one({"user_id": user_id})
    if not sub:
        return
    await send_push(
        subscription_info=sub["subscription"],
        title=f"{ticker} moved {direction} {abs(change_pct):.1f}%",
        body=f"Current price: ${price:.2f}",
        url="/#watchlist",
        tag=f"watchlist-{ticker}",
        db=db,
        user_id=user_id,
        is_pro=sub.get("is_pro", False),
    )


async def notify_market_signal(db, signal_type: str, ticker: str, strength: str):
    """Trigger push for new market signal."""
    await broadcast_notification(
        db,
        title=f"New Signal: {ticker}",
        body=f"{signal_type} — Strength: {strength}",
        url="/#signals",
        tag=f"signal-{ticker}",
        notif_type=NOTIF_MARKET_SIGNAL,
    )
