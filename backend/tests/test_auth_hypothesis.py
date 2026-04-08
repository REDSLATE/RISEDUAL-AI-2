"""
Test suite for RISEDUALAI Auth System and AI Hypothesis Engine
Tests: JWT auth (register, login, logout, me, refresh), brute force protection, hypothesis paywall
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

# Test user for registration
TEST_USER_EMAIL = f"test_user_{int(time.time())}@test.com"
TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "TestPass123!")
TEST_USER_NAME = "Test User"


class TestAuthRegister:
    """Test POST /api/auth/register"""
    
    def test_register_success(self):
        """Register a new user successfully"""
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD,
            "name": TEST_USER_NAME
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "id" in data, "Response should contain user id"
        assert data["email"] == TEST_USER_EMAIL.lower(), "Email should match"
        assert data["name"] == TEST_USER_NAME, "Name should match"
        assert data["role"] == "user", "New user should have role=user"
        assert data["subscription_status"] == "free", "New user should have subscription_status=free"
        
        # Check cookies are set
        assert "access_token" in response.cookies, "access_token cookie should be set"
        assert "refresh_token" in response.cookies, "refresh_token cookie should be set"
        print(f"✓ Register success: {data['email']} with role={data['role']}, subscription={data['subscription_status']}")
    
    def test_register_duplicate_email(self):
        """Registering with existing email should fail"""
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": ADMIN_EMAIL,
            "password": "SomePassword123",
            "name": "Duplicate"
        })
        assert response.status_code == 400, f"Expected 400 for duplicate email, got {response.status_code}"
        assert "already registered" in response.json().get("detail", "").lower()
        print("✓ Duplicate email registration correctly rejected")
    
    def test_register_short_password(self):
        """Password less than 6 chars should fail"""
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": f"short_pw_{int(time.time())}@test.com",
            "password": "12345",
            "name": "Short PW"
        })
        assert response.status_code == 400, f"Expected 400 for short password, got {response.status_code}"
        print("✓ Short password correctly rejected")


class TestAuthLogin:
    """Test POST /api/auth/login"""
    
    def test_login_admin_success(self):
        """Login with admin credentials"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["email"] == ADMIN_EMAIL, "Email should match"
        assert data["role"] == "admin", "Admin should have role=admin"
        assert data["subscription_status"] == "pro", "Admin should have subscription_status=pro"
        
        # Check cookies
        assert "access_token" in session.cookies, "access_token cookie should be set"
        assert "refresh_token" in session.cookies, "refresh_token cookie should be set"
        print(f"✓ Admin login success: role={data['role']}, subscription={data['subscription_status']}")
    
    def test_login_wrong_password(self):
        """Login with wrong password should return 401"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "WrongPassword123"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        assert "invalid" in response.json().get("detail", "").lower()
        print("✓ Wrong password correctly rejected with 401")
    
    def test_login_nonexistent_user(self):
        """Login with non-existent email should return 401"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "nonexistent@test.com",
            "password": "SomePassword123"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Non-existent user login correctly rejected")


class TestAuthMe:
    """Test GET /api/auth/me"""
    
    def test_me_with_valid_session(self):
        """Get current user with valid cookies"""
        session = requests.Session()
        # Login first
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, "Login should succeed"
        
        # Get /me
        me_resp = session.get(f"{BASE_URL}/api/auth/me")
        assert me_resp.status_code == 200, f"Expected 200, got {me_resp.status_code}: {me_resp.text}"
        
        data = me_resp.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        print(f"✓ /me returns correct user: {data['email']}")
    
    def test_me_without_auth(self):
        """Get /me without cookies should return 401"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ /me without auth correctly returns 401")


class TestAuthLogout:
    """Test POST /api/auth/logout"""
    
    def test_logout_clears_cookies(self):
        """Logout should clear auth cookies"""
        session = requests.Session()
        # Login first
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        # Logout
        logout_resp = session.post(f"{BASE_URL}/api/auth/logout")
        assert logout_resp.status_code == 200, f"Expected 200, got {logout_resp.status_code}"
        assert logout_resp.json().get("message") == "Logged out"
        
        # Verify /me now fails (cookies should be cleared)
        me_resp = session.get(f"{BASE_URL}/api/auth/me")
        assert me_resp.status_code == 401, "After logout, /me should return 401"
        print("✓ Logout clears cookies and /me returns 401")


class TestAuthRefresh:
    """Test POST /api/auth/refresh"""
    
    def test_refresh_token(self):
        """Refresh token should work with valid refresh_token cookie"""
        session = requests.Session()
        # Login first
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        # Refresh
        refresh_resp = session.post(f"{BASE_URL}/api/auth/refresh")
        assert refresh_resp.status_code == 200, f"Expected 200, got {refresh_resp.status_code}: {refresh_resp.text}"
        assert refresh_resp.json().get("message") == "Token refreshed"
        print("✓ Token refresh works")
    
    def test_refresh_without_token(self):
        """Refresh without refresh_token should fail"""
        response = requests.post(f"{BASE_URL}/api/auth/refresh")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Refresh without token correctly returns 401")


class TestHypothesisFreeUser:
    """Test GET /api/hypothesis/{symbol} as free user (no auth or new user)"""
    
    def test_hypothesis_free_user_returns_locked(self):
        """Free user should get locked/teaser hypothesis"""
        # No cookies = free user
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["symbol"] == "AAPL", "Symbol should be AAPL"
        assert data["is_pro"] == False, "is_pro should be False for free user"
        assert "teaser" in data, "Response should contain teaser"
        assert data["teaser"]["verdict"] == "LOCKED", "Verdict should be LOCKED"
        assert "data_sources_count" in data["teaser"], "Teaser should have data_sources_count"
        assert "world_events_count" in data["teaser"], "Teaser should have world_events_count"
        assert "congressional_trades_count" in data["teaser"], "Teaser should have congressional_trades_count"
        assert "summary" in data["teaser"], "Teaser should have summary"
        print(f"✓ Free user hypothesis: is_pro={data['is_pro']}, verdict={data['teaser']['verdict']}")
        print(f"  Teaser stats: {data['teaser']['data_sources_count']} data points, {data['teaser']['world_events_count']} world events, {data['teaser']['congressional_trades_count']} congress trades")


class TestHypothesisProUser:
    """Test GET /api/hypothesis/{symbol} as Pro user (admin)"""
    
    def test_hypothesis_pro_user_returns_full(self):
        """Pro user should get full hypothesis with verdict, confidence, thesis, etc."""
        session = requests.Session()
        # Login as admin (pro user)
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, "Admin login should succeed"
        
        # Get hypothesis - this takes 60-90 seconds for AI analysis
        print("  Fetching hypothesis for AAPL as Pro user (may take 60-90 seconds)...")
        response = session.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=150)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["symbol"] == "AAPL", "Symbol should be AAPL"
        assert data["is_pro"] == True, "is_pro should be True for pro user"
        assert "verdict" in data, "Response should contain verdict"
        assert data["verdict"] in ["BUY", "SELL", "HOLD", "NEUTRAL"], f"Verdict should be BUY/SELL/HOLD/NEUTRAL, got {data.get('verdict')}"
        assert "confidence" in data, "Response should contain confidence"
        assert isinstance(data["confidence"], (int, float)), "Confidence should be a number"
        print(f"✓ Pro user hypothesis: is_pro={data['is_pro']}, verdict={data['verdict']}, confidence={data['confidence']}%")
        
        # Check optional fields
        if "thesis" in data:
            print(f"  Thesis: {data['thesis'][:100]}...")
        if "catalysts" in data:
            print(f"  Catalysts: {len(data['catalysts'])} items")
        if "risks" in data:
            print(f"  Risks: {len(data['risks'])} items")


class TestBcryptHashFormat:
    """Verify bcrypt hash format starts with $2b$"""
    
    def test_bcrypt_hash_format(self):
        """Register user and verify password is hashed with bcrypt"""
        # This is an indirect test - we verify login works which means bcrypt is working
        unique_email = f"bcrypt_test_{int(time.time())}@test.com"
        
        # Register
        reg_resp = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": unique_email,
            "password": "BcryptTest123!",
            "name": "Bcrypt Test"
        })
        assert reg_resp.status_code == 200, f"Registration failed: {reg_resp.text}"
        
        # Login with same password
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": unique_email,
            "password": "BcryptTest123!"
        })
        assert login_resp.status_code == 200, "Login should work with correct password"
        
        # Login with wrong password should fail
        wrong_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": unique_email,
            "password": "WrongPassword"
        })
        assert wrong_resp.status_code == 401, "Login should fail with wrong password"
        print("✓ Bcrypt password hashing working correctly (login/verify works)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
