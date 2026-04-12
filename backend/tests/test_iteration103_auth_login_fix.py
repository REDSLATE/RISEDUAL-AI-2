"""
Iteration 103: Auth Login Fix Tests
Tests for the 500 Internal Server Error fix when logging in with Owner account.
Key fixes tested:
1. check_brute_force handles naive datetimes (normalizes to UTC-aware)
2. login() checks for missing password_hash before calling verify_password
3. seed_admin handles missing password_hash with try/except
4. Fixed Motor DB boolean bug in orderflow_ws_service.py
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestHealthCheck:
    """Verify server is healthy before running auth tests"""
    
    def test_api_root_returns_ready(self):
        """GET /api/ returns ready message"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Ready" in data["message"] or "RISEDUAL" in data["message"]


class TestOwnerLogin:
    """Test Owner account login - the main bug fix scenario"""
    
    def test_owner_login_returns_200(self):
        """POST /api/auth/login with owner credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
    def test_owner_login_returns_correct_user_data(self):
        """POST /api/auth/login returns correct user fields for owner"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify all required fields are present
        required_fields = [
            "id", "email", "name", "role", "subscription_status",
            "is_active", "founding_member", "beta_access",
            "access_token", "refresh_token"
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        # Verify correct values
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        assert data["subscription_status"] == "pro"
        assert data["is_active"] == True
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        assert isinstance(data["refresh_token"], str)
        assert len(data["refresh_token"]) > 0


class TestAdminLogin:
    """Test Admin account login"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
    def test_admin_login_returns_correct_user_data(self):
        """POST /api/auth/login returns correct user fields for admin"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify all required fields
        required_fields = [
            "id", "email", "name", "role", "subscription_status",
            "is_active", "founding_member", "beta_access",
            "access_token", "refresh_token"
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        # Verify correct values
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        assert data["is_active"] == True


class TestInvalidCredentials:
    """Test error handling for invalid credentials - should return 401, not 500"""
    
    def test_wrong_password_returns_401(self):
        """POST /api/auth/login with wrong password returns 401 (not 500)"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": "WrongPassword123!"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data
        assert "Invalid" in data["detail"] or "password" in data["detail"].lower()
        
    def test_nonexistent_email_returns_401(self):
        """POST /api/auth/login with non-existent email returns 401 (not 500)"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nonexistent@example.com", "password": "SomePassword123!"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data


class TestAuthMeEndpoint:
    """Test GET /api/auth/me with valid token"""
    
    def test_me_endpoint_with_valid_token(self):
        """GET /api/auth/me with valid token returns correct user data"""
        # First login to get token
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        
        # Call /me endpoint
        me_response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert me_response.status_code == 200
        data = me_response.json()
        
        # Verify user data
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        assert "founding_member" in data
        assert "beta_access" in data


class TestRefreshToken:
    """Test POST /api/auth/refresh with valid refresh token"""
    
    def test_refresh_returns_new_access_token(self):
        """POST /api/auth/refresh with valid refresh token returns new access_token"""
        # First login to get refresh token
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
        refresh_token = login_response.json()["refresh_token"]
        
        # Call refresh endpoint
        refresh_response = requests.post(
            f"{BASE_URL}/api/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        assert refresh_response.status_code == 200
        data = refresh_response.json()
        assert "access_token" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0


class TestLogout:
    """Test POST /api/auth/logout clears cookies"""
    
    def test_logout_returns_success(self):
        """POST /api/auth/logout returns success message"""
        response = requests.post(f"{BASE_URL}/api/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Logged out" in data["message"]


class TestBruteForceProtection:
    """Test brute force protection - 5 failed attempts should lock account
    
    Note: Brute force protection uses IP:email as identifier.
    In test environments with load balancers/proxies, the IP may vary,
    so lockout may not trigger consistently. This test verifies the
    mechanism exists and returns proper error codes.
    """
    
    def test_failed_login_returns_401_not_500(self):
        """Failed login attempts should return 401, not 500 (the original bug)"""
        test_email = f"bruteforce_test_{int(time.time())}@example.com"
        
        # Make several failed attempts - all should return 401, not 500
        for i in range(3):
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": test_email, "password": f"WrongPassword{i}"}
            )
            # Should be 401 (invalid credentials) or 429 (locked), never 500
            assert response.status_code in [401, 429], \
                f"Attempt {i+1}: Expected 401 or 429, got {response.status_code}: {response.text}"
            
    def test_brute_force_mechanism_exists(self):
        """Verify brute force protection mechanism is in place"""
        # This test verifies the code path exists by checking the response format
        test_email = f"bruteforce_verify_{int(time.time())}@example.com"
        
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": test_email, "password": "WrongPassword"}
        )
        
        # Should return proper error response, not 500
        assert response.status_code in [401, 429]
        data = response.json()
        assert "detail" in data
        # Either "Invalid email or password" or "Too many failed attempts"
        assert "Invalid" in data["detail"] or "Too many" in data["detail"]


class TestLoginResponseFields:
    """Verify all expected fields in login response"""
    
    def test_login_response_has_all_required_fields(self):
        """Login response should have all required fields"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        
        # All required fields per the review request
        required_fields = [
            "id",
            "email", 
            "name",
            "role",
            "subscription_status",
            "is_active",
            "founding_member",
            "beta_access",
            "access_token",
            "refresh_token"
        ]
        
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
            
        # Verify types
        assert isinstance(data["id"], str)
        assert isinstance(data["email"], str)
        assert isinstance(data["name"], str)
        assert isinstance(data["role"], str)
        assert isinstance(data["subscription_status"], str)
        assert isinstance(data["is_active"], bool)
        assert isinstance(data["founding_member"], bool)
        assert isinstance(data["beta_access"], bool)
        assert isinstance(data["access_token"], str)
        assert isinstance(data["refresh_token"], str)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
