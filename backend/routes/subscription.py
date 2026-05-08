"""Subscription & payment routes: Stripe checkout, webhooks, status."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import logging
from datetime import datetime, timezone

from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api")

# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


class CheckoutRequest(BaseModel):
    origin_url: str
    plan: str = "monthly"         # legacy: "monthly" | "annual"
    tier: str | None = None       # new: "pro" | "pro_max" (takes precedence when set)


@router.get("/subscription/plans")
async def get_plans():
    """Return available subscription plans."""
    return {
        "plans": [
            {
                "id": "monthly",
                "name": "Pro Monthly",
                "price": 55.00,
                "currency": "usd",
                "interval": "month",
                "features": [
                    "Unlimited AI Chat & Hypothesis",
                    "All 3 AI Models + Consensus Mode",
                    "Dark Pool & Congress Data (unblurred)",
                    "Unlimited Watchlist & Journal",
                    "PDF Export & Portfolio Analyzer",
                    "Market Signals & Priority Support",
                ],
            },
            {
                "id": "annual",
                "name": "Pro Annual",
                "price": 594.00,
                "currency": "usd",
                "interval": "year",
                "savings": "Save $66/year",
                "features": [
                    "Everything in Pro Monthly",
                    "2 months free ($66 savings)",
                ],
            },
        ]
    }


@router.post("/subscription/create-checkout-session")
async def create_checkout_session(request: CheckoutRequest, http_request: Request):
    try:
        from services.payment_service import StripePaymentService, TIER_PRICE_MAP
        payment_service = StripePaymentService()
        host_url = str(http_request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        # Resolve the tier (new flow) first; fall back to the legacy plan.
        tier = request.tier if request.tier in TIER_PRICE_MAP else None
        plan = request.plan if request.plan in ("monthly", "annual") else "monthly"

        if tier:
            amount = TIER_PRICE_MAP[tier]
            plan_label = f"risedualai_{tier}"
        else:
            amount = 594.00 if plan == "annual" else 55.00
            plan_label = f"risedualai_pro_{plan}"

        metadata = {"plan": plan_label, "source": "web_checkout"}

        session = await payment_service.create_checkout_session(
            origin_url=request.origin_url,
            webhook_url=webhook_url,
            metadata=metadata,
            plan=plan,
            tier=tier,
        )

        await db.payment_transactions.insert_one({
            "session_id": session.session_id,
            "amount": amount,
            "currency": "usd",
            "plan": plan_label,
            "metadata": metadata,
            "payment_status": "initiated",
            "created_at": datetime.now(timezone.utc).isoformat()
        })

        return {"url": session.url, "session_id": session.session_id}
    except Exception as e:
        logging.error(f"Error creating checkout session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subscription/status/{session_id}")
async def get_payment_status(session_id: str, http_request: Request):
    try:
        from services.payment_service import StripePaymentService
        payment_service = StripePaymentService()
        host_url = str(http_request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        status = await payment_service.get_checkout_status(session_id, webhook_url)

        existing = await db.payment_transactions.find_one({"session_id": session_id})
        if existing and existing.get("payment_status") != status.payment_status:
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "payment_status": status.payment_status,
                    "status": status.status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )

        return {
            "status": status.status,
            "payment_status": status.payment_status,
            "amount_total": status.amount_total,
            "currency": status.currency
        }
    except Exception as e:
        logging.error(f"Error fetching payment status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    try:
        from services.payment_service import StripePaymentService
        payment_service = StripePaymentService()
        body = await request.body()
        signature = request.headers.get("Stripe-Signature", "")
        host_url = str(request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        webhook_response = await payment_service.handle_webhook(body, signature, webhook_url)

        if webhook_response and webhook_response.session_id:
            await db.payment_transactions.update_one(
                {"session_id": webhook_response.session_id},
                {"$set": {
                    "payment_status": webhook_response.payment_status,
                    "event_type": webhook_response.event_type,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )

        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/user/subscription")
async def update_subscription(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    status = body.get("status", "pro")
    plan = body.get("plan", "monthly")
    from bson import ObjectId
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {
            "subscription_status": status,
            "subscription_plan": plan,
            "subscription_updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    # If upgrading to pro, check for referral reward
    if status == "pro":
        try:
            from routes.referral import complete_referral_reward
            await complete_referral_reward(user["_id"])
        except Exception as e:
            logging.warning(f"Referral reward processing error: {e}")

    return {"message": "Subscription updated", "status": status}
