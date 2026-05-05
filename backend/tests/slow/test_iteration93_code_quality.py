"""
Iteration 93: Code Quality Refactoring Regression Tests
Tests that ai.py chat() refactoring (4 helpers) didn't break functionality.
"""
import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestChatEndpointRefactoring:
    """Test POST /api/chat after ai.py refactoring into 4 helpers."""
    
    def test_chat_basic_message(self):
        """Test basic chat message works after refactoring."""
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={"message": "hello", "sessionId": "test_iter93_basic"}
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        assert "response" in data, "Missing response field"
        assert "sessionId" in data, "Missing sessionId field"
        assert data["sessionId"] == "test_iter93_basic"
        assert len(data["response"]) > 0, "Empty response"
        print(f"PASS: Chat basic message works, response length: {len(data['response'])}")
    
    def test_chat_limit_endpoint(self):
        """Test GET /api/chat/limit returns correct structure."""
        response = requests.get(f"{BASE_URL}/api/chat/limit")
        assert response.status_code == 200
        data = response.json()
        assert "limit" in data
        assert "used" in data
        assert "remaining" in data
        assert "is_pro" in data
        # Free user should have limit of 5
        assert data["limit"] == 5
        print(f"PASS: Chat limit endpoint works: {data}")


class TestAuthEndpoints:
    """Test authentication endpoints."""
    
    def test_admin_login(self):
        """Test admin login works."""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        assert "access_token" in data
        print(f"PASS: Admin login works, role={data['role']}")
        return data["access_token"]
    
    def test_owner_login(self):
        """Test owner login works."""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        data = response.json()
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        print(f"PASS: Owner login works, role={data['role']}")


class TestMarketEndpoints:
    """Test market data endpoints."""
    
    def test_stocks_ticker(self):
        """Test GET /api/stocks/ticker returns data."""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # Check first ticker has required fields
        ticker = data[0]
        assert "symbol" in ticker
        assert "price" in ticker
        print(f"PASS: Stocks ticker returns {len(data)} tickers")
    
    def test_fear_greed(self):
        """Test GET /api/fear-greed returns data."""
        response = requests.get(f"{BASE_URL}/api/fear-greed")
        assert response.status_code == 200
        data = response.json()
        # API returns current, avg_7d, avg_30d fields
        assert "current" in data or "avg_7d" in data
        print("PASS: Fear greed endpoint works")


class TestAdminEndpoints:
    """Test admin-only endpoints."""
    
    @pytest.fixture
    def admin_session(self):
        """Get admin session with cookies."""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return session
    
    def test_security_overview(self, admin_session):
        """Test GET /api/admin/security/overview works for admin."""
        response = admin_session.get(f"{BASE_URL}/api/admin/security/overview")
        assert response.status_code == 200, f"Security overview failed: {response.text}"
        data = response.json()
        assert "failed_logins_24h" in data
        assert "total_users" in data
        assert "pro_users" in data
        print(f"PASS: Security overview works, total_users={data['total_users']}")


class TestChatMemoryEndpoints:
    """Test chat memory endpoints (Pro only)."""
    
    @pytest.fixture
    def pro_session(self):
        """Get pro user session."""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return session
    
    def test_get_memories(self, pro_session):
        """Test GET /api/chat/memory works for pro user."""
        response = pro_session.get(f"{BASE_URL}/api/chat/memory")
        assert response.status_code == 200
        data = response.json()
        assert "memories" in data
        assert "enabled" in data
        assert "count" in data
        print(f"PASS: Get memories works, count={data['count']}")
    
    def test_pin_memory(self, pro_session):
        """Test POST /api/chat/memory/pin works for pro user."""
        response = pro_session.post(
            f"{BASE_URL}/api/chat/memory/pin",
            json={"content": "Test pinned memory from iteration 93"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pinned"]
        assert "memory_id" in data
        assert "pinned_count" in data
        assert data["max"] == 5
        print(f"PASS: Pin memory works, memory_id={data['memory_id']}")


class TestHealthAndBasicEndpoints:
    """Test basic health and status endpoints."""
    
    def test_auth_me_unauthenticated(self):
        """Test GET /api/auth/me returns 401 for unauthenticated user."""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401
        print("PASS: Auth me returns 401 for unauthenticated")
    
    def test_crypto_prices(self):
        """Test crypto prices endpoint."""
        response = requests.get(f"{BASE_URL}/api/crypto/prices")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"PASS: Crypto prices returns {len(data)} items")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
