from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid

class Subscription(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_email: str
    plan_type: str = 'yearly'  # 'yearly', 'monthly'
    status: str = 'active'  # 'active', 'cancelled', 'expired', 'past_due'
    payment_provider: str  # 'stripe', 'paypal'
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    paypal_subscription_id: Optional[str] = None
    current_period_start: datetime = Field(default_factory=datetime.utcnow)
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class PaymentIntent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    amount: float
    currency: str = 'usd'
    status: str  # 'pending', 'succeeded', 'failed'
    payment_provider: str
    provider_payment_id: str
    metadata: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)