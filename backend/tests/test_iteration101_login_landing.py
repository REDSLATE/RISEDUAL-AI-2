"""
Iteration 101: Test Login from Landing Page Feature
- Admin/Owner can log in directly (not on waitlist)
- POST /api/auth/login works for admin@risedual.ai
- POST /api/auth/login works for managingdirector@redslateholdings.com
- Admin/Owner are NOT in the waitlist collection
- POST /api/waitlist/join still works for regular users
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAdminOwnerLogin:
    """Test that admin and owner can log in directly"""
    
    def test_admin_login_success(self):
        """Admin should be able to log in directly"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        assert data.get("email") == "admin@risedual.ai"
        assert data.get("role") == "admin"
        assert "access_token" in data
        print(f"✓ Admin login successful: {data.get('email')}, role={data.get('role')}")
    
    def test_owner_login_success(self):
        """Owner should be able to log in directly"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "managingdirector@redslateholdings.com",
            "password": "RedSlate2026!"
        })
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        data = response.json()
        assert data.get("email") == "managingdirector@redslateholdings.com"
        assert data.get("role") == "owner"
        assert "access_token" in data
        print(f"✓ Owner login successful: {data.get('email')}, role={data.get('role')}")
    
    def test_invalid_credentials_rejected(self):
        """Invalid credentials should be rejected"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "wrongpassword"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Invalid credentials correctly rejected")


class TestAdminOwnerNotInWaitlist:
    """Test that admin and owner are NOT in the waitlist collection"""
    
    def test_admin_not_in_waitlist(self):
        """Admin email should not be in waitlist"""
        # Get admin token first
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        assert login_resp.status_code == 200
        cookies = login_resp.cookies
        
        # Check waitlist admin list
        response = requests.get(f"{BASE_URL}/api/waitlist/admin/list?limit=100", cookies=cookies)
        assert response.status_code == 200, f"Failed to get waitlist: {response.text}"
        data = response.json()
        
        # Check that admin email is not in waitlist
        waitlist_emails = [entry.get("email", "").lower() for entry in data.get("entries", [])]
        assert "admin@risedual.ai" not in waitlist_emails, "Admin should NOT be in waitlist"
        print(f"✓ Admin email not in waitlist (checked {len(waitlist_emails)} entries)")
    
    def test_owner_not_in_waitlist(self):
        """Owner email should not be in waitlist"""
        # Get admin token first
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        assert login_resp.status_code == 200
        cookies = login_resp.cookies
        
        # Check waitlist admin list
        response = requests.get(f"{BASE_URL}/api/waitlist/admin/list?limit=100", cookies=cookies)
        assert response.status_code == 200
        data = response.json()
        
        # Check that owner email is not in waitlist
        waitlist_emails = [entry.get("email", "").lower() for entry in data.get("entries", [])]
        assert "managingdirector@redslateholdings.com" not in waitlist_emails, "Owner should NOT be in waitlist"
        print(f"✓ Owner email not in waitlist (checked {len(waitlist_emails)} entries)")


class TestWaitlistJoinStillWorks:
    """Test that regular users can still join the waitlist"""
    
    def test_waitlist_join_new_user(self):
        """Regular users should be able to join waitlist"""
        import time
        test_email = f"test_user_{int(time.time())}@example.com"
        
        response = requests.post(f"{BASE_URL}/api/waitlist/join", json={
            "email": test_email,
            "name": "Test User"
        })
        assert response.status_code == 200, f"Waitlist join failed: {response.text}"
        data = response.json()
        assert "referral_code" in data or "position" in data, f"Expected referral_code or position in response: {data}"
        print(f"✓ Waitlist join works for new user: {test_email}")
    
    def test_waitlist_stats_endpoint(self):
        """Waitlist stats endpoint should work"""
        response = requests.get(f"{BASE_URL}/api/waitlist/stats")
        assert response.status_code == 200, f"Waitlist stats failed: {response.text}"
        data = response.json()
        assert "total" in data or "count" in data or "waiting" in data, f"Expected stats in response: {data}"
        print(f"✓ Waitlist stats endpoint works: {data}")


class TestAPIHealth:
    """Basic API health checks"""
    
    def test_api_root(self):
        """API root should return ready message"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "RISEDUAL" in data.get("message", "")
        print(f"✓ API root healthy: {data.get('message')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
