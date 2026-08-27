"""
Iteration 28: Strategy Marketplace Feature Tests
Tests: publish, list, get, clone endpoints
"""
import pytest
import requests
import os
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD


class TestMarketplaceAuth:
    """Test authentication for marketplace operations"""
    
    def test_login_owner_returns_access_token(self):
        """POST /api/auth/login with owner credentials returns access_token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "Response missing access_token"
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0


class TestMarketplaceList:
    """Test marketplace list endpoint (public, no auth required)"""
    
    def test_list_marketplace_public_no_auth(self):
        """GET /api/marketplace/list returns list without auth"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        assert "strategies" in data
        assert "total" in data
        assert isinstance(data["strategies"], list)
        assert isinstance(data["total"], int)
    
    def test_list_marketplace_sort_win_rate(self):
        """GET /api/marketplace/list?sort=win_rate returns strategies sorted by highest win_rate first"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list?sort=win_rate")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        strategies = data["strategies"]
        if len(strategies) >= 2:
            # Verify descending order by win_rate
            for i in range(len(strategies) - 1):
                wr1 = strategies[i].get("backtest", {}).get("metrics", {}).get("win_rate", 0)
                wr2 = strategies[i+1].get("backtest", {}).get("metrics", {}).get("win_rate", 0)
                assert wr1 >= wr2, f"Not sorted by win_rate: {wr1} < {wr2}"
    
    def test_list_marketplace_sort_newest(self):
        """GET /api/marketplace/list?sort=newest returns strategies sorted by newest first"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list?sort=newest")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        strategies = data["strategies"]
        if len(strategies) >= 2:
            # Verify descending order by published_at
            for i in range(len(strategies) - 1):
                date1 = strategies[i].get("published_at", "")
                date2 = strategies[i+1].get("published_at", "")
                assert date1 >= date2, f"Not sorted by newest: {date1} < {date2}"
    
    def test_list_marketplace_sort_pnl(self):
        """GET /api/marketplace/list?sort=pnl returns strategies sorted by highest P&L first"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list?sort=pnl")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        strategies = data["strategies"]
        if len(strategies) >= 2:
            # Verify descending order by total_pnl
            for i in range(len(strategies) - 1):
                pnl1 = strategies[i].get("backtest", {}).get("metrics", {}).get("total_pnl", 0)
                pnl2 = strategies[i+1].get("backtest", {}).get("metrics", {}).get("total_pnl", 0)
                assert pnl1 >= pnl2, f"Not sorted by pnl: {pnl1} < {pnl2}"
    
    def test_list_marketplace_sort_clones(self):
        """GET /api/marketplace/list?sort=clones returns strategies sorted by most cloned first"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list?sort=clones")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        strategies = data["strategies"]
        if len(strategies) >= 2:
            # Verify descending order by clones
            for i in range(len(strategies) - 1):
                c1 = strategies[i].get("clones", 0)
                c2 = strategies[i+1].get("clones", 0)
                assert c1 >= c2, f"Not sorted by clones: {c1} < {c2}"
    
    def test_list_marketplace_strategy_fields(self):
        """Verify marketplace strategies have required fields"""
        response = requests.get(f"{BASE_URL}/api/marketplace/list")
        assert response.status_code == 200
        data = response.json()
        if data["total"] > 0:
            strategy = data["strategies"][0]
            # Required fields
            assert "strategy_id" in strategy
            assert "author_name" in strategy
            assert "strategy" in strategy
            assert "description" in strategy
            assert "backtest" in strategy
            assert "views" in strategy
            assert "clones" in strategy
            assert "published_at" in strategy
            # Backtest metrics
            metrics = strategy.get("backtest", {}).get("metrics", {})
            assert "win_rate" in metrics or metrics == {}, "Missing win_rate in metrics"


class TestMarketplaceGetStrategy:
    """Test getting individual strategy details"""
    
    def test_get_strategy_increments_view_count(self):
        """GET /api/marketplace/{strategy_id} returns full details and increments view count"""
        # First get list to find a strategy_id
        list_response = requests.get(f"{BASE_URL}/api/marketplace/list")
        assert list_response.status_code == 200
        data = list_response.json()
        
        if data["total"] == 0:
            pytest.skip("No strategies in marketplace to test")
        
        strategy_id = data["strategies"][0]["strategy_id"]
        
        # Get the strategy to get current view count
        response1 = requests.get(f"{BASE_URL}/api/marketplace/{strategy_id}")
        assert response1.status_code == 200, f"Get strategy failed: {response1.text}"
        initial_views = response1.json()["views"]
        
        # Get the strategy again - view count should increment
        response2 = requests.get(f"{BASE_URL}/api/marketplace/{strategy_id}")
        assert response2.status_code == 200
        strategy = response2.json()
        
        # Verify fields
        assert strategy["strategy_id"] == strategy_id
        assert "strategy" in strategy
        assert "backtest" in strategy
        
        # Verify view count incremented
        assert strategy["views"] == initial_views + 1, f"View count not incremented: {strategy['views']} != {initial_views + 1}"
    
    def test_get_nonexistent_strategy_returns_404(self):
        """GET /api/marketplace/{invalid_id} returns 404"""
        response = requests.get(f"{BASE_URL}/api/marketplace/nonexistent123")
        assert response.status_code == 404


class TestMarketplacePublish:
    """Test publishing strategies to marketplace (Pro only)"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for Pro user (owner)"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip("Could not authenticate")
        return response.json()["access_token"]
    
    def test_publish_strategy_pro_user(self, auth_token):
        """POST /api/marketplace/publish publishes strategy with backtest metrics (Pro user)"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        payload = {
            "strategy": {
                "name": "TEST_Momentum RSI Strategy",
                "summary": "Buy when RSI < 30, sell when RSI > 70",
                "entry_rules": [{"condition": "RSI < 30", "description": "Oversold condition"}],
                "exit_rules": [{"condition": "RSI > 70", "description": "Overbought condition"}],
                "risk_management": {"stop_loss": "2%", "take_profit": "6%"}
            },
            "description": "Test strategy for marketplace testing",
            "backtest_symbol": "AAPL",
            "backtest_years": 3,
            "backtest_metrics": {
                "total_trades": 45,
                "win_rate": 62.5,
                "total_pnl": 12500.50,
                "sharpe_ratio": 1.85,
                "max_drawdown": 3200.00,
                "buy_hold_pnl": 8500.00,
                "avg_holding_days": 12
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/marketplace/publish", json=payload, headers=headers)
        assert response.status_code == 200, f"Publish failed: {response.text}"
        data = response.json()
        assert "strategy_id" in data, "Response missing strategy_id"
        assert "message" in data
        assert data["message"] == "Strategy published to marketplace"
        
        # Verify it appears in list
        list_response = requests.get(f"{BASE_URL}/api/marketplace/list")
        strategies = list_response.json()["strategies"]
        strategy_ids = [s["strategy_id"] for s in strategies]
        assert data["strategy_id"] in strategy_ids, "Published strategy not in list"
    
    def test_publish_requires_auth(self):
        """POST /api/marketplace/publish without auth returns 401"""
        payload = {
            "strategy": {"name": "Test"},
            "description": "Test",
            "backtest_symbol": "AAPL",
            "backtest_years": 3,
            "backtest_metrics": {}
        }
        response = requests.post(f"{BASE_URL}/api/marketplace/publish", json=payload)
        assert response.status_code == 401


class TestMarketplaceClone:
    """Test cloning strategies (Pro only)"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for Pro user (owner)"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip("Could not authenticate")
        return response.json()["access_token"]
    
    def test_clone_strategy_pro_user(self, auth_token):
        """POST /api/marketplace/{strategy_id}/clone clones strategy into user's saved strategies (Pro only)"""
        # Get a strategy to clone
        list_response = requests.get(f"{BASE_URL}/api/marketplace/list")
        data = list_response.json()
        if data["total"] == 0:
            pytest.skip("No strategies to clone")
        
        strategy_id = data["strategies"][0]["strategy_id"]
        initial_clones = data["strategies"][0]["clones"]
        
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.post(f"{BASE_URL}/api/marketplace/{strategy_id}/clone", headers=headers)
        assert response.status_code == 200, f"Clone failed: {response.text}"
        result = response.json()
        assert "message" in result
        assert "name" in result
        
        # Verify clone count incremented
        get_response = requests.get(f"{BASE_URL}/api/marketplace/{strategy_id}")
        assert get_response.json()["clones"] == initial_clones + 1, "Clone count not incremented"
    
    def test_clone_requires_auth(self):
        """POST /api/marketplace/{strategy_id}/clone without auth returns 401"""
        # Get a strategy_id first
        list_response = requests.get(f"{BASE_URL}/api/marketplace/list")
        data = list_response.json()
        if data["total"] == 0:
            pytest.skip("No strategies to test")
        
        strategy_id = data["strategies"][0]["strategy_id"]
        response = requests.post(f"{BASE_URL}/api/marketplace/{strategy_id}/clone")
        assert response.status_code == 401
    
    def test_clone_nonexistent_strategy_returns_404(self, auth_token):
        """POST /api/marketplace/{invalid_id}/clone returns 404"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.post(f"{BASE_URL}/api/marketplace/nonexistent123/clone", headers=headers)
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
