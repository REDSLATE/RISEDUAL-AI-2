"""
Iteration 99: Code Quality Fixes Regression Tests
Tests for:
1. Backend: POST /api/auth/login works
2. Backend: GET /api/sectors/heatmap returns data (sector_service.py fix)
3. Backend: POST /api/chat works
4. Backend: GET /api/market/dark-pool works
5. Backend: GET /api/waitlist/stats works
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestAuthLogin:
    """Test POST /api/auth/login endpoint"""
    
    def test_login_success(self):
        """Test successful admin login"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        # Login returns user data directly (not nested under "user")
        assert "email" in data, "Response should contain email"
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] in ["admin", "owner"]
        print(f"✓ Login successful for {ADMIN_EMAIL}")
    
    def test_login_invalid_credentials(self):
        """Test login with invalid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"},
            timeout=15
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Invalid credentials correctly rejected")


class TestSectorHeatmap:
    """Test GET /api/sectors/heatmap endpoint - verifies sector_service.py import fix"""
    
    def test_heatmap_returns_data(self):
        """Test that sector heatmap returns valid data"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, f"Heatmap failed: {response.text}"
        data = response.json()
        
        # Verify structure
        assert "sectors" in data, "Response should contain sectors array"
        assert "market_summary" in data, "Response should contain market_summary"
        assert "generated_at" in data, "Response should contain generated_at timestamp"
        
        # Verify sectors data
        sectors = data["sectors"]
        assert len(sectors) > 0, "Should have at least one sector"
        
        # Check first sector has expected fields
        first_sector = sectors[0]
        expected_fields = ["symbol", "name", "weight", "change_1d", "change_1w", "change_1m"]
        for field in expected_fields:
            assert field in first_sector, f"Sector should have {field} field"
        
        print(f"✓ Sector heatmap returned {len(sectors)} sectors")
        print(f"  Best sector: {data['market_summary'].get('best_sector', {}).get('name', 'N/A')}")


class TestChatEndpoint:
    """Test POST /api/chat endpoint"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Get auth cookies from login"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15
        )
        if response.status_code == 200:
            return session.cookies
        pytest.skip("Could not authenticate for chat test")
    
    def test_chat_works(self, auth_cookies):
        """Test chat endpoint accepts requests"""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        
        # Chat requires sessionId field
        response = session.post(
            f"{BASE_URL}/api/chat",
            data={"message": "What is AAPL?", "sessionId": "test-session-123"},
            timeout=30
        )
        # Chat may return 200 or stream response
        assert response.status_code in [200, 201], f"Chat failed: {response.status_code} - {response.text}"
        print("✓ Chat endpoint accepts requests")


class TestDarkPoolEndpoint:
    """Test GET /api/dark-pool endpoint"""
    
    def test_dark_pool_returns_data(self):
        """Test dark pool endpoint returns market data"""
        response = requests.get(f"{BASE_URL}/api/dark-pool", timeout=30)
        assert response.status_code == 200, f"Dark pool failed: {response.text}"
        data = response.json()
        
        # Verify structure - endpoint returns dark_pool array
        assert "dark_pool" in data, f"Response should contain dark_pool array: {list(data.keys())}"
        assert len(data["dark_pool"]) > 0, "Should have dark pool data"
        print(f"✓ Dark pool endpoint returns {len(data['dark_pool'])} tickers")


class TestWaitlistStats:
    """Test GET /api/waitlist/stats endpoint"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Get auth cookies from login"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15
        )
        if response.status_code == 200:
            return session.cookies
        pytest.skip("Could not authenticate for waitlist stats test")
    
    def test_waitlist_stats_returns_data(self, auth_cookies):
        """Test waitlist stats endpoint returns data"""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        
        response = session.get(f"{BASE_URL}/api/waitlist/stats", timeout=15)
        assert response.status_code == 200, f"Waitlist stats failed: {response.text}"
        data = response.json()
        
        # Verify structure
        assert "total" in data or "count" in data or "stats" in data, \
            f"Response should contain stats data: {data}"
        print("✓ Waitlist stats endpoint returns data")


class TestCodeQualityFixes:
    """Verify the specific code quality fixes were applied"""
    
    def test_sector_service_import_fix(self):
        """Verify sector_service.py uses proper datetime import (not __import__)"""
        # This is implicitly tested by test_heatmap_returns_data
        # If the import was broken, the endpoint would fail
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, "Sector service import fix verified by successful heatmap call"
        print("✓ sector_service.py import fix verified")
    
    def test_accuracy_endpoint_works(self):
        """Test accuracy endpoint to verify accuracy.py import fix"""
        # Login first
        session = requests.Session()
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15
        )
        if login_resp.status_code != 200:
            pytest.skip("Could not authenticate")
        
        # Test accuracy stats endpoint (requires pro)
        response = session.get(f"{BASE_URL}/api/accuracy/stats", timeout=15)
        # May return 403 if not pro, but should not return 500 (import error)
        assert response.status_code in [200, 403], f"Accuracy endpoint error: {response.status_code} - {response.text}"
        print(f"✓ accuracy.py import fix verified (status: {response.status_code})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
