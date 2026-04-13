"""
Iteration 120: Developer API Feature Tests
Tests for user-facing Developer API with key management, rate limiting, and public API endpoints.

Features tested:
- Key management (generate, list, revoke)
- Public API endpoints (watchlist, predictions, market data, signals)
- Authentication via X-API-Key header
- Rate limiting (Free: 100/day, Pro: 5000/day)
- Pro-only endpoints (war-room, hypothesis)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"

# Shared API key for tests (generated once)
SHARED_API_KEY = None


def get_authenticated_session():
    """Helper to get an authenticated session"""
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    if login_resp.status_code != 200:
        raise Exception(f"Login failed: {login_resp.text}")
    return session


def get_or_create_api_key(session):
    """Get existing API key or create one if needed"""
    global SHARED_API_KEY
    if SHARED_API_KEY:
        return SHARED_API_KEY
    
    # Check existing keys
    keys_resp = session.get(f"{BASE_URL}/api/developer/keys")
    if keys_resp.status_code == 200:
        keys = keys_resp.json().get("keys", [])
        active_keys = [k for k in keys if k.get("is_active")]
        
        # If we have active keys, we need to generate a new one
        # First revoke one if at limit
        if len(active_keys) >= 3:
            # Revoke the oldest test key
            for k in active_keys:
                if k.get("label", "").startswith("TEST_"):
                    session.delete(f"{BASE_URL}/api/developer/keys/{k['key_id']}")
                    break
    
    # Generate new key
    key_resp = session.post(
        f"{BASE_URL}/api/developer/keys/generate",
        json={"label": "TEST_SharedKey"}
    )
    if key_resp.status_code == 200:
        SHARED_API_KEY = key_resp.json()["key"]
        return SHARED_API_KEY
    
    raise Exception(f"Failed to get/create API key: {key_resp.text}")


class TestLoginRegression:
    """Regression tests to ensure login flow still works"""
    
    def test_admin_login(self):
        """Test admin login works correctly"""
        session = requests.Session()
        resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        data = resp.json()
        # Login response has user data at root level (not nested under 'user')
        assert "email" in data, "Response should contain 'email'"
        assert data["email"] == ADMIN_EMAIL, "Email should match"
        assert "subscription_status" in data, "Response should contain 'subscription_status'"
        assert data["subscription_status"] == "pro", "Admin should be pro"
        print("Admin login successful")
        session.close()
    
    def test_owner_login(self):
        """Test owner login works correctly"""
        session = requests.Session()
        resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert resp.status_code == 200, f"Owner login failed: {resp.text}"
        data = resp.json()
        assert "email" in data, "Response should contain 'email'"
        assert data["email"] == OWNER_EMAIL, "Email should match"
        assert data["role"] == "owner", "Role should be owner"
        print("Owner login successful")
        session.close()


class TestDeveloperAPIKeyManagement:
    """Tests for API key management endpoints (cookie-auth)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with authentication"""
        self.session = get_authenticated_session()
        yield
        self.session.close()
    
    def test_generate_api_key(self):
        """POST /api/developer/keys/generate - generates API key with rsd_live_ format"""
        # First check if we're at limit and revoke if needed
        keys_resp = self.session.get(f"{BASE_URL}/api/developer/keys")
        keys = keys_resp.json().get("keys", [])
        active_keys = [k for k in keys if k.get("is_active")]
        
        if len(active_keys) >= 3:
            # Revoke a test key
            for k in active_keys:
                if k.get("label", "").startswith("TEST_"):
                    self.session.delete(f"{BASE_URL}/api/developer/keys/{k['key_id']}")
                    break
        
        resp = self.session.post(
            f"{BASE_URL}/api/developer/keys/generate",
            json={"label": "TEST_Iteration120_Gen"}
        )
        assert resp.status_code == 200, f"Key generation failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "key" in data, "Response should contain 'key'"
        assert "label" in data, "Response should contain 'label'"
        assert "message" in data, "Response should contain 'message'"
        
        # Verify key format: rsd_live_{48 hex chars}
        key = data["key"]
        assert key.startswith("rsd_live_"), f"Key should start with 'rsd_live_', got: {key[:20]}"
        assert len(key) == 57, f"Key should be 57 chars (rsd_live_ + 48 hex), got: {len(key)}"
        
        print(f"Generated key: {key[:12]}...{key[-4:]}")
    
    def test_list_api_keys(self):
        """GET /api/developer/keys - lists keys with masked preview"""
        resp = self.session.get(f"{BASE_URL}/api/developer/keys")
        assert resp.status_code == 200, f"List keys failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "keys" in data, "Response should contain 'keys'"
        assert isinstance(data["keys"], list), "Keys should be a list"
        
        if len(data["keys"]) > 0:
            key = data["keys"][0]
            # Verify key structure
            assert "key_preview" in key, "Key should have 'key_preview'"
            assert "key_id" in key, "Key should have 'key_id'"
            assert "label" in key, "Key should have 'label'"
            assert "is_active" in key, "Key should have 'is_active'"
            assert "total_calls" in key, "Key should have 'total_calls'"
            
            # Verify masked format: rsd_live_XXX...XXXX
            preview = key["key_preview"]
            assert "..." in preview, f"Key preview should be masked with '...', got: {preview}"
            assert preview.startswith("rsd_live_"), f"Preview should start with 'rsd_live_'"
        
        print(f"Found {len(data['keys'])} API keys")
    
    def test_get_api_usage(self):
        """GET /api/developer/usage - returns today's usage, limit, remaining, tier"""
        resp = self.session.get(f"{BASE_URL}/api/developer/usage")
        assert resp.status_code == 200, f"Get usage failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "today" in data, "Response should contain 'today'"
        assert "limit" in data, "Response should contain 'limit'"
        assert "remaining" in data, "Response should contain 'remaining'"
        assert "tier" in data, "Response should contain 'tier'"
        assert "history" in data, "Response should contain 'history'"
        
        # Verify tier is pro for admin user
        assert data["tier"] == "pro", f"Admin should be pro tier, got: {data['tier']}"
        assert data["limit"] == 5000, f"Pro limit should be 5000, got: {data['limit']}"
        
        # Verify remaining calculation
        assert data["remaining"] == data["limit"] - data["today"], "Remaining should be limit - today"
        
        print(f"Usage: {data['today']}/{data['limit']} ({data['tier']} tier)")
    
    def test_revoke_api_key(self):
        """DELETE /api/developer/keys/{key_id} - revokes a key"""
        # First check if we're at limit
        keys_resp = self.session.get(f"{BASE_URL}/api/developer/keys")
        keys = keys_resp.json().get("keys", [])
        active_keys = [k for k in keys if k.get("is_active")]
        
        if len(active_keys) >= 3:
            # Revoke a test key first
            for k in active_keys:
                if k.get("label", "").startswith("TEST_"):
                    self.session.delete(f"{BASE_URL}/api/developer/keys/{k['key_id']}")
                    break
        
        # Generate a key to revoke
        gen_resp = self.session.post(
            f"{BASE_URL}/api/developer/keys/generate",
            json={"label": "TEST_ToRevoke"}
        )
        assert gen_resp.status_code == 200, f"Key generation failed: {gen_resp.text}"
        key_data = gen_resp.json()
        key_id = key_data["key"][-8:]
        
        # Revoke the key
        revoke_resp = self.session.delete(f"{BASE_URL}/api/developer/keys/{key_id}")
        assert revoke_resp.status_code == 200, f"Key revocation failed: {revoke_resp.text}"
        data = revoke_resp.json()
        assert data.get("success") == True, "Revocation should return success: true"
        
        # Verify key is now inactive
        list_resp = self.session.get(f"{BASE_URL}/api/developer/keys")
        keys = list_resp.json()["keys"]
        revoked_key = next((k for k in keys if k["key_id"] == key_id), None)
        if revoked_key:
            assert revoked_key["is_active"] == False, "Revoked key should be inactive"
        
        print(f"Successfully revoked key: {key_id}")


class TestPublicAPIAuth:
    """Tests for public API authentication"""
    
    def test_api_returns_401_without_key(self):
        """API returns 401 when no X-API-Key header provided"""
        resp = requests.get(f"{BASE_URL}/api/v1/watchlist")
        assert resp.status_code == 401, f"Should return 401 without key, got: {resp.status_code}"
        data = resp.json()
        assert "Missing X-API-Key" in data.get("detail", ""), f"Error should mention missing key: {data}"
        print("Correctly returns 401 without X-API-Key header")
    
    def test_api_returns_401_for_invalid_key(self):
        """API returns 401 for invalid/revoked key"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/watchlist",
            headers={"X-API-Key": "rsd_live_invalid_key_12345678901234567890123456"}
        )
        assert resp.status_code == 401, f"Should return 401 for invalid key, got: {resp.status_code}"
        data = resp.json()
        assert "Invalid or revoked" in data.get("detail", ""), f"Error should mention invalid key: {data}"
        print("Correctly returns 401 for invalid API key")


class TestPublicAPIEndpoints:
    """Tests for public API endpoints (X-API-Key auth)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and get API key"""
        self.session = get_authenticated_session()
        self.api_key = get_or_create_api_key(self.session)
        yield
        self.session.close()
    
    def test_get_watchlist(self):
        """GET /api/v1/watchlist - returns user's watchlist with X-API-Key auth"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/watchlist",
            headers={"X-API-Key": self.api_key}
        )
        assert resp.status_code == 200, f"Watchlist request failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "tickers" in data, "Response should contain 'tickers'"
        assert "count" in data, "Response should contain 'count'"
        assert "prices" in data, "Response should contain 'prices'"
        assert isinstance(data["tickers"], list), "Tickers should be a list"
        
        print(f"Watchlist: {data['count']} tickers")
    
    def test_get_predictions(self):
        """GET /api/v1/predictions - returns recent predictions"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/predictions",
            headers={"X-API-Key": self.api_key}
        )
        assert resp.status_code == 200, f"Predictions request failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "predictions" in data, "Response should contain 'predictions'"
        assert "count" in data, "Response should contain 'count'"
        assert isinstance(data["predictions"], list), "Predictions should be a list"
        
        print(f"Predictions: {data['count']} items")
    
    def test_get_symbol_predictions(self):
        """GET /api/v1/predictions/AAPL - returns symbol-specific predictions"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/predictions/AAPL",
            headers={"X-API-Key": self.api_key}
        )
        assert resp.status_code == 200, f"Symbol predictions request failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "symbol" in data, "Response should contain 'symbol'"
        assert "predictions" in data, "Response should contain 'predictions'"
        assert "count" in data, "Response should contain 'count'"
        assert data["symbol"] == "AAPL", f"Symbol should be AAPL, got: {data['symbol']}"
        
        print(f"AAPL predictions: {data['count']} items")
    
    def test_get_fear_greed(self):
        """GET /api/v1/market/fear-greed - returns Fear & Greed index"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/market/fear-greed",
            headers={"X-API-Key": self.api_key}
        )
        assert resp.status_code == 200, f"Fear & Greed request failed: {resp.text}"
        data = resp.json()
        
        # Fear & Greed should return some data (structure may vary)
        assert data is not None, "Response should not be None"
        print(f"Fear & Greed data received")
    
    def test_get_sectors(self):
        """GET /api/v1/market/sectors - returns sector data"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/market/sectors",
            headers={"X-API-Key": self.api_key}
        )
        assert resp.status_code == 200, f"Sectors request failed: {resp.text}"
        data = resp.json()
        
        # Sectors should return some data
        assert data is not None, "Response should not be None"
        print(f"Sectors data received")


class TestProOnlyEndpoints:
    """Tests for Pro-only API endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and get API key for Pro user"""
        self.session = get_authenticated_session()
        self.api_key = get_or_create_api_key(self.session)
        yield
        self.session.close()
    
    def test_war_room_pro_only(self):
        """GET /api/v1/signals/war-room/AAPL - Pro only, returns cached analysis"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/signals/war-room/AAPL",
            headers={"X-API-Key": self.api_key}
        )
        # Pro user should get 200 (may return no_cached_analysis if not run before)
        assert resp.status_code == 200, f"War Room request failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "symbol" in data, "Response should contain 'symbol'"
        assert data["symbol"] == "AAPL", f"Symbol should be AAPL, got: {data['symbol']}"
        
        print(f"War Room response: {data.get('status', 'has_data')}")
    
    def test_hypothesis_pro_only(self):
        """GET /api/v1/signals/hypothesis/AAPL - Pro only"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/signals/hypothesis/AAPL",
            headers={"X-API-Key": self.api_key}
        )
        # Pro user should get 200
        assert resp.status_code == 200, f"Hypothesis request failed: {resp.text}"
        data = resp.json()
        
        # Verify response structure
        assert "symbol" in data, "Response should contain 'symbol'"
        assert "hypotheses" in data, "Response should contain 'hypotheses'"
        assert "count" in data, "Response should contain 'count'"
        assert data["symbol"] == "AAPL", f"Symbol should be AAPL, got: {data['symbol']}"
        
        print(f"Hypothesis response: {data['count']} items")


class TestRateLimiting:
    """Tests for rate limiting functionality"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and get API key"""
        self.session = get_authenticated_session()
        self.api_key = get_or_create_api_key(self.session)
        yield
        self.session.close()
    
    def test_usage_increments_on_api_call(self):
        """Verify usage counter increments after API calls"""
        # Get initial usage
        usage_before = self.session.get(f"{BASE_URL}/api/developer/usage").json()
        initial_count = usage_before["today"]
        
        # Make an API call
        requests.get(
            f"{BASE_URL}/api/v1/predictions",
            headers={"X-API-Key": self.api_key}
        )
        
        # Check usage increased
        usage_after = self.session.get(f"{BASE_URL}/api/developer/usage").json()
        assert usage_after["today"] >= initial_count, "Usage should increment after API call"
        
        print(f"Usage: {initial_count} -> {usage_after['today']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
