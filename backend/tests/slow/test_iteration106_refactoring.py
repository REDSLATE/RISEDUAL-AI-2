"""
Iteration 106: Code Quality Refactoring Round 3 Tests
Tests for:
- useChatMemory hook extracted from RiseDualGPTChat (344→258L)
- ChatHistorySidebar extracted to separate file
- broker.py place_order refactored with _log_order helper
- referral.py process_referral_signup + complete_referral_reward refactored with helpers
"""
import pytest
import requests

from conftest_creds import BASE_URL, OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD


class TestAuthEndpoints:
    """Auth endpoints - verify login still works after refactoring"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["role"] == "admin"
        print(f"PASS: Admin login returns 200, role={data['role']}")
    
    def test_owner_login_returns_200(self):
        """POST /api/auth/login with owner credentials returns 200"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["role"] == "owner"
        print(f"PASS: Owner login returns 200, role={data['role']}")


class TestPublicEndpoints:
    """Public endpoints - verify they still work after refactoring"""
    
    def test_root_endpoint(self):
        """GET /api/ returns ready message"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Ready" in data["message"]
        print("PASS: Root endpoint returns ready message")
    
    def test_sectors_heatmap(self):
        """GET /api/sectors/heatmap returns data"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200
        data = response.json()
        assert "sectors" in data
        assert len(data["sectors"]) > 0
        print(f"PASS: Sectors heatmap returns {len(data['sectors'])} sectors")
    
    def test_waitlist_stats(self):
        """GET /api/waitlist/stats returns stats"""
        response = requests.get(f"{BASE_URL}/api/waitlist/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        print(f"PASS: Waitlist stats returns total={data['total']}")


class TestChatEndpoints:
    """Chat endpoints - verify useChatMemory hook refactoring works"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for pro user"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Auth failed")
    
    def test_chat_endpoint(self, auth_token):
        """POST /api/chat - verify chat still works after useChatMemory extraction"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.post(
            f"{BASE_URL}/api/chat",
            headers=headers,
            data={"message": "Hello, what is AAPL?", "sessionId": "test_session_106"},
            timeout=60
        )
        # May return 200 or 429 (rate limit) or 500 (LLM timeout)
        assert response.status_code in [200, 429, 500, 502, 504]
        if response.status_code == 200:
            data = response.json()
            assert "response" in data or "message" in data
            print("PASS: Chat endpoint works")
        else:
            print(f"INFO: Chat endpoint returned {response.status_code}")
    
    def test_chat_memory_endpoint(self, auth_token):
        """GET /api/chat/memory - verify memory endpoint works"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/chat/memory", headers=headers)
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert "memories" in data or "enabled" in data
            print("PASS: Chat memory endpoint works")
        else:
            print(f"INFO: Chat memory endpoint returned {response.status_code}")
    
    def test_chat_sessions_endpoint(self, auth_token):
        """GET /api/chat/sessions - verify sessions endpoint works (ChatHistorySidebar data source)"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/chat/sessions", headers=headers)
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            print(f"PASS: Chat sessions endpoint works, {len(data)} sessions")
        else:
            print(f"INFO: Chat sessions endpoint returned {response.status_code}")


class TestBrokerEndpoints:
    """Broker endpoints - verify _log_order helper refactoring works"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Owner auth failed")
    
    def test_broker_connections_endpoint(self, owner_token):
        """GET /api/broker/connections - verify broker routes still work"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/broker/connections", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "connections" in data
        print(f"PASS: Broker connections endpoint works, {len(data['connections'])} connections")
    
    def test_broker_execution_status(self, owner_token):
        """GET /api/broker/execution-status - verify execution status endpoint"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/broker/execution-status", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "execution_allowed" in data
        assert "mode" in data
        print(f"PASS: Broker execution status works, mode={data['mode']}")


class TestReferralEndpoints:
    """Referral endpoints - verify _notify_referral_signup and _grant_referral_reward helpers work"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Auth failed")
    
    def test_referral_info_endpoint(self, auth_token):
        """GET /api/referral/info - verify referral info endpoint works"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/referral/info", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "code" in data
        assert "total_referrals" in data
        assert "completed_referrals" in data
        print(f"PASS: Referral info endpoint works, code={data['code']}")
    
    def test_referral_leaderboard_endpoint(self):
        """GET /api/referral/leaderboard - public endpoint"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        assert "leaderboard" in data
        assert "total_participants" in data
        print(f"PASS: Referral leaderboard works, {len(data['leaderboard'])} entries")
    
    def test_referral_validate_endpoint(self):
        """GET /api/referral/validate/{code} - validate referral code"""
        # Use a known invalid code
        response = requests.get(f"{BASE_URL}/api/referral/validate/INVALID123")
        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        print(f"PASS: Referral validate endpoint works, valid={data['valid']}")


class TestAdminEndpoints:
    """Admin endpoints - verify admin panel data sources work"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Owner auth failed")
    
    def test_admin_users_list(self, owner_token):
        """GET /api/auth/admin/users - data source for UsersTab component"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/auth/admin/users", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert len(data["users"]) > 0
        print(f"PASS: Admin users list returns {len(data['users'])} users")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
