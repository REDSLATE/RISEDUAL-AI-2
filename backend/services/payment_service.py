import os
import logging
from dotenv import load_dotenv
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CheckoutStatusResponse
)

load_dotenv()
logger = logging.getLogger(__name__)

SUBSCRIPTION_PRICE_MONTHLY = 55.00  # $55/month  (legacy default — Pro monthly)
SUBSCRIPTION_PRICE_ANNUAL = 594.00  # $49.50/month billed annually (10% off)

# Tiered pricing that matches the landing-page plan cards.
# Legacy callers using plan="monthly"/"annual" keep working via the
# SUBSCRIPTION_PRICE_* constants above; new callers should pass `tier`.
TIER_PRICE_MAP = {
    "pro":     55.00,   # Pro monthly
    "pro_max": 99.00,   # Pro Max monthly
}


class StripePaymentService:
    def __init__(self) -> None:
        self.api_key = os.environ.get('STRIPE_API_KEY')
        self.price_id = os.environ.get('STRIPE_PRICE_ID')

    def _get_checkout(self, webhook_url: str) -> StripeCheckout:
        return StripeCheckout(api_key=self.api_key, webhook_url=webhook_url)

    async def create_checkout_session(
        self,
        origin_url: str,
        webhook_url: str,
        metadata: dict = None,
        plan: str = "monthly",
        tier: str = None,
    ) -> CheckoutSessionResponse:
        checkout = self._get_checkout(webhook_url)
        success_url = f"{origin_url}?payment_status=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{origin_url}?payment_status=cancelled"

        # New tier argument wins when supplied; legacy plan argument is the
        # fallback so old callers (email links, older frontends) still work.
        if tier in TIER_PRICE_MAP:
            amount = TIER_PRICE_MAP[tier]
        else:
            amount = SUBSCRIPTION_PRICE_ANNUAL if plan == "annual" else SUBSCRIPTION_PRICE_MONTHLY

        request = CheckoutSessionRequest(
            amount=amount,
            currency="usd",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata or {}
        )
        return await checkout.create_checkout_session(request)

    async def get_checkout_status(self, session_id: str, webhook_url: str) -> CheckoutStatusResponse:
        checkout = self._get_checkout(webhook_url)
        return await checkout.get_checkout_status(session_id)

    async def handle_webhook(self, body: bytes, signature: str, webhook_url: str):
        checkout = self._get_checkout(webhook_url)
        return await checkout.handle_webhook(body, signature)
