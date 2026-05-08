"""
Iteration 122: 4-Tier Credit System Tests
Tests the rebuilt credit system with 4 plans: Free, Starter, Pro, Pro Max
"""
import pytest
import requests
import os
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCreditPlansEndpoint:
    """GET /api/credits/plans - Returns all 4 plans with correct prices, credits, topup rates"""
    
    def test_plans_returns_4_tiers(self):
        """Verify all 4 plans are returned"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        assert response.status_code == 200
        data = response.json()
        assert "plans" in data
        assert len(data["plans"]) == 4
        
        plan_keys = [p["key"] for p in data["plans"]]
        assert "free" in plan_keys
        assert "starter" in plan_keys
        assert "pro" in plan_keys
        assert "pro_max" in plan_keys
    
    def test_free_plan_details(self):
        """Verify Free plan: $0, 50 credits, $15/1K topup"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        data = response.json()
        free_plan = next(p for p in data["plans"] if p["key"] == "free")
        
        assert free_plan["price"] == 0
        assert free_plan["monthly_credits"] == 50
        assert free_plan["topup_per_1000"] == 15.0
        assert free_plan["unlimited_features"] == []
    
    def test_starter_plan_details(self):
        """Verify Starter plan: $19, 3000 credits, $12/1K topup"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        data = response.json()
        starter_plan = next(p for p in data["plans"] if p["key"] == "starter")
        
        assert starter_plan["price"] == 19
        assert starter_plan["monthly_credits"] == 3000
        assert starter_plan["topup_per_1000"] == 12.0
        assert starter_plan["unlimited_features"] == []
    
    def test_pro_plan_details(self):
        """Verify Pro plan: $55, 15000 credits, $8/1K topup, unlimited chat+war_room"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        data = response.json()
        pro_plan = next(p for p in data["plans"] if p["key"] == "pro")
        
        assert pro_plan["price"] == 55
        assert pro_plan["monthly_credits"] == 15000
        assert pro_plan["topup_per_1000"] == 8.0
        assert "chat" in pro_plan["unlimited_features"]
        assert "war_room" in pro_plan["unlimited_features"]
    
    def test_pro_max_plan_details(self):
        """Verify Pro Max plan: $99, 50000 credits, $5/1K topup, unlimited chat+war_room"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        data = response.json()
        pro_max_plan = next(p for p in data["plans"] if p["key"] == "pro_max")
        
        assert pro_max_plan["price"] == 99
        assert pro_max_plan["monthly_credits"] == 50000
        assert pro_max_plan["topup_per_1000"] == 5.0
        assert "chat" in pro_max_plan["unlimited_features"]
        assert "war_room" in pro_max_plan["unlimited_features"]


class TestCreditMatrixEndpoint:
    """GET /api/credits/matrix - Returns full pricing matrix for all plans"""
    
    def test_matrix_returns_actions_and_topups(self):
        """Verify matrix contains actions and topups sections"""
        response = requests.get(f"{BASE_URL}/api/credits/matrix")
        assert response.status_code == 200
        data = response.json()
        
        assert "actions" in data
        assert "topups" in data
    
    def test_matrix_action_costs(self):
        """Verify action costs are correct per plan"""
        response = requests.get(f"{BASE_URL}/api/credits/matrix")
        data = response.json()
        
        # Chat should be unlimited for pro/pro_max
        assert data["actions"]["chat"]["free"] == "1 credits"
        assert data["actions"]["chat"]["starter"] == "1 credits"
        assert data["actions"]["chat"]["pro"] == "Unlimited"
        assert data["actions"]["chat"]["pro_max"] == "Unlimited"
        
        # War room should be unlimited for pro/pro_max
        assert data["actions"]["war_room"]["free"] == "5 credits"
        assert data["actions"]["war_room"]["starter"] == "5 credits"
        assert data["actions"]["war_room"]["pro"] == "Unlimited"
        assert data["actions"]["war_room"]["pro_max"] == "Unlimited"
        
        # Hypothesis costs credits on all plans
        assert data["actions"]["hypothesis"]["free"] == "3 credits"
        assert data["actions"]["hypothesis"]["pro"] == "3 credits"
    
    def test_matrix_topup_rates(self):
        """Verify topup rates per plan"""
        response = requests.get(f"{BASE_URL}/api/credits/matrix")
        data = response.json()
        
        assert data["topups"]["free"]["included"] == 50
        assert data["topups"]["free"]["topup_per_1000"] == 15.0
        
        assert data["topups"]["starter"]["included"] == 3000
        assert data["topups"]["starter"]["topup_per_1000"] == 12.0
        
        assert data["topups"]["pro"]["included"] == 15000
        assert data["topups"]["pro"]["topup_per_1000"] == 8.0
        
        assert data["topups"]["pro_max"]["included"] == 50000
        assert data["topups"]["pro_max"]["topup_per_1000"] == 5.0


class TestAuthenticatedCreditEndpoints:
    """Tests requiring authentication"""
    
    @pytest.fixture(autouse=True)
    def setup_session(self):
        """Login and get session"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        self.user_data = login_response.json()
    
    def test_balance_returns_plan_info(self):
        """GET /api/credits/balance - Returns plan_key, plan_label, monthly_credits, unlimited_features, topup_rate"""
        response = self.session.get(f"{BASE_URL}/api/credits/balance")
        assert response.status_code == 200
        data = response.json()
        
        # Admin users are treated as pro_max
        assert data["plan_key"] == "pro_max"
        assert data["plan_label"] == "Pro Max"
        assert data["monthly_credits"] == 50000
        assert "chat" in data["unlimited_features"]
        assert "war_room" in data["unlimited_features"]
        assert data["topup_rate"] == 5.0
        assert "credits" in data
    
    def test_topups_returns_plan_specific_pricing(self):
        """GET /api/credits/topups - Returns plan-specific top-up packs with correct pricing"""
        response = self.session.get(f"{BASE_URL}/api/credits/topups")
        assert response.status_code == 200
        data = response.json()
        
        assert data["plan_key"] == "pro_max"
        assert "topups" in data
        assert len(data["topups"]) == 4
        
        # Verify topup tiers: 500, 1000, 2500, 5000
        topup_ids = [t["id"] for t in data["topups"]]
        assert "topup_500" in topup_ids
        assert "topup_1000" in topup_ids
        assert "topup_2500" in topup_ids
        assert "topup_5000" in topup_ids
        
        # Verify pro_max pricing ($5/1K)
        topup_1000 = next(t for t in data["topups"] if t["id"] == "topup_1000")
        assert topup_1000["credits"] == 1000
        assert topup_1000["price"] == 5.0  # $5/1K for pro_max
    
    def test_costs_returns_unlimited_for_pro_features(self):
        """GET /api/credits/costs - Returns per-action costs with unlimited flag for Pro features"""
        response = self.session.get(f"{BASE_URL}/api/credits/costs")
        assert response.status_code == 200
        data = response.json()
        
        assert data["plan_key"] == "pro_max"
        assert "costs" in data
        
        # Chat and war_room should be unlimited for pro_max
        assert data["costs"]["chat"]["unlimited"]
        assert data["costs"]["chat"]["cost"] == 0
        assert data["costs"]["war_room"]["unlimited"]
        assert data["costs"]["war_room"]["cost"] == 0
        
        # Other actions should have costs
        assert not data["costs"]["hypothesis"]["unlimited"]
        assert data["costs"]["hypothesis"]["cost"] == 3
        assert data["costs"]["prediction"]["cost"] == 3
        assert data["costs"]["intelligence"]["cost"] == 2
    
    def test_purchase_topup_success(self):
        """POST /api/credits/purchase - Purchases top-up and returns new balance"""
        # Get initial balance
        balance_before = self.session.get(f"{BASE_URL}/api/credits/balance").json()["credits"]
        
        # Purchase topup
        response = self.session.post(
            f"{BASE_URL}/api/credits/purchase",
            json={"topup_id": "topup_500"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"]
        assert data["credits_added"] == 500
        assert data["price"] == 2.5  # $5/1K * 0.5K = $2.5 for pro_max
        assert data["new_balance"] == balance_before + 500
    
    def test_purchase_invalid_topup_fails(self):
        """POST /api/credits/purchase - Invalid topup_id returns 400"""
        response = self.session.post(
            f"{BASE_URL}/api/credits/purchase",
            json={"topup_id": "invalid_topup"}
        )
        assert response.status_code == 400
    
    def test_history_returns_events(self):
        """GET /api/credits/history - Returns usage events"""
        response = self.session.get(f"{BASE_URL}/api/credits/history")
        assert response.status_code == 200
        data = response.json()
        
        assert "events" in data
        # Events should be a list (may be empty or have items)
        assert isinstance(data["events"], list)


class TestUnauthenticatedEndpoints:
    """Test endpoints that don't require auth"""
    
    def test_plans_no_auth_required(self):
        """GET /api/credits/plans works without auth"""
        response = requests.get(f"{BASE_URL}/api/credits/plans")
        assert response.status_code == 200
    
    def test_matrix_no_auth_required(self):
        """GET /api/credits/matrix works without auth"""
        response = requests.get(f"{BASE_URL}/api/credits/matrix")
        assert response.status_code == 200
    
    def test_balance_requires_auth(self):
        """GET /api/credits/balance requires auth"""
        response = requests.get(f"{BASE_URL}/api/credits/balance")
        assert response.status_code == 401
    
    def test_topups_requires_auth(self):
        """GET /api/credits/topups requires auth"""
        response = requests.get(f"{BASE_URL}/api/credits/topups")
        assert response.status_code == 401
    
    def test_purchase_requires_auth(self):
        """POST /api/credits/purchase requires auth"""
        response = requests.post(
            f"{BASE_URL}/api/credits/purchase",
            json={"topup_id": "topup_500"}
        )
        assert response.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
