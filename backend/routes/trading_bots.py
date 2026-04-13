"""Trading Bot Routes — Grid Bot, Signal Bot, TradingView Webhook Bot."""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List
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
    config: Optional[Dict] = None


class ToggleBotRequest(BaseModel):
    enabled: bool


@router.post("")
async def create_bot(request: Request, bot: CreateBotRequest):
    """Create a new trading bot (starts OFF by default)."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    if bot.mode == "live" and user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Live bots restricted to authorized accounts")

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
