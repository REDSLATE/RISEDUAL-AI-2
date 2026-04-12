"""
Iteration 102: User Badges Feature Tests
Tests for:
- POST /api/auth/login returns founding_member and beta_access fields
- Admin login returns role='admin' with founding_member=false
- Owner login returns role='owner' with founding_member=false
"""
import pytest
import requests
import os

from conftest_creds import BASE_URL, OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD


class TestUserBadgesBackend:
    """Tests for user badges feature - backend API responses"""
    
    def test_admin_login_returns_badge_fields(self):
        """Admin login should return founding_member and beta_access fields"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        
        data = response.json()
        
        # Verify required fields exist
        assert "founding_member" in data, "founding_member field missing from login response"
        assert "beta_access" in data, "beta_access field missing from login response"
        assert "role" in data, "role field missing from login response"
        
        # Verify admin-specific values
        assert data["role"] == "admin", f"Expected role='admin', got '{data['role']}'"
        assert data["founding_member"] == False, f"Admin should have founding_member=false, got {data['founding_member']}"
        
        print(f"Admin login response: role={data['role']}, founding_member={data['founding_member']}, beta_access={data['beta_access']}")
    
    def test_owner_login_returns_badge_fields(self):
        """Owner login should return founding_member and beta_access fields"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        
        data = response.json()
        
        # Verify required fields exist
        assert "founding_member" in data, "founding_member field missing from login response"
        assert "beta_access" in data, "beta_access field missing from login response"
        assert "role" in data, "role field missing from login response"
        
        # Verify owner-specific values
        assert data["role"] == "owner", f"Expected role='owner', got '{data['role']}'"
        assert data["founding_member"] == False, f"Owner should have founding_member=false, got {data['founding_member']}"
        
        print(f"Owner login response: role={data['role']}, founding_member={data['founding_member']}, beta_access={data['beta_access']}")
    
    def test_login_response_structure(self):
        """Verify complete login response structure includes all badge-related fields"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Required fields for user badges feature
        required_fields = ["id", "email", "name", "role", "subscription_status", "founding_member", "beta_access"]
        
        for field in required_fields:
            assert field in data, f"Required field '{field}' missing from login response"
        
        # Verify data types
        assert isinstance(data["founding_member"], bool), f"founding_member should be boolean, got {type(data['founding_member'])}"
        assert isinstance(data["beta_access"], bool), f"beta_access should be boolean, got {type(data['beta_access'])}"
        assert isinstance(data["role"], str), f"role should be string, got {type(data['role'])}"
        
        print(f"Login response structure verified: {list(data.keys())}")
    
    def test_me_endpoint_returns_badge_fields(self):
        """GET /api/auth/me should also return badge fields"""
        # First login to get cookies
        session = requests.Session()
        login_response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
        
        # Get access token from response
        login_data = login_response.json()
        access_token = login_data.get("access_token")
        
        # Call /me endpoint with Bearer token
        me_response = session.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert me_response.status_code == 200, f"/me endpoint failed: {me_response.text}"
        
        data = me_response.json()
        
        # Verify badge fields exist
        assert "founding_member" in data, "founding_member field missing from /me response"
        assert "beta_access" in data, "beta_access field missing from /me response"
        assert "role" in data, "role field missing from /me response"
        
        print(f"/me response: role={data['role']}, founding_member={data['founding_member']}, beta_access={data['beta_access']}")


class TestBadgePriority:
    """Tests for badge priority logic (Creator > Founding 100 > Beta > Pro > Free)"""
    
    def test_admin_is_creator_not_founding(self):
        """Admin should be Creator badge, not Founding 100"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Admin is Creator (role=admin), not Founding 100
        assert data["role"] == "admin"
        # Even if founding_member was true, Creator badge takes priority
        # But admin should have founding_member=false
        assert data["founding_member"] == False
        
        print("Admin badge priority verified: Creator (admin role)")
    
    def test_owner_is_creator_not_founding(self):
        """Owner should be Creator badge, not Founding 100"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Owner is Creator (role=owner), not Founding 100
        assert data["role"] == "owner"
        # Even if founding_member was true, Creator badge takes priority
        # But owner should have founding_member=false
        assert data["founding_member"] == False
        
        print("Owner badge priority verified: Creator (owner role)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
