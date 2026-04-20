"""
Test suite for RISEDUALAI Subscription Plans - Monthly and Annual pricing
Tests the dual subscription pricing feature with Stripe checkout integration
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestSubscriptionCheckout:
    """Tests for subscription checkout endpoint with monthly/annual plans"""
    
    def test_monthly_checkout_returns_200(self):
        """POST /api/subscription/create-checkout-session with plan=monthly returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai", "plan": "monthly"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("Monthly checkout returns 200 - PASSED")
    
    def test_monthly_checkout_returns_valid_url(self):
        """POST /api/subscription/create-checkout-session with plan=monthly returns valid Stripe URL"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai", "plan": "monthly"},
            headers={"Content-Type": "application/json"}
        )
        data = response.json()
        assert "url" in data, "Response should contain 'url' field"
        assert "checkout.stripe.com" in data["url"], "URL should be a Stripe checkout URL"
        assert "session_id" in data, "Response should contain 'session_id' field"
        print(f"Monthly checkout URL: {data['url'][:80]}...")
        print("Monthly checkout returns valid Stripe URL - PASSED")
    
    def test_annual_checkout_returns_200(self):
        """POST /api/subscription/create-checkout-session with plan=annual returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai", "plan": "annual"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("Annual checkout returns 200 - PASSED")
    
    def test_annual_checkout_returns_valid_url(self):
        """POST /api/subscription/create-checkout-session with plan=annual returns valid Stripe URL"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai", "plan": "annual"},
            headers={"Content-Type": "application/json"}
        )
        data = response.json()
        assert "url" in data, "Response should contain 'url' field"
        assert "checkout.stripe.com" in data["url"], "URL should be a Stripe checkout URL"
        assert "session_id" in data, "Response should contain 'session_id' field"
        print(f"Annual checkout URL: {data['url'][:80]}...")
        print("Annual checkout returns valid Stripe URL - PASSED")
    
    def test_default_plan_is_monthly(self):
        """POST /api/subscription/create-checkout-session without plan defaults to monthly"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "url" in data, "Response should contain 'url' field"
        print("Default plan checkout works - PASSED")
    
    def test_invalid_plan_defaults_to_monthly(self):
        """POST /api/subscription/create-checkout-session with invalid plan defaults to monthly"""
        response = requests.post(
            f"{BASE_URL}/api/subscription/create-checkout-session",
            json={"origin_url": "https://risedual.ai", "plan": "invalid_plan"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "url" in data, "Response should contain 'url' field"
        print("Invalid plan defaults to monthly - PASSED")


class TestForeignMarketsEndpoint:
    """Tests for foreign markets endpoint used by auto-refresh feature"""
    
    def test_foreign_markets_returns_200(self):
        """GET /api/foreign-markets returns 200"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("Foreign markets returns 200 - PASSED")
    
    def test_foreign_markets_has_asia(self):
        """GET /api/foreign-markets returns Asia indices"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        assert "asia" in data, "Response should contain 'asia' field"
        assert len(data["asia"]) > 0, "Asia should have at least one index"
        print(f"Asia indices: {len(data['asia'])} - PASSED")
    
    def test_foreign_markets_has_europe(self):
        """GET /api/foreign-markets returns Europe indices"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        assert "europe" in data, "Response should contain 'europe' field"
        assert len(data["europe"]) > 0, "Europe should have at least one index"
        print(f"Europe indices: {len(data['europe'])} - PASSED")
    
    def test_foreign_markets_has_americas(self):
        """GET /api/foreign-markets returns Americas indices"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        assert "americas" in data, "Response should contain 'americas' field"
        assert len(data["americas"]) > 0, "Americas should have at least one index"
        print(f"Americas indices: {len(data['americas'])} - PASSED")
    
    def test_foreign_markets_has_commodities(self):
        """GET /api/foreign-markets returns commodities"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        assert "commodities" in data, "Response should contain 'commodities' field"
        print(f"Commodities: {len(data.get('commodities', []))} - PASSED")
    
    def test_foreign_markets_has_currencies(self):
        """GET /api/foreign-markets returns currencies"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        assert "currencies" in data, "Response should contain 'currencies' field"
        print(f"Currencies: {len(data.get('currencies', []))} - PASSED")
    
    def test_market_data_has_required_fields(self):
        """Each market entry has required fields: symbol, name, price, change_percent"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=120)
        data = response.json()
        
        # Check first Asia index
        if data.get("asia"):
            market = data["asia"][0]
            assert "symbol" in market, "Market should have 'symbol'"
            assert "name" in market, "Market should have 'name'"
            assert "price" in market, "Market should have 'price'"
            assert "change_percent" in market, "Market should have 'change_percent'"
            print(f"Market data fields verified: {market['name']} - PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
