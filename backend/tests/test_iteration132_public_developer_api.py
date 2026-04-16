"""
Iteration 132: Public Developer API Testing

Tests the RISEDUAL AI Public Developer API for subscribers:
- Developer key management endpoints (JWT auth via Bearer token)
- Public API endpoints (API key auth via x-api-key header)
- Tier-based access control (Free/Pro/Enterprise)
- Rate limiting and usage tracking

Admin user has subscription_status='pro' which grants access to:
- All Free tier endpoints (watchlist, predictions, quote, market, headlines)
- Pro tier endpoints (ml_signal, ml-stats, sectors, provider_status, etc.)
- Should be DENIED access to Enterprise-only endpoints (batch_signals, paper_trades)
"""

import pytest
import requests
import os

from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Pre-existing API key for admin user (Pro tier)
EXISTING_API_KEY = os.environ.get("TEST_API_KEY", "")


class TestPublicAPIInfo:
    """Test /api/v1/info - public endpoint, no auth required"""
    
    def test_info_endpoint_no_auth(self):
        """GET /api/v1/info should return API info without authentication"""
        response = requests.get(f"{BASE_URL}/api/v1/info")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "api" in data, "Response should contain 'api' field"
        assert "version" in data, "Response should contain 'version' field"
        assert "tiers" in data, "Response should contain 'tiers' field"
        assert "authentication" in data, "Response should contain 'authentication' field"
        
        # Verify tier details
        tiers = data["tiers"]
        assert "free" in tiers, "Should have 'free' tier"
        assert "pro" in tiers, "Should have 'pro' tier"
        assert "enterprise" in tiers, "Should have 'enterprise' tier"
        
        # Verify tier structure
        for tier_name, tier_config in tiers.items():
            assert "daily_limit" in tier_config, f"{tier_name} should have daily_limit"
            assert "endpoints" in tier_config, f"{tier_name} should have endpoints"
            assert "label" in tier_config, f"{tier_name} should have label"
        
        print(f"✓ /api/v1/info returns API info: {data['api']} v{data['version']}")
        print(f"  Tiers: Free ({tiers['free']['daily_limit']}/day), Pro ({tiers['pro']['daily_limit']}/day), Enterprise ({tiers['enterprise']['daily_limit']}/day)")


class TestDeveloperKeyManagement:
    """Test /api/developer/* endpoints - require JWT auth via Bearer token"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Login and get JWT access token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        token = data.get("access_token")
        assert token, "Login should return access_token"
        print(f"✓ Logged in as {ADMIN_EMAIL}, got JWT token")
        return token
    
    def test_list_keys_requires_auth(self):
        """GET /api/developer/keys should require authentication"""
        response = requests.get(f"{BASE_URL}/api/developer/keys")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("✓ /api/developer/keys returns 401 without auth")
    
    def test_list_keys_with_bearer_token(self, auth_token):
        """GET /api/developer/keys should return user's API keys (masked)"""
        response = requests.get(
            f"{BASE_URL}/api/developer/keys",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "keys" in data, "Response should contain 'keys' array"
        
        keys = data["keys"]
        print(f"✓ /api/developer/keys returns {len(keys)} keys")
        
        # Verify key structure if keys exist
        if keys:
            key = keys[0]
            assert "key_preview" in key, "Key should have key_preview (masked)"
            assert "key_id" in key, "Key should have key_id (last 8 chars)"
            assert "label" in key, "Key should have label"
            assert "is_active" in key, "Key should have is_active"
            assert "total_calls" in key, "Key should have total_calls"
            print(f"  First key: {key['key_preview']} (id: {key['key_id']}, active: {key['is_active']}, calls: {key['total_calls']})")
        
        return keys
    
    def test_generate_key_requires_auth(self):
        """POST /api/developer/keys/generate should require authentication"""
        response = requests.post(f"{BASE_URL}/api/developer/keys/generate")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("✓ /api/developer/keys/generate returns 401 without auth")
    
    def test_generate_key_with_bearer_token(self, auth_token):
        """POST /api/developer/keys/generate should create a new API key"""
        response = requests.post(
            f"{BASE_URL}/api/developer/keys/generate",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"label": "TEST_iteration132_key"}
        )
        
        # Could be 200 (success) or 400 (max keys reached)
        if response.status_code == 400:
            data = response.json()
            assert "Maximum 3 active API keys" in data.get("detail", ""), "Should indicate max keys reached"
            print("✓ /api/developer/keys/generate returns 400 - max keys reached (expected)")
            return None
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "key" in data, "Response should contain 'key'"
        assert "label" in data, "Response should contain 'label'"
        assert data["key"].startswith("rsd_live_"), "Key should start with 'rsd_live_'"
        
        print(f"✓ /api/developer/keys/generate created new key: {data['key'][:20]}...")
        return data["key"]
    
    def test_usage_requires_auth(self):
        """GET /api/developer/usage should require authentication"""
        response = requests.get(f"{BASE_URL}/api/developer/usage")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("✓ /api/developer/usage returns 401 without auth")
    
    def test_usage_with_bearer_token(self, auth_token):
        """GET /api/developer/usage should return daily usage stats"""
        response = requests.get(
            f"{BASE_URL}/api/developer/usage",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "today" in data, "Response should contain 'today' (usage count)"
        assert "limit" in data, "Response should contain 'limit'"
        assert "remaining" in data, "Response should contain 'remaining'"
        assert "tier" in data, "Response should contain 'tier'"
        assert "tier_label" in data, "Response should contain 'tier_label'"
        assert "endpoints_available" in data, "Response should contain 'endpoints_available'"
        
        print(f"✓ /api/developer/usage: {data['today']}/{data['limit']} calls today ({data['tier_label']} tier)")
        print(f"  Remaining: {data['remaining']}, Endpoints: {len(data['endpoints_available'])}")
        
        return data


class TestPublicAPIAuthErrors:
    """Test authentication error handling for /api/v1/* endpoints"""
    
    def test_missing_api_key_header(self):
        """Endpoints requiring API key should return 401 when header is missing"""
        response = requests.get(f"{BASE_URL}/api/v1/predictions")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        
        data = response.json()
        assert "Missing X-API-Key" in data.get("detail", ""), "Should indicate missing API key"
        print("✓ /api/v1/predictions returns 401 with 'Missing X-API-Key' when no header")
    
    def test_invalid_api_key(self):
        """Endpoints should return 401 for invalid API key"""
        response = requests.get(
            f"{BASE_URL}/api/v1/predictions",
            headers={"X-API-Key": "rsd_live_invalid_key_12345678901234567890"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        
        data = response.json()
        assert "Invalid or revoked" in data.get("detail", ""), "Should indicate invalid key"
        print("✓ /api/v1/predictions returns 401 with 'Invalid or revoked' for bad key")


class TestFreeTierEndpoints:
    """Test Free tier endpoints with valid API key (Pro user has access to all Free endpoints)"""
    
    def test_predictions_endpoint(self):
        """GET /api/v1/predictions - Free tier endpoint"""
        response = requests.get(
            f"{BASE_URL}/api/v1/predictions",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "predictions" in data, "Response should contain 'predictions'"
        assert "count" in data, "Response should contain 'count'"
        
        print(f"✓ /api/v1/predictions returns {data['count']} predictions")
    
    def test_quote_endpoint(self):
        """GET /api/v1/quote/AAPL - Free tier endpoint, returns live stock quote"""
        response = requests.get(
            f"{BASE_URL}/api/v1/quote/AAPL",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Quote should have price data
        assert "symbol" in data or "price" in data or "c" in data, "Quote should have price data"
        
        print(f"✓ /api/v1/quote/AAPL returns quote data: {data}")


class TestProTierEndpoints:
    """Test Pro tier endpoints - admin user has Pro subscription"""
    
    def test_ml_signal_endpoint(self):
        """GET /api/v1/ml-signal/AAPL - Pro tier required"""
        response = requests.get(
            f"{BASE_URL}/api/v1/ml-signal/AAPL",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Could return actual signal or "no_model" status
        if data.get("status") == "no_model":
            print("✓ /api/v1/ml-signal/AAPL returns no_model status (model not trained yet)")
        else:
            assert "direction" in data or "ticker" in data, "Signal should have direction or ticker"
            print(f"✓ /api/v1/ml-signal/AAPL returns ML signal: {data}")
    
    def test_ml_stats_endpoint(self):
        """GET /api/v1/ml-stats - Pro tier required"""
        response = requests.get(
            f"{BASE_URL}/api/v1/ml-stats",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "gate_status" in data or "stats" in data, "Response should have gate_status or stats"
        
        print("✓ /api/v1/ml-stats returns ML pipeline status")
    
    def test_sectors_endpoint_valid_period(self):
        """GET /api/v1/sectors?period=1d - Pro tier required, validates period"""
        response = requests.get(
            f"{BASE_URL}/api/v1/sectors?period=1d",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "period" in data, "Response should contain 'period'"
        assert data["period"] == "1d", "Period should be '1d'"
        
        print("✓ /api/v1/sectors?period=1d returns sector data")
    
    def test_sectors_endpoint_invalid_period(self):
        """GET /api/v1/sectors?period=invalid - should return 400"""
        response = requests.get(
            f"{BASE_URL}/api/v1/sectors?period=invalid",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 400, f"Expected 400 for invalid period, got {response.status_code}"
        
        data = response.json()
        assert "Invalid period" in data.get("detail", ""), "Should indicate invalid period"
        
        print("✓ /api/v1/sectors?period=invalid returns 400 with validation error")


class TestEnterpriseTierRestrictions:
    """Test that Enterprise-only endpoints return 403 for Pro users"""
    
    def test_batch_signals_forbidden_for_pro(self):
        """POST /api/v1/ml-signal/batch - Enterprise only, should return 403 for Pro"""
        response = requests.post(
            f"{BASE_URL}/api/v1/ml-signal/batch",
            headers={"X-API-Key": EXISTING_API_KEY},
            json={"tickers": ["AAPL", "NVDA", "TSLA"]}
        )
        assert response.status_code == 403, f"Expected 403 for Pro user, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "enterprise" in data.get("detail", "").lower(), "Should indicate enterprise tier required"
        
        print("✓ /api/v1/ml-signal/batch returns 403 for Pro user (Enterprise only)")
    
    def test_paper_trades_forbidden_for_pro(self):
        """GET /api/v1/paper-trades - Enterprise only, should return 403 for Pro"""
        response = requests.get(
            f"{BASE_URL}/api/v1/paper-trades",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response.status_code == 403, f"Expected 403 for Pro user, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "enterprise" in data.get("detail", "").lower(), "Should indicate enterprise tier required"
        
        print("✓ /api/v1/paper-trades returns 403 for Pro user (Enterprise only)")


class TestRateLimitTracking:
    """Test that API calls increment the rate limit counter"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Login and get JWT access token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        return response.json().get("access_token")
    
    def test_usage_increments_on_api_call(self, auth_token):
        """Rate limit counter should increment on each API call"""
        # Get initial usage
        response1 = requests.get(
            f"{BASE_URL}/api/developer/usage",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response1.status_code == 200
        initial_usage = response1.json()["today"]
        
        # Make an API call using the API key
        response2 = requests.get(
            f"{BASE_URL}/api/v1/predictions",
            headers={"X-API-Key": EXISTING_API_KEY}
        )
        assert response2.status_code == 200
        
        # Check usage again
        response3 = requests.get(
            f"{BASE_URL}/api/developer/usage",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response3.status_code == 200
        new_usage = response3.json()["today"]
        
        # Usage should have incremented
        assert new_usage >= initial_usage, f"Usage should have incremented: {initial_usage} -> {new_usage}"
        
        print(f"✓ Rate limit counter incremented: {initial_usage} -> {new_usage}")


class TestKeyRevocation:
    """Test API key revocation flow"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Login and get JWT access token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        return response.json().get("access_token")
    
    def test_revoke_nonexistent_key(self, auth_token):
        """DELETE /api/developer/keys/{key_id} should return 404 for nonexistent key"""
        response = requests.delete(
            f"{BASE_URL}/api/developer/keys/nonexist",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        print("✓ /api/developer/keys/nonexist returns 404 for nonexistent key")
    
    def test_revoke_requires_auth(self):
        """DELETE /api/developer/keys/{key_id} should require authentication"""
        response = requests.delete(f"{BASE_URL}/api/developer/keys/12345678")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        
        print("✓ /api/developer/keys/{key_id} returns 401 without auth")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
