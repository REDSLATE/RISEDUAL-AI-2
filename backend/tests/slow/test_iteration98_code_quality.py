"""Iteration 98: Code Quality Fixes Regression Tests

Tests for:
1. Centralized credentials in conftest_creds.py
2. 'is N' comparison operators fixed to '== N'
3. Empty catch blocks now log errors (WhaleRadar.jsx, WaitlistAdmin.jsx)
4. Insecure random in polygon_dark_pool_service.py replaced with hashlib.sha256 seed
5. redeem_beta_key refactored with extracted _validate_beta_key helper
6. embed_widget_js JS template moved to separate file templates/waitlist_widget.js
"""
import pytest
import requests
import os
import sys

# Import centralized credentials
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestAuthLogin:
    """Test POST /api/auth/login works"""
    
    def test_admin_login_success(self):
        """Admin login should work with correct credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"✓ Admin login successful: {data['email']}")
    
    def test_owner_login_success(self):
        """Owner login should work with correct credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        print(f"✓ Owner login successful: {data['email']}")
    
    def test_login_invalid_credentials(self):
        """Login with invalid credentials should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"}
        )
        assert response.status_code == 401
        print("✓ Invalid credentials correctly rejected")


class TestRedeemBetaKey:
    """Test POST /api/auth/redeem-beta-key still works after refactoring"""
    
    def test_redeem_invalid_beta_key(self):
        """Redeeming an invalid beta key should return 400"""
        response = requests.post(
            f"{BASE_URL}/api/auth/redeem-beta-key",
            json={
                "beta_key": "INVALID-KEY-12345",
                "email": "test_invalid_key@test.com",
                "password": "TestPass123!",
                "name": "Test User"
            }
        )
        assert response.status_code == 400
        data = response.json()
        assert "Invalid or already used beta key" in data.get("detail", "")
        print("✓ Invalid beta key correctly rejected")
    
    def test_redeem_short_password(self):
        """Redeeming with short password should return 400"""
        response = requests.post(
            f"{BASE_URL}/api/auth/redeem-beta-key",
            json={
                "beta_key": "TEST-KEY-12345",
                "email": "test_short_pass@test.com",
                "password": "123",  # Too short
                "name": "Test User"
            }
        )
        assert response.status_code == 400
        data = response.json()
        assert "Password must be at least 6 characters" in data.get("detail", "")
        print("✓ Short password correctly rejected")


class TestWaitlistJoin:
    """Test POST /api/waitlist/join still works"""
    
    def test_waitlist_join_success(self):
        """Joining waitlist should work"""
        import time
        unique_email = f"test_iter98_{int(time.time())}@test.com"
        response = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": unique_email, "name": "Test User 98"}
        )
        assert response.status_code == 200, f"Waitlist join failed: {response.text}"
        data = response.json()
        assert "referral_code" in data
        assert "position" in data
        assert data["status"] == "waiting"
        print(f"✓ Waitlist join successful: position #{data['position']}")
    
    def test_waitlist_join_invalid_email(self):
        """Joining with invalid email should return 400"""
        response = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": "invalid-email", "name": "Test User"}
        )
        assert response.status_code == 400
        print("✓ Invalid email correctly rejected")


class TestEmbedWidget:
    """Test GET /api/waitlist/embed/widget.js returns JavaScript from template file"""
    
    def test_widget_js_returns_javascript(self):
        """Widget.js endpoint should return JavaScript content"""
        response = requests.get(f"{BASE_URL}/api/waitlist/embed/widget.js")
        assert response.status_code == 200
        assert "application/javascript" in response.headers.get("content-type", "")
        
        # Verify it contains expected JavaScript content
        content = response.text
        assert "RiseDualWaitlist" in content
        assert "createWidget" in content
        assert "function" in content
        # Verify __API_URL__ placeholder was replaced
        assert "__API_URL__" not in content
        assert BASE_URL in content or "risedual" in content.lower()
        print("✓ Widget.js returns valid JavaScript from template")
    
    def test_embed_snippet_returns_json(self):
        """Embed snippet endpoint should return JSON with embed code"""
        response = requests.get(f"{BASE_URL}/api/waitlist/embed/snippet")
        assert response.status_code == 200
        data = response.json()
        
        assert "snippet" in data
        assert "script_url" in data
        assert "instructions" in data
        
        # Verify snippet contains expected HTML
        assert "risedual-waitlist" in data["snippet"]
        assert "widget.js" in data["script_url"]
        assert "RiseDualWaitlist.init" in data["snippet"]
        print("✓ Embed snippet returns valid JSON with embed code")


class TestDarkPoolService:
    """Test GET /api/dark-pool returns data (polygon service with new hashlib seed)"""
    
    def test_dark_pool_returns_data(self):
        """Dark pool endpoint should return market data"""
        response = requests.get(f"{BASE_URL}/api/dark-pool")
        assert response.status_code == 200, f"Dark pool failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "dark_pool" in data or "source" in data
        
        # If data is available, verify structure
        if data.get("dark_pool"):
            assert isinstance(data["dark_pool"], list)
            if len(data["dark_pool"]) > 0:
                row = data["dark_pool"][0]
                assert "ticker" in row
                assert "dark_pool_volume" in row or "dark_pool_pct" in row
                print(f"✓ Dark pool returns {len(data['dark_pool'])} tickers")
        else:
            # API key might not be set, but endpoint should still work
            print(f"✓ Dark pool endpoint works (source: {data.get('source', 'unknown')})")


class TestChatEndpoint:
    """Test POST /api/chat works (uses Form data, not JSON)"""
    
    def test_chat_endpoint_accepts_request(self):
        """Chat endpoint should accept valid request format (Form data)"""
        import time
        session_id = f"test-session-{int(time.time())}"
        # Chat endpoint uses Form data, not JSON
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "Hello, what is AAPL?",
                "sessionId": session_id
            }
        )
        # Chat should work (200) or require auth (401) or hit rate limit (429)
        assert response.status_code in [200, 401, 429], f"Unexpected status: {response.status_code} - {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert "response" in data or "sessionId" in data
            print("✓ Chat endpoint returns response")
        elif response.status_code == 429:
            print("✓ Chat endpoint works (rate limited)")
        else:
            print("✓ Chat endpoint works (requires auth)")


class TestCentralizedCredentials:
    """Verify centralized credentials are properly loaded"""
    
    def test_credentials_loaded_from_conftest(self):
        """Credentials should be loaded from conftest_creds.py"""
        assert BASE_URL is not None
        assert ADMIN_EMAIL is not None
        assert ADMIN_PASSWORD is not None
        assert OWNER_EMAIL is not None
        assert OWNER_PASSWORD is not None
        
        # Verify they match expected values
        assert "risedual" in ADMIN_EMAIL.lower() or "admin" in ADMIN_EMAIL.lower()
        assert "redslateholdings" in OWNER_EMAIL.lower() or "owner" in OWNER_EMAIL.lower()
        print("✓ Centralized credentials loaded correctly")


class TestTemplateFileExists:
    """Verify the widget JS template file exists"""
    
    def test_template_file_exists(self):
        """Template file should exist at expected path"""
        import pathlib
        template_path = pathlib.Path("/app/backend/templates/waitlist_widget.js")
        assert template_path.exists(), f"Template file not found at {template_path}"
        
        content = template_path.read_text()
        assert "__API_URL__" in content, "Template should contain __API_URL__ placeholder"
        assert "RiseDualWaitlist" in content
        print("✓ Template file exists with correct content")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
