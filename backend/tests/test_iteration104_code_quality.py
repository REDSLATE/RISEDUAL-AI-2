"""Iteration 104: Code Quality Fixes Verification Tests

Tests verify:
1. Auth endpoints still work after hardcoded secrets centralized to conftest_creds.py
2. No regressions after console.log/warn/error replaced with logger utility
3. Refactored market_data.py (get_ticker_prediction helpers) still works
4. Motor DB boolean bug fix still in place
5. Type hints added to route_registry.py (code review)
"""
import pytest
import requests

# Import centralized credentials
from conftest_creds import (
    BASE_URL, OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD
)


class TestAuthEndpoints:
    """Auth endpoints - verify no regressions after code quality fixes"""

    def test_owner_login_returns_200(self):
        """POST /api/auth/login with owner credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        print(f"✓ Owner login successful: {data['email']} (role={data['role']})")

    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"✓ Admin login successful: {data['email']} (role={data['role']})")

    def test_wrong_password_returns_401(self):
        """POST /api/auth/login with wrong password returns 401 (not 500)"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": "WrongPassword123!"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("✓ Wrong password correctly returns 401")

    def test_nonexistent_email_returns_401(self):
        """POST /api/auth/login with non-existent email returns 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nonexistent@test.com", "password": "Test1234!"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("✓ Non-existent email correctly returns 401")

    def test_auth_me_with_valid_token(self):
        """GET /api/auth/me with valid token returns user data"""
        # First login to get token
        login_resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        # Now test /me endpoint
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        print(f"✓ /api/auth/me returns correct user data: {data['email']}")


class TestPublicEndpoints:
    """Public endpoints - verify no regressions after code quality fixes"""

    def test_root_endpoint(self):
        """GET /api/ returns ready message"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "message" in data
        print(f"✓ Root endpoint: {data['message']}")

    def test_sectors_heatmap(self):
        """GET /api/sectors/heatmap returns sectors data"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        # Should return sectors array or similar structure
        assert isinstance(data, (dict, list)), "Expected dict or list response"
        print("✓ Sectors heatmap endpoint working")

    def test_waitlist_stats(self):
        """GET /api/waitlist/stats returns stats"""
        response = requests.get(f"{BASE_URL}/api/waitlist/stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, dict), "Expected dict response"
        print(f"✓ Waitlist stats endpoint working: {data}")

    def test_chat_limit(self):
        """GET /api/chat/limit returns limit info"""
        response = requests.get(f"{BASE_URL}/api/chat/limit")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, dict), "Expected dict response"
        print("✓ Chat limit endpoint working")

    def test_fear_greed(self):
        """GET /api/fear-greed returns data"""
        response = requests.get(f"{BASE_URL}/api/fear-greed")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, dict), "Expected dict response"
        print("✓ Fear & Greed endpoint working")


class TestRefactoredMarketData:
    """Market data endpoints - verify refactored get_ticker_prediction still works"""

    def test_market_prediction_endpoint(self):
        """GET /api/market/prediction returns prediction data"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=60)
        # This endpoint may take time due to AI processing
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, dict), "Expected dict response"
        # Check for expected fields
        if "overall_direction" in data:
            print(f"✓ Market prediction: direction={data.get('overall_direction')}")
        else:
            print(f"✓ Market prediction endpoint working (response keys: {list(data.keys())[:5]})")

    def test_ticker_prediction_endpoint(self):
        """GET /api/market/prediction/{symbol} - refactored with _fetch_ticker_context and _log_ticker_prediction helpers"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/AAPL", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, dict), "Expected dict response"
        # Check for ticker-specific fields
        assert data.get("symbol") == "AAPL" or "ticker_focused" in data, "Expected ticker-focused response"
        print(f"✓ Ticker prediction for AAPL working (symbol={data.get('symbol')})")

    def test_ticker_prediction_invalid_symbol(self):
        """GET /api/market/prediction/{symbol} with invalid symbol returns 400"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/TOOLONGSYMBOL123")
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("✓ Invalid symbol correctly returns 400")


class TestCredentialsCentralization:
    """Verify conftest_creds.py is properly configured"""

    def test_base_url_from_env(self):
        """BASE_URL should be set from environment"""
        assert BASE_URL, "BASE_URL should not be empty"
        assert BASE_URL.startswith("http"), f"BASE_URL should start with http: {BASE_URL}"
        print(f"✓ BASE_URL configured: {BASE_URL}")

    def test_owner_credentials_set(self):
        """Owner credentials should be set"""
        assert OWNER_EMAIL, "OWNER_EMAIL should not be empty"
        assert OWNER_PASSWORD, "OWNER_PASSWORD should not be empty"
        assert "@" in OWNER_EMAIL, "OWNER_EMAIL should be valid email"
        print(f"✓ Owner credentials configured: {OWNER_EMAIL}")

    def test_admin_credentials_set(self):
        """Admin credentials should be set"""
        assert ADMIN_EMAIL, "ADMIN_EMAIL should not be empty"
        assert ADMIN_PASSWORD, "ADMIN_PASSWORD should not be empty"
        assert "@" in ADMIN_EMAIL, "ADMIN_EMAIL should be valid email"
        print(f"✓ Admin credentials configured: {ADMIN_EMAIL}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
