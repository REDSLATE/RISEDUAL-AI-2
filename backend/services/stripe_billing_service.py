"""Stripe Billing Service — handles subscription and credit top-up checkout flows.

Uses Stripe Checkout Sessions for subscriptions and top-ups.
Webhooks grant credits and update subscription status.
All state persisted to MongoDB (not in-memory).
"""
import os
import logging
import stripe
from datetime import datetime, timezone
from fastapi import HTTPException

logger = logging.getLogger(__name__)

db = None

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

PLAN_PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", "price_starter_monthly"),
    "pro": os.environ.get("STRIPE_PRICE_PRO", "price_pro_monthly"),
    "pro_max": os.environ.get("STRIPE_PRICE_PRO_MAX", "price_pro_max_monthly"),
}

TOPUP_PRICE_IDS = {
    "topup_1000": os.environ.get("STRIPE_PRICE_TOPUP_1000", "price_topup_1000"),
    "topup_2000": os.environ.get("STRIPE_PRICE_TOPUP_2000", "price_topup_2000"),
    "topup_5000": os.environ.get("STRIPE_PRICE_TOPUP_5000", "price_topup_5000"),
    "topup_10000": os.environ.get("STRIPE_PRICE_TOPUP_10000", "price_topup_10000"),
}

MONTHLY_CREDITS = {"free": 50, "starter": 3000, "pro": 15000, "pro_max": 50000}
TOPUP_CREDITS = {"topup_1000": 1000, "topup_2000": 2000, "topup_5000": 5000, "topup_10000": 10000}


def set_db(database):
    global db
    db = database


def _init_stripe():
    if STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY


def _get_app_base_url() -> str:
    return os.environ.get("APP_BASE_URL", os.environ.get("FRONTEND_URL", "http://localhost:3000"))


async def get_or_create_customer(user_id: str, email: str) -> dict:
    """Get or create a Stripe customer record in MongoDB."""
    if db is None:
        return {}

    doc = await db.billing_customers.find_one({"user_id": user_id}, {"_id": 0})
    if doc and doc.get("stripe_customer_id"):
        return doc

    # Create in Stripe
    _init_stripe()
    try:
        customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
        stripe_id = customer.id
    except Exception as e:
        logger.error(f"Stripe customer creation failed: {e}")
        stripe_id = None

    record = {
        "user_id": user_id,
        "email": email,
        "stripe_customer_id": stripe_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.billing_customers.update_one(
        {"user_id": user_id},
        {"$set": record},
        upsert=True,
    )
    return record


async def create_subscription_checkout(user_id: str, email: str, plan: str) -> dict:
    """Create a Stripe Checkout session for a subscription plan."""
    price_id = PLAN_PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid plan")

    _init_stripe()
    customer = await get_or_create_customer(user_id, email)
    customer_id = customer.get("stripe_customer_id")

    if not customer_id:
        raise HTTPException(status_code=500, detail="Could not create Stripe customer")

    base_url = _get_app_base_url()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=user_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base_url}?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}?payment=cancel",
        metadata={"user_id": user_id, "kind": "subscription", "plan": plan},
    )
    return {"checkout_url": session.url, "session_id": session.id}


async def create_topup_checkout(user_id: str, email: str, pack: str) -> dict:
    """Create a Stripe Checkout session for a credit top-up."""
    price_id = TOPUP_PRICE_IDS.get(pack)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid top-up pack")

    _init_stripe()
    customer = await get_or_create_customer(user_id, email)
    customer_id = customer.get("stripe_customer_id")

    if not customer_id:
        raise HTTPException(status_code=500, detail="Could not create Stripe customer")

    base_url = _get_app_base_url()
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        client_reference_id=user_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base_url}?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}?payment=cancel",
        metadata={"user_id": user_id, "kind": "topup", "pack": pack},
    )
    return {"checkout_url": session.url, "session_id": session.id}


async def create_customer_portal(user_id: str) -> dict:
    """Create a Stripe Customer Portal session."""
    if db is None:
        raise HTTPException(status_code=500, detail="Database unavailable")

    doc = await db.billing_customers.find_one({"user_id": user_id}, {"_id": 0})
    if not doc or not doc.get("stripe_customer_id"):
        raise HTTPException(status_code=404, detail="No billing account found")

    _init_stripe()
    base_url = _get_app_base_url()
    session = stripe.billing_portal.Session.create(
        customer=doc["stripe_customer_id"],
        return_url=base_url,
    )
    return {"url": session.url}


async def process_webhook(payload: bytes, signature: str) -> dict:
    """Process a Stripe webhook event. Grants credits and updates subscriptions."""
    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature")

    _init_stripe()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=signature, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {exc}")

    if db is None:
        return {"ok": False, "error": "Database unavailable"}

    # Deduplicate
    existing = await db.billing_webhooks.find_one({"event_id": event.id})
    if existing:
        return {"ok": True, "duplicate": True, "event_id": event.id}

    etype = event["type"]
    obj = event["data"]["object"]

    try:
        if etype == "checkout.session.completed":
            await _handle_checkout_completed(obj)
        elif etype == "invoice.paid":
            await _handle_invoice_paid(obj)
        elif etype in {"customer.subscription.updated", "customer.subscription.created"}:
            await _handle_subscription_update(obj)
        elif etype == "customer.subscription.deleted":
            await _handle_subscription_deleted(obj)
        elif etype == "invoice.payment_failed":
            await _handle_payment_failed(obj)
    except Exception as e:
        logger.exception(f"Webhook handler error for {etype}: {e}")

    # Mark processed
    await db.billing_webhooks.insert_one({
        "event_id": event.id,
        "type": etype,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })

    return {"ok": True, "event_id": event.id, "type": etype}


async def _handle_checkout_completed(obj: dict):
    user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
    meta = obj.get("metadata") or {}
    kind = meta.get("kind")

    if kind == "topup" and user_id:
        pack = meta.get("pack")
        if pack in TOPUP_CREDITS:
            from services.credit_service import purchase_topup, get_user_plan
            user = await db.users.find_one({"_id": _to_oid(user_id)})
            plan_key = get_user_plan(user) if user else "free"
            await purchase_topup(user_id, pack, plan_key)
            logger.info(f"Stripe topup: {pack} for {user_id}")

    elif kind == "subscription" and user_id:
        plan = meta.get("plan")
        if plan in MONTHLY_CREDITS:
            await _activate_subscription(user_id, plan, obj.get("subscription"))


async def _handle_invoice_paid(obj: dict):
    customer_id = obj.get("customer")
    lines = obj.get("lines", {}).get("data", [])
    plan = None
    for line in lines:
        price_id = ((line.get("pricing") or {}).get("price_details") or {}).get("price") or (line.get("price") or {}).get("id")
        for key, value in PLAN_PRICE_IDS.items():
            if value == price_id:
                plan = key
                break

    if customer_id and plan:
        user_id = await _find_user_by_customer(customer_id)
        if user_id:
            await _activate_subscription(user_id, plan, obj.get("subscription"))
            from services.credit_service import grant_plan_credits
            await grant_plan_credits(user_id, plan)
            logger.info(f"Stripe invoice.paid: {plan} credits for {user_id}")


async def _handle_subscription_update(obj: dict):
    customer_id = obj.get("customer")
    items = (obj.get("items") or {}).get("data") or [{}]
    price_id = (items[0].get("price") or {}).get("id") if items else None
    plan = next((k for k, v in PLAN_PRICE_IDS.items() if v == price_id), None)

    if customer_id and plan:
        user_id = await _find_user_by_customer(customer_id)
        if user_id:
            await _set_user_plan(user_id, plan, obj.get("status", "active"), obj.get("id"))


async def _handle_subscription_deleted(obj: dict):
    customer_id = obj.get("customer")
    if customer_id:
        user_id = await _find_user_by_customer(customer_id)
        if user_id:
            await _set_user_plan(user_id, "free", "canceled", obj.get("id"))


async def _handle_payment_failed(obj: dict):
    customer_id = obj.get("customer")
    if customer_id:
        user_id = await _find_user_by_customer(customer_id)
        if user_id:
            await db.users.update_one(
                {"_id": _to_oid(user_id)},
                {"$set": {"subscription_status": "past_due"}}
            )


async def _activate_subscription(user_id: str, plan: str, stripe_sub_id: str = None):
    """Activate a subscription: update user plan + grant monthly credits."""
    await _set_user_plan(user_id, plan, "active", stripe_sub_id)
    from services.credit_service import grant_plan_credits
    await grant_plan_credits(user_id, plan)


async def _set_user_plan(user_id: str, plan: str, status: str, stripe_sub_id: str = None):
    """Update user's subscription status in MongoDB."""
    if db is None:
        return
    await db.users.update_one(
        {"_id": _to_oid(user_id)},
        {"$set": {
            "subscription_status": plan,
            "stripe_subscription_id": stripe_sub_id,
            "subscription_updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )


async def _find_user_by_customer(stripe_customer_id: str) -> str:
    """Find user_id by Stripe customer ID."""
    if db is None:
        return None
    doc = await db.billing_customers.find_one(
        {"stripe_customer_id": stripe_customer_id},
        {"_id": 0, "user_id": 1}
    )
    return doc["user_id"] if doc else None


def _to_oid(user_id: str):
    from bson import ObjectId
    try:
        return ObjectId(user_id)
    except Exception:
        return user_id
