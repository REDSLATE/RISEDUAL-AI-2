"""
Backend tests for RISEDUALAI Bearer Token Auth and AI Hypothesis
Tests: Login, Register, /me, Refresh, Hypothesis (free vs pro)
"""
import pytest
import requests
import os
import time
from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Admin credentials from environment
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", ADMIN_EMAIL)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


class TestAuthLogin:
    """Test login endpoint with Bearer token response"""
    
    def test_login_admin_success(self):
        """POST /api/auth/login with admin credentials returns tokens"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Verify tokens in response body
        assert "access_token" in data, "Missing access_token in response"
        assert "refresh_token" in data, "Missing refresh_token in response"
        assert len(data["access_token"]) > 50, "access_token too short"
        assert len(data["refresh_token"]) > 50, "refresh_token too short"
        
        # Verify user info
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        print(f"PASSED: Admin login returns tokens and user info (role={data['role']}, subscription={data['subscription_status']})")
    
    def test_login_wrong_password(self):
        """POST /api/auth/login with wrong password returns 401"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "WrongPassword123"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: Wrong password returns 401")
    
    def test_login_nonexistent_user(self):
        """POST /api/auth/login with nonexistent email returns 401"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "nonexistent@test.com",
            "password": "AnyPassword123"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: Nonexistent user returns 401")


class TestAuthRegister:
    """Test register endpoint"""
    
    def test_register_new_user(self):
        """POST /api/auth/register creates user with free subscription"""
        unique_email = f"test_user_{int(time.time())}@test.com"
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": unique_email,
            "password": "TestPass123!",
            "name": "Test User"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Verify tokens returned
        assert "access_token" in data, "Missing access_token"
        assert "refresh_token" in data, "Missing refresh_token"
        
        # Verify user info
        assert data["email"] == unique_email
        assert data["subscription_status"] == "free", f"Expected free, got {data['subscription_status']}"
        assert data["role"] == "user"
        print(f"PASSED: Register returns tokens + user with subscription_status=free")
    
    def test_register_duplicate_email(self):
        """POST /api/auth/register with existing email returns 400"""
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": ADMIN_EMAIL,
            "password": "TestPass123!",
            "name": "Duplicate"
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASSED: Duplicate email returns 400")
    
    def test_register_short_password(self):
        """POST /api/auth/register with short password returns 400"""
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": f"short_pw_{int(time.time())}@test.com",
            "password": "12345",
            "name": "Short PW"
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASSED: Short password returns 400")


class TestAuthMe:
    """Test /api/auth/me endpoint with Bearer token"""
    
    def test_me_with_valid_token(self):
        """GET /api/auth/me with valid Bearer token returns user info"""
        # First login to get token
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = login_resp.json()["access_token"]
        
        # Call /me with Bearer token
        response = requests.get(f"{BASE_URL}/api/auth/me", headers={
            "Authorization": f"Bearer {token}"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        print(f"PASSED: /me with Bearer token returns user info")
    
    def test_me_without_auth(self):
        """GET /api/auth/me without token returns 401"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /me without auth returns 401")
    
    def test_me_with_invalid_token(self):
        """GET /api/auth/me with invalid token returns 401"""
        response = requests.get(f"{BASE_URL}/api/auth/me", headers={
            "Authorization": "Bearer invalid_token_here"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /me with invalid token returns 401")


class TestAuthRefresh:
    """Test token refresh endpoint"""
    
    def test_refresh_token(self):
        """POST /api/auth/refresh with valid refresh_token returns new access_token"""
        # Login to get refresh token
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        refresh_token = login_resp.json()["refresh_token"]
        
        # Refresh
        response = requests.post(f"{BASE_URL}/api/auth/refresh", json={
            "refresh_token": refresh_token
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "access_token" in data, "Missing access_token in refresh response"
        assert len(data["access_token"]) > 50
        print("PASSED: Refresh returns new access_token")
    
    def test_refresh_without_token(self):
        """POST /api/auth/refresh without token returns 401"""
        response = requests.post(f"{BASE_URL}/api/auth/refresh", json={})
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: Refresh without token returns 401")


class TestHypothesisFreeUser:
    """Test hypothesis endpoint for free/unauthenticated users"""
    
    def test_hypothesis_without_auth(self):
        """GET /api/hypothesis/AAPL without auth returns is_pro=false with teaser"""
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["is_pro"] == False, f"Expected is_pro=false, got {data.get('is_pro')}"
        assert data["symbol"] == "AAPL"
        assert "teaser" in data, "Missing teaser for free user"
        assert data["teaser"]["verdict"] == "LOCKED"
        assert "data_sources_count" in data["teaser"]
        assert "world_events_count" in data["teaser"]
        assert "congressional_trades_count" in data["teaser"]
        print(f"PASSED: Hypothesis without auth returns is_pro=false, verdict=LOCKED")
    
    def test_hypothesis_free_user_with_token(self):
        """GET /api/hypothesis/AAPL with free user token returns is_pro=false"""
        # Register a new free user
        unique_email = f"free_user_{int(time.time())}@test.com"
        reg_resp = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": unique_email,
            "password": "TestPass123!",
            "name": "Free User"
        })
        token = reg_resp.json()["access_token"]
        
        # Get hypothesis with free user token
        response = requests.get(f"{BASE_URL}/api/hypothesis/TSLA", headers={
            "Authorization": f"Bearer {token}"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data["is_pro"] == False, f"Expected is_pro=false for free user"
        assert data["teaser"]["verdict"] == "LOCKED"
        print("PASSED: Free user with token gets is_pro=false, verdict=LOCKED")


class TestHypothesisProUser:
    """Test hypothesis endpoint for Pro users (admin)"""
    
    def test_hypothesis_pro_user(self):
        """GET /api/hypothesis/AAPL with admin (Pro) token returns full hypothesis
        NOTE: This test takes 60-90 seconds due to AI generation
        """
        # Login as admin (Pro user)
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = login_resp.json()["access_token"]
        
        print("Fetching hypothesis for Pro user (this takes 60-90 seconds)...")
        # Get hypothesis with Pro user token - increased timeout
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", headers={
            "Authorization": f"Bearer {token}"
        }, timeout=120)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["is_pro"] == True, f"Expected is_pro=true for Pro user, got {data.get('is_pro')}"
        assert data["symbol"] == "AAPL"
        
        # Verify full hypothesis fields
        assert "verdict" in data, "Missing verdict in Pro hypothesis"
        assert data["verdict"] in ["BUY", "SELL", "HOLD", "NEUTRAL"], f"Invalid verdict: {data.get('verdict')}"
        assert "confidence" in data, "Missing confidence"
        assert "thesis" in data, "Missing thesis"
        
        print(f"PASSED: Pro user gets full hypothesis (verdict={data['verdict']}, confidence={data.get('confidence')}%)")


class TestLogout:
    """Test logout endpoint"""
    
    def test_logout(self):
        """POST /api/auth/logout returns success"""
        response = requests.post(f"{BASE_URL}/api/auth/logout")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "message" in data
        print("PASSED: Logout returns success message")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
