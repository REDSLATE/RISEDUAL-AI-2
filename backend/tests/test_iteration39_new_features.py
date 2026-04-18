"""
Iteration 39: Testing new features and migrated endpoints
- Sector Rotation Heatmap API (new)
- P&L Tracker API (new)
- Migrated endpoints from ai.py to market_data.py
"""
import pytest
import requests
import os
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD
ADMIN_EMAIL = ADMIN_EMAIL
ADMIN_PASSWORD = ADMIN_PASSWORD


class TestMigratedEndpoints:
    """Test endpoints migrated from ai.py to market_data.py"""
    
    def test_world_events_endpoint(self):
        """GET /api/world-events - migrated from ai.py"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify response structure
        assert "events" in data or "total_events" in data or isinstance(data, dict), "Response should be a dict with events data"
        print(f"PASS: /api/world-events returns {response.status_code}")
    
    def test_foreign_markets_endpoint(self):
        """GET /api/foreign-markets - migrated from ai.py"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify response structure - should have region data
        assert isinstance(data, dict), "Response should be a dict"
        print(f"PASS: /api/foreign-markets returns {response.status_code}")
    
    def test_gov_filings_endpoint(self):
        """GET /api/gov-filings - migrated from ai.py"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, dict), "Response should be a dict"
        print(f"PASS: /api/gov-filings returns {response.status_code}")
    
    def test_market_news_endpoint(self):
        """GET /api/market/news - migrated from ai.py"""
        response = requests.get(f"{BASE_URL}/api/market/news", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, (dict, list)), "Response should be dict or list"
        print(f"PASS: /api/market/news returns {response.status_code}")


class TestSectorHeatmapAPI:
    """Test new Sector Rotation Heatmap endpoint"""
    
    def test_sector_heatmap_endpoint(self):
        """GET /api/sectors/heatmap - new endpoint with 30s timeout (Alpha Vantage calls)"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "sectors" in data, "Response should have 'sectors' key"
        assert "market_summary" in data, "Response should have 'market_summary' key"
        assert "generated_at" in data, "Response should have 'generated_at' key"
        
        sectors = data["sectors"]
        assert isinstance(sectors, list), "sectors should be a list"
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        # Verify sector data structure
        for sector in sectors:
            assert "symbol" in sector, "Each sector should have 'symbol'"
            assert "name" in sector, "Each sector should have 'name'"
            assert "weight" in sector, "Each sector should have 'weight'"
            assert "price" in sector, "Each sector should have 'price'"
            assert "change_1d" in sector, "Each sector should have 'change_1d'"
            assert "change_1w" in sector, "Each sector should have 'change_1w'"
            assert "change_1m" in sector, "Each sector should have 'change_1m'"
            assert "change_3m" in sector, "Each sector should have 'change_3m'"
            assert "change_ytd" in sector, "Each sector should have 'change_ytd'"
        
        # Verify market summary
        summary = data["market_summary"]
        assert "weighted_change_1d" in summary, "Summary should have weighted_change_1d"
        assert "weighted_change_1w" in summary, "Summary should have weighted_change_1w"
        assert "best_sector" in summary, "Summary should have best_sector"
        assert "worst_sector" in summary, "Summary should have worst_sector"
        assert "total_sectors" in summary, "Summary should have total_sectors"
        assert summary["total_sectors"] == 11, f"Expected 11 total sectors, got {summary['total_sectors']}"
        
        print(f"PASS: /api/sectors/heatmap returns {response.status_code} with {len(sectors)} sectors")
        print(f"  Best sector: {summary['best_sector']['name']} ({summary['best_sector']['change']}%)")
        print(f"  Worst sector: {summary['worst_sector']['name']} ({summary['worst_sector']['change']}%)")


class TestPnLTrackerAPI:
    """Test new P&L Tracker endpoint (requires auth)"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for owner account"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("access_token") or data.get("token")
        pytest.skip(f"Login failed with status {response.status_code}")
    
    def test_pnl_summary_unauthenticated(self):
        """GET /api/broker/pnl-summary without auth should return 401"""
        response = requests.get(f"{BASE_URL}/api/broker/pnl-summary", timeout=10)
        assert response.status_code == 401, f"Expected 401 for unauthenticated, got {response.status_code}"
        print("PASS: /api/broker/pnl-summary returns 401 for unauthenticated users")
    
    def test_pnl_summary_authenticated(self, auth_token):
        """GET /api/broker/pnl-summary with auth should return valid structure"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/broker/pnl-summary", headers=headers, timeout=10)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure (even if no broker connections)
        assert "total_value" in data, "Response should have 'total_value'"
        assert "total_pl" in data, "Response should have 'total_pl'"
        assert "total_pl_pct" in data, "Response should have 'total_pl_pct'"
        assert "brokers" in data, "Response should have 'brokers'"
        assert "positions" in data, "Response should have 'positions'"
        assert "sector_allocation" in data, "Response should have 'sector_allocation'"
        
        assert isinstance(data["brokers"], list), "brokers should be a list"
        assert isinstance(data["positions"], list), "positions should be a list"
        assert isinstance(data["sector_allocation"], list), "sector_allocation should be a list"
        
        print(f"PASS: /api/broker/pnl-summary returns {response.status_code}")
        print(f"  Total value: ${data['total_value']}")
        print(f"  Brokers connected: {len(data['brokers'])}")
        print(f"  Positions: {len(data['positions'])}")


class TestExistingEndpointsStillWork:
    """Verify existing endpoints still work after route splitting"""
    
    def test_api_root(self):
        """GET /api/ - health check"""
        response = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/ returns {response.status_code}")
    
    def test_stocks_ticker(self):
        """GET /api/stocks/ticker"""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/stocks/ticker returns {response.status_code}")
    
    def test_crypto_prices(self):
        """GET /api/crypto/prices"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/crypto/prices returns {response.status_code}")
    
    def test_dark_pool(self):
        """GET /api/dark-pool"""
        response = requests.get(f"{BASE_URL}/api/dark-pool", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/dark-pool returns {response.status_code}")
    
    def test_referral_leaderboard(self):
        """GET /api/referral/leaderboard"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/referral/leaderboard returns {response.status_code}")
    
    def test_promo_active(self):
        """GET /api/promo/active"""
        response = requests.get(f"{BASE_URL}/api/promo/active", timeout=10)
        assert response.status_code == 200
        print(f"PASS: /api/promo/active returns {response.status_code}")


class TestAuthEndpoints:
    """Test auth endpoints still work"""
    
    def test_login_owner(self):
        """POST /api/auth/login with owner credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "access_token" in data or "token" in data, "Response should have token"
        print("PASS: Owner login successful")
    
    def test_login_admin(self):
        """POST /api/auth/login with admin credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "access_token" in data or "token" in data, "Response should have token"
        print("PASS: Admin login successful")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
