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

SUBSCRIPTION_PRICE = 45.00  # $45/month - server-side defined, never from frontend

class StripePaymentService:
    def __init__(self):
        self.api_key = os.environ.get('STRIPE_API_KEY')
        self.price_id = os.environ.get('STRIPE_PRICE_ID')

    def _get_checkout(self, webhook_url: str) -> StripeCheckout:
        return StripeCheckout(api_key=self.api_key, webhook_url=webhook_url)

    async def create_checkout_session(self, origin_url: str, webhook_url: str, metadata: dict = None) -> CheckoutSessionResponse:
        checkout = self._get_checkout(webhook_url)
        success_url = f"{origin_url}?payment_status=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{origin_url}?payment_status=cancelled"

        request = CheckoutSessionRequest(
            amount=45.00,
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
