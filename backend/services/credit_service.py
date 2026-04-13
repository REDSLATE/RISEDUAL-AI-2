"""Credit System Service — manages AI credits for all users.

Signup: 50 free credits. Buy credit packs or go Pro for 5,000/month.
Pro perks: Chat + War Room are FREE (unlimited), other AI costs credits.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

db = None

SIGNUP_BONUS = 50

# Credit costs per action
CREDIT_COSTS = {
    "chat": 1,
    "war_room": 5,
    "hypothesis": 3,
    "prediction": 3,
    "intelligence": 2,  # AI Score, Patterns, Brief
    "scanner_validate": 2,
    "api_call": 1,
}

# Pro users get these actions FREE (unlimited)
PRO_FREE_ACTIONS = {"chat", "war_room"}

# Credit packs available for purchase
CREDIT_PACKS = [
    {"id": "starter", "name": "Starter", "credits": 100, "price": 5.00},
    {"id": "explorer", "name": "Explorer", "credits": 500, "price": 20.00},
    {"id": "power", "name": "Power", "credits": 1500, "price": 45.00},
    {"id": "pro_topup", "name": "Pro Top-Up", "credits": 2000, "price": 15.00, "pro_only": True},
]

PRO_MONTHLY_CREDITS = 5000


def set_db(database):
    global db
    db = database


async def get_balance(user_id: str) -> dict:
    """Get user's current credit balance."""
    if db is None:
        return {"credits": 0}

    doc = await db.user_credits.find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return {"credits": 0, "total_earned": 0, "total_spent": 0}

    return {
        "credits": doc.get("credits", 0),
        "total_earned": doc.get("total_earned", 0),
        "total_spent": doc.get("total_spent", 0),
    }


async def grant_signup_bonus(user_id: str) -> int:
    """Grant signup bonus credits. Returns credits granted (0 if already granted)."""
    if db is None:
        return 0

    existing = await db.user_credits.find_one({"user_id": user_id})
    if existing:
        return 0  # Already has a record — no double bonus

    await db.user_credits.insert_one({
        "user_id": user_id,
        "credits": SIGNUP_BONUS,
        "total_earned": SIGNUP_BONUS,
        "total_spent": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    await _log_transaction(user_id, SIGNUP_BONUS, "signup_bonus", "Signup bonus")
    logger.info(f"Granted {SIGNUP_BONUS} signup credits to {user_id}")
    return SIGNUP_BONUS


async def grant_pro_monthly(user_id: str) -> int:
    """Grant monthly Pro credits (5,000). Called on Pro subscription activation/renewal."""
    if db is None:
        return 0

    await db.user_credits.update_one(
        {"user_id": user_id},
        {
            "$inc": {"credits": PRO_MONTHLY_CREDITS, "total_earned": PRO_MONTHLY_CREDITS},
            "$set": {"last_pro_grant": datetime.now(timezone.utc).isoformat()},
        },
        upsert=True,
    )

    await _log_transaction(user_id, PRO_MONTHLY_CREDITS, "pro_monthly", "Pro monthly credits")
    return PRO_MONTHLY_CREDITS


async def purchase_credits(user_id: str, pack_id: str) -> dict:
    """Process a credit pack purchase. Returns result dict."""
    if db is None:
        return {"success": False, "error": "Database unavailable"}

    pack = next((p for p in CREDIT_PACKS if p["id"] == pack_id), None)
    if not pack:
        return {"success": False, "error": "Invalid pack"}

    # Check pro_only packs
    if pack.get("pro_only"):
        user = await db.users.find_one({"_id": _to_oid(user_id)})
        if not user or user.get("subscription_status") != "pro":
            return {"success": False, "error": "This pack is for Pro subscribers only"}

    credits = pack["credits"]

    await db.user_credits.update_one(
        {"user_id": user_id},
        {"$inc": {"credits": credits, "total_earned": credits}},
        upsert=True,
    )

    await _log_transaction(user_id, credits, "purchase", f"Purchased {pack['name']} pack")

    # Record purchase
    await db.credit_purchases.insert_one({
        "user_id": user_id,
        "pack_id": pack_id,
        "credits": credits,
        "price": pack["price"],
        "purchased_at": datetime.now(timezone.utc).isoformat(),
    })

    balance = await get_balance(user_id)
    return {"success": True, "credits_added": credits, "new_balance": balance["credits"]}


async def deduct_credits(user_id: str, action: str, is_pro: bool = False) -> dict:
    """Deduct credits for an action. Returns {allowed, cost, remaining}.

    Pro users get chat and war_room FREE.
    """
    cost = CREDIT_COSTS.get(action, 1)

    # Pro free actions
    if is_pro and action in PRO_FREE_ACTIONS:
        return {"allowed": True, "cost": 0, "remaining": -1, "pro_free": True}

    if db is None:
        return {"allowed": False, "cost": cost, "remaining": 0, "error": "Database unavailable"}

    doc = await db.user_credits.find_one({"user_id": user_id}, {"_id": 0})
    current = doc.get("credits", 0) if doc else 0

    if current < cost:
        return {
            "allowed": False,
            "cost": cost,
            "remaining": current,
            "error": f"Not enough credits. Need {cost}, have {current}.",
        }

    # Deduct
    await db.user_credits.update_one(
        {"user_id": user_id},
        {"$inc": {"credits": -cost, "total_spent": cost}},
    )

    await _log_transaction(user_id, -cost, action, f"Used {action}")

    return {"allowed": True, "cost": cost, "remaining": current - cost}


async def get_transaction_history(user_id: str, limit: int = 30) -> list:
    """Get recent credit transactions."""
    if db is None:
        return []

    cursor = db.credit_transactions.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("timestamp", -1).limit(limit)

    txns = []
    async for t in cursor:
        txns.append(t)
    return txns


async def _log_transaction(user_id: str, amount: int, action: str, description: str):
    """Log a credit transaction."""
    if db is None:
        return
    await db.credit_transactions.insert_one({
        "user_id": user_id,
        "amount": amount,
        "action": action,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _to_oid(user_id: str):
    from bson import ObjectId
    try:
        return ObjectId(user_id)
    except Exception:
        return user_id
