"""Credit System Service — manages AI credits with tiered plan support.

Plans: Free (50cr), Starter $19 (3,000cr), Pro $55 (15,000cr), Pro Max $99 (50,000cr).
Pro/Pro Max: Unlimited AI Chat + War Room.
All plans: Advanced AI actions use credits.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

db = None

# ══════════════════════════════════════════════════
#  PLAN CONFIGURATION (single source of truth)
# ══════════════════════════════════════════════════

PLAN_RULES = {
    "free": {
        "monthly_credits": 50,
        "topup_per_1000": 15.00,
        "unlimited_features": set(),
        "price": 0,
        "label": "Free",
    },
    "starter": {
        "monthly_credits": 3000,
        "topup_per_1000": 12.00,
        "unlimited_features": set(),
        "price": 19,
        "label": "Starter",
    },
    "pro": {
        "monthly_credits": 15000,
        "topup_per_1000": 8.00,
        "unlimited_features": {"chat", "war_room"},
        "price": 55,
        "label": "Pro",
    },
    "pro_max": {
        "monthly_credits": 50000,
        "topup_per_1000": 5.00,
        "unlimited_features": {"chat", "war_room"},
        "price": 99,
        "label": "Pro Max",
    },
}

ACTION_COSTS = {
    "chat": 1,
    "war_room": 5,
    "hypothesis": 3,
    "prediction": 3,
    "intelligence": 2,
    "scanner_validate": 2,
    "api_call": 1,
}

# Top-up packs (price calculated per-plan)
TOPUP_TIERS = [
    {"id": "topup_500", "credits": 500, "label": "500 Credits"},
    {"id": "topup_1000", "credits": 1000, "label": "1,000 Credits"},
    {"id": "topup_2500", "credits": 2500, "label": "2,500 Credits"},
    {"id": "topup_5000", "credits": 5000, "label": "5,000 Credits"},
]


def set_db(database):
    global db
    db = database


def _to_oid(user_id: str):
    from bson import ObjectId
    try:
        return ObjectId(user_id)
    except Exception:
        return user_id


def get_user_plan(user: dict) -> str:
    """Determine user's plan key from their subscription_status or role."""
    if user.get("role") in ("admin", "owner"):
        return "pro_max"
    status = user.get("subscription_status", "free")
    if status in PLAN_RULES:
        return status
    if status == "pro":
        return "pro"
    return "free"


def get_action_cost(plan_key: str, action_key: str) -> int:
    """Get credit cost for an action under a plan. Returns 0 if unlimited."""
    plan = PLAN_RULES.get(plan_key, PLAN_RULES["free"])
    if action_key in plan["unlimited_features"]:
        return 0
    return ACTION_COSTS.get(action_key, 1)


def get_topup_packs(plan_key: str) -> list:
    """Get available top-up packs with plan-specific pricing."""
    plan = PLAN_RULES.get(plan_key, PLAN_RULES["free"])
    rate = plan["topup_per_1000"]
    packs = []
    for tier in TOPUP_TIERS:
        price = round((tier["credits"] / 1000) * rate, 2)
        packs.append({
            "id": tier["id"],
            "credits": tier["credits"],
            "label": tier["label"],
            "price": price,
            "per_credit": round(rate / 1000, 4),
        })
    return packs


async def get_balance(user_id: str) -> dict:
    """Get user's current credit balance."""
    if db is None:
        return {"credits": 0, "total_earned": 0, "total_spent": 0}

    doc = await db.user_credits.find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return {"credits": 0, "total_earned": 0, "total_spent": 0}

    return {
        "credits": doc.get("credits", 0),
        "total_earned": doc.get("total_earned", 0),
        "total_spent": doc.get("total_spent", 0),
    }


async def grant_signup_bonus(user_id: str) -> int:
    """Grant signup bonus (Free plan monthly credits). Returns credits granted."""
    if db is None:
        return 0

    existing = await db.user_credits.find_one({"user_id": user_id})
    if existing:
        return 0

    bonus = PLAN_RULES["free"]["monthly_credits"]
    await db.user_credits.insert_one({
        "user_id": user_id,
        "credits": bonus,
        "total_earned": bonus,
        "total_spent": 0,
        "plan_key": "free",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    await _log_event(user_id, "signup_bonus", bonus, False, "Signup bonus — 50 free credits")
    return bonus


async def grant_plan_credits(user_id: str, plan_key: str) -> int:
    """Grant monthly plan credits on subscription activation/renewal."""
    if db is None:
        return 0

    plan = PLAN_RULES.get(plan_key, PLAN_RULES["free"])
    credits = plan["monthly_credits"]

    await db.user_credits.update_one(
        {"user_id": user_id},
        {
            "$inc": {"credits": credits, "total_earned": credits},
            "$set": {
                "plan_key": plan_key,
                "last_renewal": datetime.now(timezone.utc).isoformat(),
            },
        },
        upsert=True,
    )

    await _log_event(user_id, "plan_credits", credits, False, f"{plan['label']} monthly — {credits:,} credits")
    return credits


async def purchase_topup(user_id: str, topup_id: str, plan_key: str) -> dict:
    """Process a top-up credit purchase."""
    if db is None:
        return {"success": False, "error": "Database unavailable"}

    tier = next((t for t in TOPUP_TIERS if t["id"] == topup_id), None)
    if not tier:
        return {"success": False, "error": "Invalid top-up tier"}

    plan = PLAN_RULES.get(plan_key, PLAN_RULES["free"])
    price = round((tier["credits"] / 1000) * plan["topup_per_1000"], 2)
    credits = tier["credits"]

    await db.user_credits.update_one(
        {"user_id": user_id},
        {"$inc": {"credits": credits, "total_earned": credits}},
        upsert=True,
    )

    await _log_event(user_id, "topup_purchase", credits, False, f"Purchased {tier['label']} (${price})")

    await db.credit_purchases.insert_one({
        "user_id": user_id,
        "topup_id": topup_id,
        "credits": credits,
        "price": price,
        "price_per_1000": plan["topup_per_1000"],
        "plan_key": plan_key,
        "purchased_at": datetime.now(timezone.utc).isoformat(),
    })

    balance = await get_balance(user_id)
    return {"success": True, "credits_added": credits, "price": price, "new_balance": balance["credits"]}


async def deduct_credits(user_id: str, action: str, plan_key: str) -> dict:
    """Deduct credits for an action. Returns {allowed, cost, remaining, was_unlimited}."""
    cost = get_action_cost(plan_key, action)

    if cost == 0:
        await _log_event(user_id, action, 0, True, f"{action} (unlimited)")
        return {"allowed": True, "cost": 0, "remaining": -1, "was_unlimited": True}

    if db is None:
        return {"allowed": False, "cost": cost, "remaining": 0, "error": "Database unavailable"}

    doc = await db.user_credits.find_one({"user_id": user_id}, {"_id": 0})
    current = doc.get("credits", 0) if doc else 0

    if current < cost:
        upgrades = []
        if plan_key == "free":
            upgrades = ["starter", "pro", "buy_topup"]
        elif plan_key == "starter":
            upgrades = ["pro", "buy_topup"]
        elif plan_key == "pro":
            upgrades = ["pro_max", "buy_topup"]
        else:
            upgrades = ["buy_topup"]

        return {
            "allowed": False,
            "cost": cost,
            "remaining": current,
            "error": f"You need {cost} credits but have {current}.",
            "upgrade_options": upgrades,
        }

    await db.user_credits.update_one(
        {"user_id": user_id},
        {"$inc": {"credits": -cost, "total_spent": cost}},
    )

    await _log_event(user_id, action, -cost, False, f"Used {action}")
    return {"allowed": True, "cost": cost, "remaining": current - cost, "was_unlimited": False}


async def get_usage_events(user_id: str, limit: int = 30) -> list:
    """Get recent usage/transaction events."""
    if db is None:
        return []

    cursor = db.credit_events.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)

    events = []
    async for e in cursor:
        events.append(e)
    return events


async def _log_event(user_id: str, action: str, credits_charged: int, was_unlimited: bool, description: str):
    """Log a usage event."""
    if db is None:
        return
    await db.credit_events.insert_one({
        "user_id": user_id,
        "action": action,
        "credits_charged": credits_charged,
        "was_unlimited": was_unlimited,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
