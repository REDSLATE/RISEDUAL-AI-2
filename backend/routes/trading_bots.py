"""Trading Bot Routes — Grid Bot, Signal Bot, TradingView Webhook Bot."""
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bots", tags=["trading-bots"])

_db = None

def set_db(database):
    global _db
    _db = database
    from services.trading_bot_service import set_db as set_svc_db
    set_svc_db(database)


class CreateBotRequest(BaseModel):
    type: str  # grid, signal, webhook
    name: str = ""
    mode: str = "paper"
    broker: str = "public"   # "public" | "moomoo"
    config: Optional[dict] = None


class ToggleBotRequest(BaseModel):
    enabled: bool


class UpdateBrokerRequest(BaseModel):
    broker: str  # "public" | "moomoo"


@router.post("")
async def create_bot(request: Request, bot: CreateBotRequest):
    """Create a new trading bot (starts OFF by default)."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    if bot.mode == "live" and user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Live bots restricted to authorized accounts")

    # Integrity mitigation gate — when a data-integrity alert has
    # tripped a BLOCK_NEW_BOTS self-defense, reject creation with
    # 423 Locked and surface the source rule + TTL so the operator
    # knows WHY. Existing bots keep running; this only blocks NEW
    # commitments of capital while the data is suspect.
    try:
        from services.integrity_mitigation_service import (
            are_new_bots_blocked_by_integrity,
        )
        blocked, detail = await are_new_bots_blocked_by_integrity(_db)
        if blocked:
            raise HTTPException(
                status_code=423,
                detail={
                    "error_code": "integrity_block_new_bots",
                    "message": "New bots temporarily blocked by an active "
                               "data-integrity mitigation. Existing bots "
                               "continue to run.",
                    **(detail or {}),
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        # Never block a legitimate request on a mitigation-lookup
        # glitch — the Mongo audit trail is the source of truth,
        # a transient read failure is less harmful than a false 423.
        logger.warning("[integrity_mitigation] bot-create gate lookup failed: %s", e)

    from services.trading_bot_service import create_bot
    result = await create_bot(user_id, bot.model_dump())
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("")
async def list_bots(request: Request):
    """List all bots for the current user."""
    user = await get_current_user(request)
    from services.trading_bot_service import get_user_bots
    return await get_user_bots(user["_id"])


@router.patch("/{bot_id}/toggle")
async def toggle_bot(bot_id: str, request: Request, body: ToggleBotRequest):
    """Toggle a bot on/off."""
    user = await get_current_user(request)
    # Same BLOCK_NEW_BOTS gate as create_bot — re-enabling a dormant
    # bot is functionally a new capital commitment. Toggling OFF
    # (enabled=false) is always allowed so operators can safely
    # pause a bot during an incident.
    if body.enabled:
        try:
            from services.integrity_mitigation_service import (
                are_new_bots_blocked_by_integrity,
            )
            blocked, detail = await are_new_bots_blocked_by_integrity(_db)
            if blocked:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "error_code": "integrity_block_new_bots",
                        "message": "Re-enabling bots is temporarily blocked "
                                   "by an active data-integrity mitigation.",
                        **(detail or {}),
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("[integrity_mitigation] bot-toggle gate lookup failed: %s", e)

    from services.trading_bot_service import toggle_bot
    result = await toggle_bot(user["_id"], bot_id, body.enabled)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/{bot_id}/config")
async def update_config(bot_id: str, request: Request):
    """Update bot configuration."""
    user = await get_current_user(request)
    body = await request.json()
    from services.trading_bot_service import update_bot_config
    result = await update_bot_config(user["_id"], bot_id, body)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/{bot_id}/broker")
async def update_bot_broker(bot_id: str, request: Request, body: UpdateBrokerRequest):
    """Switch the broker adapter for a bot ("public" | "moomoo").

    Enforced at the write layer so the bot doc's ``broker`` field is
    the sole source of truth for ``broker_router.resolve_broker``.
    """
    user = await get_current_user(request)
    broker = (body.broker or "").strip().lower()
    if broker not in ("public", "moomoo"):
        raise HTTPException(status_code=400, detail="Broker must be 'public' or 'moomoo'.")
    from bson import ObjectId
    from datetime import datetime, timezone
    try:
        _id = ObjectId(bot_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid bot_id")
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    res = await _db.trading_bots.update_one(
        {"_id": _id, "user_id": user_id},
        {"$set": {"broker": broker, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Bot not found")
    return {"bot_id": bot_id, "broker": broker}


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str, request: Request):
    """Delete a bot."""
    user = await get_current_user(request)
    from services.trading_bot_service import delete_bot
    result = await delete_bot(user["_id"], bot_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/webhook/{bot_id}/{webhook_secret}")
async def receive_webhook(bot_id: str, webhook_secret: str, request: Request):
    """TradingView webhook receiver — no auth required, uses webhook secret.
    
    Expected payload: {"action": "buy"|"sell", "symbol": "AAPL", "qty": 10}
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Find bot by ID and verify secret
    from bson import ObjectId
    bot = await _db.trading_bots.find_one({"_id": ObjectId(bot_id), "type": "webhook"})
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    from services.trading_bot_service import process_webhook
    result = await process_webhook(bot["user_id"], bot_id, webhook_secret, payload)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/signal/process")
async def process_signal(request: Request):
    """Manually trigger signal bot processing for a validated signal."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    body = await request.json()
    from services.trading_bot_service import process_signal_for_bots
    results = await process_signal_for_bots(user_id, body)
    return {"executions": results, "count": len(results)}
