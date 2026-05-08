"""Credit System Service — manages AI credits with tiered plan support.

Plans: Free (50cr), Starter $19 (3,000cr), Pro $55 (15,000cr), Pro Max $99 (50,000cr).
Pro/Pro Max: Unlimited AI Chat + War Room.
All plans: Advanced AI actions use credits.

Uses frozen dataclasses for plan config and pure functions for credit logic.
"""
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)

db: Any = None


# ══════════════════════════════════════════════════
#  PLAN & ACTION CONFIG (frozen dataclasses)
# ══════════════════════════════════════════════════

@dataclass(frozen=True)
class PlanRule:
    key: str
    monthly_credits: int
    topup_per_1000_usd: int
    unlimited_features: set[str] = field(default_factory=set)


PLAN_RULES: dict[str, PlanRule] = {
    "free": PlanRule(
        key="free",
        monthly_credits=50,
        topup_per_1000_usd=15,
        unlimited_features=set(),
    ),
    "starter": PlanRule(
        key="starter",
        monthly_credits=3000,
        topup_per_1000_usd=12,
        unlimited_features=set(),
    ),
    "pro": PlanRule(
        key="pro",
        monthly_credits=15000,
        topup_per_1000_usd=8,
        unlimited_features={"ai_chat", "war_room"},
    ),
    "pro_max": PlanRule(
        key="pro_max",
        monthly_credits=50000,
        topup_per_1000_usd=5,
        unlimited_features={"ai_chat", "war_room"},
    ),
}

ACTION_COSTS: dict[str, int] = {
    "ai_chat": 1,
    "war_room": 5,
    "ai_hypothesis": 3,
    "market_prediction": 3,
    "ai_intelligence": 2,
    "scanner_validation": 2,
    "api_call": 1,
    "web_search": 1,
    "web_research": 2,
}

# Map internal action keys used by endpoints to the canonical ACTION_COSTS keys
_ACTION_ALIAS = {
    "chat": "ai_chat",
    "hypothesis": "ai_hypothesis",
    "prediction": "market_prediction",
    "intelligence": "ai_intelligence",
    "scanner_validate": "scanner_validation",
}

TOPUP_TIERS: list[dict[str, Any]] = [
    {"id": "topup_1000", "credits": 1000, "label": "1,000 Credits"},
    {"id": "topup_2000", "credits": 2000, "label": "2,000 Credits"},
    {"id": "topup_5000", "credits": 5000, "label": "5,000 Credits"},
    {"id": "topup_10000", "credits": 10000, "label": "10,000 Credits"},
]


class CreditError(ValueError):
    pass


# ══════════════════════════════════════════════════
#  PURE FUNCTIONS (no DB, no side effects)
# ══════════════════════════════════════════════════

def get_plan_rule(plan_key: str) -> PlanRule:
    try:
        return PLAN_RULES[plan_key]
    except KeyError as exc:
        raise CreditError(f"Unknown plan: {plan_key}") from exc


def get_action_cost(action_key: str) -> int:
    canonical = _ACTION_ALIAS.get(action_key, action_key)
    try:
        return ACTION_COSTS[canonical]
    except KeyError as exc:
        raise CreditError(f"Unknown action: {action_key}") from exc


def is_unlimited(plan_key: str, action_key: str) -> bool:
    canonical = _ACTION_ALIAS.get(action_key, action_key)
    plan = get_plan_rule(plan_key)
    return canonical in plan.unlimited_features


def credits_required(plan_key: str, action_key: str) -> int:
    if is_unlimited(plan_key, action_key):
        return 0
    return get_action_cost(action_key)


def can_run_action(plan_key: str, action_key: str, wallet_balance: int) -> tuple[bool, int]:
    cost = credits_required(plan_key, action_key)
    if cost == 0:
        return True, 0
    return wallet_balance >= cost, cost


def charge_action(plan_key: str, action_key: str, wallet_balance: int) -> dict:
    allowed, cost = can_run_action(plan_key, action_key, wallet_balance)
    if not allowed:
        return {
            "ok": False,
            "error": "insufficient_credits",
            "action": action_key,
            "credits_required": cost,
            "credits_available": wallet_balance,
            "upgrade_options": ["starter", "pro", "buy_topup"],
        }

    new_balance = wallet_balance if cost == 0 else wallet_balance - cost
    return {
        "ok": True,
        "action": action_key,
        "plan": plan_key,
        "was_unlimited": cost == 0,
        "credits_charged": cost,
        "balance_before": wallet_balance,
        "balance_after": new_balance,
    }


def topup_price(plan_key: str, credits: int) -> float:
    if credits <= 0 or credits % 1000 != 0:
        raise CreditError("Top-ups must be sold in 1,000-credit increments")
    plan = get_plan_rule(plan_key)
    blocks = credits // 1000
    return blocks * plan.topup_per_1000_usd


# ══════════════════════════════════════════════════
#  DB-DEPENDENT FUNCTIONS
# ══════════════════════════════════════════════════

def set_db(database: Any) -> None:
    global db
    db = database


def _to_oid(user_id: str) -> Any:
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
    return "free"


def get_topup_packs(plan_key: str) -> list:
    """Get available top-up packs with plan-specific pricing."""
    packs = []
    for tier in TOPUP_TIERS:
        price = topup_price(plan_key, tier["credits"])
        plan = get_plan_rule(plan_key)
        packs.append({
            "id": tier["id"],
            "credits": tier["credits"],
            "label": tier["label"],
            "price": price,
            "per_credit": round(plan.topup_per_1000_usd / 1000, 4),
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

    bonus = PLAN_RULES["free"].monthly_credits
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


async def grant_custom_credits(user_id: str, amount: int, plan_key: str, reason: str) -> int:
    """Grant a custom credit amount on top of the current balance.

    Used by the beta-cohort redemption flow to hand out grants that
    don't map cleanly onto a standard plan (e.g. "Pro + 30k credits
    for the First 50"). The plan_key is still stamped on the wallet
    row so per-plan accounting and rate-limits treat the user as a
    Pro member.

    Idempotent by caller: the helper always increments, so callers
    must guard against double-granting (e.g. check a `*_granted_at`
    marker on the user doc first).
    """
    if db is None or amount <= 0:
        return 0

    await db.user_credits.update_one(
        {"user_id": user_id},
        {
            "$inc": {"credits": amount, "total_earned": amount},
            "$set": {
                "plan_key": plan_key,
                "last_grant_at": datetime.now(timezone.utc).isoformat(),
            },
            "$setOnInsert": {
                "total_spent": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        upsert=True,
    )
    await _log_event(user_id, "custom_grant", amount, False, reason)
    return amount



async def grant_plan_credits(user_id: str, plan_key: str) -> int:
    """Grant monthly plan credits on subscription activation/renewal."""
    if db is None:
        return 0

    plan = get_plan_rule(plan_key)
    credits = plan.monthly_credits

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

    await _log_event(user_id, "plan_credits", credits, False, f"{plan.key} monthly — {credits:,} credits")
    return credits


async def purchase_topup(user_id: str, topup_id: str, plan_key: str) -> dict:
    """Process a top-up credit purchase."""
    if db is None:
        return {"success": False, "error": "Database unavailable"}

    tier = next((t for t in TOPUP_TIERS if t["id"] == topup_id), None)
    if not tier:
        return {"success": False, "error": "Invalid top-up tier"}

    price = topup_price(plan_key, tier["credits"])
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
        "price_per_1000": get_plan_rule(plan_key).topup_per_1000_usd,
        "plan_key": plan_key,
        "purchased_at": datetime.now(timezone.utc).isoformat(),
    })

    balance = await get_balance(user_id)
    return {"success": True, "credits_added": credits, "price": price, "new_balance": balance["credits"]}


async def deduct_credits(user_id: str, action: str, plan_key: str) -> dict:
    """Deduct credits for an action using pure charge_action logic + DB persistence."""
    if db is None:
        return {"allowed": False, "cost": 0, "remaining": 0, "error": "Database unavailable"}

    # Get current balance
    doc = await db.user_credits.find_one({"user_id": user_id}, {"_id": 0})
    current = doc.get("credits", 0) if doc else 0

    # Run pure charge logic
    result = charge_action(plan_key, action, current)

    if not result["ok"]:
        return {
            "allowed": False,
            "cost": result["credits_required"],
            "remaining": result["credits_available"],
            "error": f"You need {result['credits_required']} credits but have {result['credits_available']}.",
            "upgrade_options": result.get("upgrade_options", []),
        }

    # Persist if credits were actually charged
    if result["credits_charged"] > 0:
        await db.user_credits.update_one(
            {"user_id": user_id},
            {"$inc": {"credits": -result["credits_charged"], "total_spent": result["credits_charged"]}},
        )

    await _log_event(
        user_id, action, -result["credits_charged"] if result["credits_charged"] > 0 else 0,
        result["was_unlimited"],
        f"{action} {'(unlimited)' if result['was_unlimited'] else ''}"
    )

    return {
        "allowed": True,
        "cost": result["credits_charged"],
        "remaining": result["balance_after"],
        "was_unlimited": result["was_unlimited"],
    }


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


async def _log_event(user_id: str, action: str, credits_charged: int, was_unlimited: bool, description: str) -> None:
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
