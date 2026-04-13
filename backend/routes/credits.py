"""Credit routes — balance, top-up purchase, usage history, plan info."""
import logging
from fastapi import APIRouter, Request, HTTPException

from services.auth_helpers import get_current_user
from services import credit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credits", tags=["credits"])
db = None


def set_db(database):
    global db
    db = database
    credit_service.set_db(database)


@router.get("/balance")
async def get_balance(request: Request):
    """Get current credit balance and plan info."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    plan_key = credit_service.get_user_plan(user)
    plan = credit_service.PLAN_RULES.get(plan_key, credit_service.PLAN_RULES["free"])
    balance = await credit_service.get_balance(user_id)

    return {
        **balance,
        "plan_key": plan_key,
        "plan_label": plan["label"],
        "monthly_credits": plan["monthly_credits"],
        "unlimited_features": list(plan["unlimited_features"]),
        "topup_rate": plan["topup_per_1000"],
    }


@router.get("/plans")
async def get_plans():
    """Get all plan details for pricing display."""
    plans = []
    for key, plan in credit_service.PLAN_RULES.items():
        plans.append({
            "key": key,
            "label": plan["label"],
            "price": plan["price"],
            "monthly_credits": plan["monthly_credits"],
            "topup_per_1000": plan["topup_per_1000"],
            "unlimited_features": list(plan["unlimited_features"]),
        })
    return {"plans": plans}


@router.get("/topups")
async def get_topups(request: Request):
    """Get available top-up packs with plan-specific pricing."""
    user = await get_current_user(request)
    plan_key = credit_service.get_user_plan(user)
    packs = credit_service.get_topup_packs(plan_key)
    return {"plan_key": plan_key, "topups": packs}


@router.get("/costs")
async def get_costs(request: Request):
    """Get credit costs per action for the user's plan."""
    try:
        user = await get_current_user(request)
        plan_key = credit_service.get_user_plan(user)
    except Exception:
        plan_key = "free"

    costs = {}
    for action, base_cost in credit_service.ACTION_COSTS.items():
        cost = credit_service.get_action_cost(plan_key, action)
        costs[action] = {"cost": cost, "unlimited": cost == 0}

    return {"plan_key": plan_key, "costs": costs}


@router.post("/purchase")
async def purchase_topup(request: Request):
    """Purchase a credit top-up."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    plan_key = credit_service.get_user_plan(user)
    body = await request.json()
    topup_id = body.get("topup_id") or body.get("pack_id")
    if not topup_id:
        raise HTTPException(status_code=400, detail="topup_id required")

    result = await credit_service.purchase_topup(user_id, topup_id, plan_key)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Purchase failed"))
    return result


@router.get("/history")
async def get_history(request: Request, limit: int = 30):
    """Get credit usage event history."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    events = await credit_service.get_usage_events(user_id, limit)
    return {"events": events}


@router.get("/matrix")
async def get_pricing_matrix():
    """Get the full pricing matrix for public display."""
    actions = {}
    for action, base_cost in credit_service.ACTION_COSTS.items():
        actions[action] = {}
        for plan_key in credit_service.PLAN_RULES:
            cost = credit_service.get_action_cost(plan_key, action)
            actions[action][plan_key] = "Unlimited" if cost == 0 else f"{cost} credits"

    topups = {}
    for plan_key, plan in credit_service.PLAN_RULES.items():
        topups[plan_key] = {
            "included": plan["monthly_credits"],
            "topup_per_1000": plan["topup_per_1000"],
        }

    return {"actions": actions, "topups": topups}
