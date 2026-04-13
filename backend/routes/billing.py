"""Billing routes — Stripe checkout, portal, webhooks, and billing state."""
import logging
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional

from services.auth_helpers import get_current_user
from services import stripe_billing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])
db = None


def set_db(database):
    global db
    db = database
    stripe_billing_service.set_db(database)


@router.post("/checkout/subscription")
async def checkout_subscription(request: Request):
    """Create Stripe Checkout for a subscription plan."""
    user = await get_current_user(request)
    body = await request.json()
    plan = body.get("plan")
    if plan not in ("starter", "pro", "pro_max"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose starter, pro, or pro_max.")
    result = await stripe_billing_service.create_subscription_checkout(
        str(user["_id"]), user["email"], plan
    )
    return result


@router.post("/checkout/topup")
async def checkout_topup(request: Request):
    """Create Stripe Checkout for a credit top-up."""
    user = await get_current_user(request)
    body = await request.json()
    pack = body.get("pack") or body.get("topup_id")
    if not pack:
        raise HTTPException(status_code=400, detail="pack required")
    result = await stripe_billing_service.create_topup_checkout(
        str(user["_id"]), user["email"], pack
    )
    return result


@router.post("/portal")
async def billing_portal(request: Request):
    """Open Stripe Customer Portal for managing subscription."""
    user = await get_current_user(request)
    result = await stripe_billing_service.create_customer_portal(str(user["_id"]))
    return result


@router.post("/webhook")
async def billing_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(default=None, alias="Stripe-Signature"),
):
    """Stripe webhook endpoint — verifies signature and processes events."""
    payload = await request.body()
    return await stripe_billing_service.process_webhook(payload, stripe_signature)


@router.get("/me")
async def billing_me(request: Request):
    """Get current user's billing state."""
    user = await get_current_user(request)
    user_id = str(user["_id"])

    customer = await db.billing_customers.find_one({"user_id": user_id}, {"_id": 0}) if db is not None else None

    from services.credit_service import get_balance, get_user_plan
    balance = await get_balance(user_id)
    plan_key = get_user_plan(user)

    return {
        "user_id": user_id,
        "plan_key": plan_key,
        "subscription_status": user.get("subscription_status", "free"),
        "stripe_customer_id": customer.get("stripe_customer_id") if customer else None,
        "credits": balance,
    }
