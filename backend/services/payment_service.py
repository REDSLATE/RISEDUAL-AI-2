import os
import stripe
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class StripeService:
    def __init__(self):
        self.api_key = os.environ.get('STRIPE_SECRET_KEY')
        if self.api_key:
            stripe.api_key = self.api_key
        self.price_id = os.environ.get('STRIPE_PRICE_ID')  # Yearly subscription price ID
        self.success_url = os.environ.get('STRIPE_SUCCESS_URL', 'https://yourdomain.com/success')
        self.cancel_url = os.environ.get('STRIPE_CANCEL_URL', 'https://yourdomain.com/cancel')
    
    def create_checkout_session(self, user_email: str, user_id: str) -> Optional[Dict]:
        """Create a Stripe Checkout Session for subscription"""
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': self.price_id,
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=self.success_url + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=self.cancel_url,
                customer_email=user_email,
                metadata={'user_id': user_id},
                subscription_data={
                    'metadata': {'user_id': user_id}
                }
            )
            return {'url': session.url, 'session_id': session.id}
        except Exception as e:
            logger.error(f"Stripe checkout error: {str(e)}")
            return None
    
    def create_customer(self, email: str, user_id: str) -> Optional[str]:
        """Create a Stripe customer"""
        try:
            customer = stripe.Customer.create(
                email=email,
                metadata={'user_id': user_id}
            )
            return customer.id
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            return None
    
    def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel a subscription"""
        try:
            stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
            return True
        except Exception as e:
            logger.error(f"Error cancelling subscription: {str(e)}")
            return False
    
    def get_subscription(self, subscription_id: str) -> Optional[Dict]:
        """Get subscription details"""
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            return subscription
        except Exception as e:
            logger.error(f"Error retrieving subscription: {str(e)}")
            return None

class PayPalService:
    def __init__(self):
        self.client_id = os.environ.get('PAYPAL_CLIENT_ID')
        self.client_secret = os.environ.get('PAYPAL_CLIENT_SECRET')
        self.mode = os.environ.get('PAYPAL_MODE', 'sandbox')  # 'sandbox' or 'live'
        self.base_url = 'https://api-m.sandbox.paypal.com' if self.mode == 'sandbox' else 'https://api-m.paypal.com'
    
    def get_access_token(self) -> Optional[str]:
        """Get PayPal OAuth token"""
        import requests
        import base64
        
        try:
            auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            headers = {
                'Authorization': f'Basic {auth}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            response = requests.post(
                f'{self.base_url}/v1/oauth2/token',
                headers=headers,
                data='grant_type=client_credentials'
            )
            return response.json().get('access_token')
        except Exception as e:
            logger.error(f"PayPal auth error: {str(e)}")
            return None
    
    def create_subscription(self, user_email: str, user_id: str) -> Optional[Dict]:
        """Create PayPal subscription"""
        import requests
        
        try:
            access_token = self.get_access_token()
            if not access_token:
                return None
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # Create subscription
            plan_id = os.environ.get('PAYPAL_PLAN_ID')  # Yearly plan ID
            data = {
                'plan_id': plan_id,
                'subscriber': {
                    'email_address': user_email
                },
                'application_context': {
                    'brand_name': 'RISEDUALAI',
                    'return_url': os.environ.get('PAYPAL_RETURN_URL', 'https://yourdomain.com/success'),
                    'cancel_url': os.environ.get('PAYPAL_CANCEL_URL', 'https://yourdomain.com/cancel'),
                    'user_action': 'SUBSCRIBE_NOW'
                },
                'custom_id': user_id
            }
            
            response = requests.post(
                f'{self.base_url}/v1/billing/subscriptions',
                headers=headers,
                json=data
            )
            
            result = response.json()
            approval_url = next((link['href'] for link in result.get('links', []) if link['rel'] == 'approve'), None)
            
            return {
                'subscription_id': result.get('id'),
                'approval_url': approval_url
            }
        except Exception as e:
            logger.error(f"PayPal subscription error: {str(e)}")
            return None
    
    def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel PayPal subscription"""
        import requests
        
        try:
            access_token = self.get_access_token()
            if not access_token:
                return False
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                f'{self.base_url}/v1/billing/subscriptions/{subscription_id}/cancel',
                headers=headers,
                json={'reason': 'User requested cancellation'}
            )
            
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Error cancelling PayPal subscription: {str(e)}")
            return False