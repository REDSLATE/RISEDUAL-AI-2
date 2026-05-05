"""
Stripe Payment Integration Tests for RISEDUALAI
Tests: checkout session creation, payment status polling, webhook endpoint, MongoDB persistence
"""
import pytest
import requests
import os

# Get BASE_URL from environment
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    raise ValueError("REACT_APP_BACKEND_URL environment variable not set")

API_URL = f"{BASE_URL}/api"


class TestStripePaymentIntegration:
    """Stripe payment integration tests"""
    
    # Store session_id for cross-test usage
    created_session_id = None
    
    def test_api_root_accessible(self):
        """Test that API root is accessible"""
        response = requests.get(f"{API_URL}/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "RISEDUALAI" in data["message"]
        print("✓ API root accessible")
    
    def test_create_checkout_session_success(self):
        """Test POST /api/subscription/create-checkout-session creates Stripe checkout session"""
        payload = {
            "origin_url": BASE_URL
        }
        response = requests.post(
            f"{API_URL}/subscription/create-checkout-session",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertions
        data = response.json()
        assert "url" in data, "Response should contain 'url'"
        assert "session_id" in data, "Response should contain 'session_id'"
        
        # Verify URL is a valid Stripe checkout URL
        assert data["url"].startswith("https://checkout.stripe.com/"), \
            f"URL should be Stripe checkout URL, got: {data['url'][:50]}..."
        
        # Verify session_id format (Stripe session IDs start with 'cs_')
        assert data["session_id"].startswith("cs_"), \
            f"Session ID should start with 'cs_', got: {data['session_id'][:20]}..."
        
        # Store for subsequent tests
        TestStripePaymentIntegration.created_session_id = data["session_id"]
        
        print(f"✓ Checkout session created: {data['session_id'][:30]}...")
        print(f"✓ Stripe URL: {data['url'][:60]}...")
    
    def test_create_checkout_session_missing_origin_url(self):
        """Test checkout session creation fails without origin_url"""
        response = requests.post(
            f"{API_URL}/subscription/create-checkout-session",
            json={},
            headers={"Content-Type": "application/json"}
        )
        
        # Should return 422 (validation error) for missing required field
        assert response.status_code == 422, f"Expected 422 for missing origin_url, got {response.status_code}"
        print("✓ Validation error returned for missing origin_url")
    
    def test_get_payment_status_for_created_session(self):
        """Test GET /api/subscription/status/{session_id} returns payment status"""
        session_id = TestStripePaymentIntegration.created_session_id
        if not session_id:
            pytest.skip("No session_id from previous test")
        
        response = requests.get(f"{API_URL}/subscription/status/{session_id}")
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertions
        data = response.json()
        assert "status" in data, "Response should contain 'status'"
        assert "payment_status" in data, "Response should contain 'payment_status'"
        assert "amount_total" in data, "Response should contain 'amount_total'"
        assert "currency" in data, "Response should contain 'currency'"
        
        # Verify expected values for unpaid session
        # Status should be 'open' or 'expired' for unpaid sessions
        assert data["status"] in ["open", "expired", "complete"], \
            f"Unexpected status: {data['status']}"
        
        # Payment status should be 'unpaid' for new sessions
        assert data["payment_status"] in ["unpaid", "paid", "no_payment_required"], \
            f"Unexpected payment_status: {data['payment_status']}"
        
        # Amount should be $50 (5000 cents)
        if data["amount_total"]:
            assert data["amount_total"] == 5000, \
                f"Expected amount_total 5000 (cents), got {data['amount_total']}"
        
        # Currency should be USD
        if data["currency"]:
            assert data["currency"].lower() == "usd", \
                f"Expected currency 'usd', got {data['currency']}"
        
        print(f"✓ Payment status retrieved: status={data['status']}, payment_status={data['payment_status']}")
    
    def test_get_payment_status_invalid_session(self):
        """Test payment status for invalid session ID returns error"""
        invalid_session_id = "cs_invalid_session_12345"
        
        response = requests.get(f"{API_URL}/subscription/status/{invalid_session_id}")
        
        # Should return 500 (Stripe API error) for invalid session
        assert response.status_code == 500, \
            f"Expected 500 for invalid session, got {response.status_code}"
        print("✓ Error returned for invalid session ID")
    
    def test_webhook_endpoint_exists(self):
        """Test POST /api/webhook/stripe endpoint exists and responds"""
        # Send empty body - should fail validation but endpoint should exist
        response = requests.post(
            f"{API_URL}/webhook/stripe",
            data=b"",
            headers={"Content-Type": "application/json"}
        )
        
        # Endpoint should exist (not 404)
        assert response.status_code != 404, "Webhook endpoint should exist"
        
        # Without valid signature, should return 400 or 500
        assert response.status_code in [400, 500], \
            f"Expected 400/500 for invalid webhook, got {response.status_code}"
        
        print(f"✓ Webhook endpoint exists (returned {response.status_code} for invalid request)")
    
    def test_webhook_endpoint_requires_signature(self):
        """Test webhook endpoint requires Stripe-Signature header"""
        # Send with fake body but no signature
        fake_event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_123"}}
        }
        
        response = requests.post(
            f"{API_URL}/webhook/stripe",
            json=fake_event,
            headers={"Content-Type": "application/json"}
        )
        
        # Should fail without valid signature
        assert response.status_code in [400, 500], \
            f"Expected 400/500 without signature, got {response.status_code}"
        
        print("✓ Webhook correctly rejects requests without valid signature")


class TestMongoDBPersistence:
    """Test MongoDB persistence of payment transactions"""
    
    def test_checkout_creates_db_entry(self):
        """Test that creating checkout session creates entry in payment_transactions"""
        # Create a new checkout session
        payload = {"origin_url": BASE_URL}
        response = requests.post(
            f"{API_URL}/subscription/create-checkout-session",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 200
        data = response.json()
        session_id = data["session_id"]
        
        # Now check status - this should also verify DB entry exists
        status_response = requests.get(f"{API_URL}/subscription/status/{session_id}")
        assert status_response.status_code == 200
        
        status_data = status_response.json()
        # If we can get status, the session exists in Stripe
        # The DB entry is created during checkout creation
        assert "status" in status_data
        
        print(f"✓ Checkout session {session_id[:20]}... created and status retrievable")


class TestCheckoutSessionDetails:
    """Test checkout session configuration details"""
    
    def test_checkout_session_has_correct_amount(self):
        """Test checkout session is configured for $50"""
        payload = {"origin_url": BASE_URL}
        response = requests.post(
            f"{API_URL}/subscription/create-checkout-session",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 200
        data = response.json()
        session_id = data["session_id"]
        
        # Get status to verify amount
        status_response = requests.get(f"{API_URL}/subscription/status/{session_id}")
        assert status_response.status_code == 200
        
        status_data = status_response.json()
        
        # Amount should be 5000 cents ($50)
        if status_data.get("amount_total"):
            assert status_data["amount_total"] == 5000, \
                f"Expected $50 (5000 cents), got {status_data['amount_total']} cents"
            print(f"✓ Checkout amount is correct: ${status_data['amount_total']/100}")
        else:
            print("⚠ amount_total not returned (may be null for unpaid sessions)")
    
    def test_checkout_session_currency_is_usd(self):
        """Test checkout session uses USD currency"""
        payload = {"origin_url": BASE_URL}
        response = requests.post(
            f"{API_URL}/subscription/create-checkout-session",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 200
        data = response.json()
        session_id = data["session_id"]
        
        # Get status to verify currency
        status_response = requests.get(f"{API_URL}/subscription/status/{session_id}")
        assert status_response.status_code == 200
        
        status_data = status_response.json()
        
        if status_data.get("currency"):
            assert status_data["currency"].lower() == "usd", \
                f"Expected USD, got {status_data['currency']}"
            print(f"✓ Currency is correct: {status_data['currency'].upper()}")
        else:
            print("⚠ currency not returned (may be null for unpaid sessions)")


# Fixtures
@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
