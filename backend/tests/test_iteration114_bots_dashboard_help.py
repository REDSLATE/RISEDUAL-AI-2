"""
Iteration 114: Bots Dashboard + Help Center Tests
Tests for:
1. POST /api/auth/login with admin returns 200
2. GET /api/bots returns bot list (used by BotsDashboard)
3. PATCH /api/bots/{id}/toggle works from dashboard toggle
4. Verify existing features still work (no regression)
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuthLogin:
    """Test admin login returns 200"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@risedual.ai", "password": "RiseDual2026!"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "access_token" in data, "Response should contain access_token"
        assert data["email"] == "admin@risedual.ai"
        assert data["role"] == "admin"
        print("PASS: Admin login returns 200 with valid token")


class TestBotsEndpoints:
    """Test bots API endpoints used by BotsDashboard"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get auth cookies"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@risedual.ai", "password": "RiseDual2026!"},
            headers={"Content-Type": "application/json"}
        )
        assert login_response.status_code == 200
        self.token = login_response.json().get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_get_bots_returns_list(self):
        """GET /api/bots returns bot list for BotsDashboard"""
        response = self.session.get(f"{BASE_URL}/api/bots")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), "Response should be a list"
        print(f"PASS: GET /api/bots returns list with {len(data)} bots")
        
        # Verify bot structure if bots exist
        if len(data) > 0:
            bot = data[0]
            assert "bot_id" in bot, "Bot should have bot_id"
            assert "name" in bot, "Bot should have name"
            assert "type" in bot, "Bot should have type"
            assert "enabled" in bot, "Bot should have enabled status"
            assert "stats" in bot, "Bot should have stats"
            print(f"PASS: Bot structure is valid - {bot['name']} ({bot['type']})")
    
    def test_toggle_bot_on_off(self):
        """PATCH /api/bots/{id}/toggle works from dashboard toggle"""
        # First create a test bot
        create_response = self.session.post(
            f"{BASE_URL}/api/bots",
            json={
                "name": "TEST_Toggle_Bot",
                "type": "grid",
                "config": {"symbol": "BTC", "upper_price": 75000, "lower_price": 65000, "grid_levels": 5}
            }
        )
        assert create_response.status_code == 200, f"Failed to create test bot: {create_response.text}"
        bot_id = create_response.json()["bot_id"]
        print(f"Created test bot: {bot_id}")
        
        try:
            # Toggle ON
            toggle_on_response = self.session.patch(
                f"{BASE_URL}/api/bots/{bot_id}/toggle",
                json={"enabled": True}
            )
            assert toggle_on_response.status_code == 200, f"Toggle ON failed: {toggle_on_response.text}"
            assert toggle_on_response.json()["enabled"] == True
            print("PASS: Toggle ON works")
            
            # Toggle OFF
            toggle_off_response = self.session.patch(
                f"{BASE_URL}/api/bots/{bot_id}/toggle",
                json={"enabled": False}
            )
            assert toggle_off_response.status_code == 200, f"Toggle OFF failed: {toggle_off_response.text}"
            assert toggle_off_response.json()["enabled"] == False
            print("PASS: Toggle OFF works")
            
        finally:
            # Cleanup - delete test bot
            delete_response = self.session.delete(f"{BASE_URL}/api/bots/{bot_id}")
            assert delete_response.status_code == 200
            print(f"Cleaned up test bot: {bot_id}")


class TestExistingFeatures:
    """Regression tests - verify existing features still work"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get auth cookies"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@risedual.ai", "password": "RiseDual2026!"},
            headers={"Content-Type": "application/json"}
        )
        assert login_response.status_code == 200
        self.token = login_response.json().get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_smart_orders_endpoint(self):
        """Smart orders endpoint works"""
        response = self.session.get(f"{BASE_URL}/api/smart-orders")
        assert response.status_code == 200
        print("PASS: Smart orders endpoint works")
    
    def test_scanner_strategies_endpoint(self):
        """Scanner strategies endpoint works"""
        response = self.session.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200
        data = response.json()
        # Response is an object with 'strategies' key
        assert "strategies" in data or isinstance(data, list)
        strategies = data.get("strategies", data) if isinstance(data, dict) else data
        print(f"PASS: Scanner strategies endpoint works - {len(strategies)} strategies")
    
    def test_paper_portfolio_endpoint(self):
        """Paper portfolio endpoint works"""
        response = self.session.get(f"{BASE_URL}/api/paper/portfolio")
        assert response.status_code == 200
        print("PASS: Paper portfolio endpoint works")
    
    def test_user_profile_endpoint(self):
        """User profile endpoint works"""
        response = self.session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "email" in data
        print("PASS: User profile endpoint works")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
