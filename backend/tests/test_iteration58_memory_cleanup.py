"""
Iteration 58: Memory Cleanup Feature Tests

Tests the nightly memory cleanup feature that:
- Removes toxic outliers (high-confidence wrong predictions)
- Prunes obsolete data (older than 90 days)
- Scheduled to run at 2 AM UTC via APScheduler
- Manual trigger endpoints for Pro users

NOTE: Per review request, do NOT run cleanup again - just verify endpoints respond
and cleanup history shows 1 run (55 toxic outliers removed: 2974->2919).
"""
import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from test_credentials.md
class TestMemoryCleanupFeature:
    """Memory cleanup endpoint tests (Pro only)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for all tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login_admin(self):
        """Login as admin and return session with cookies"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        return self.session
    
    # ── Memory Stats Tests ──
    
    def test_memory_stats_returns_2919_episodes(self):
        """GET /api/accuracy/memory should show 2919 episodes after cleanup"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200, f"Memory stats failed: {response.text}"
        
        data = response.json()
        assert "total_episodes" in data, "Missing total_episodes field"
        assert data["total_episodes"] == 2919, f"Expected 2919 episodes, got {data['total_episodes']}"
        assert data.get("initialized"), "Memory should be initialized"
        assert data.get("collection_name") == "market_regimes"
        
        # Verify last_cleanup info is present
        assert "last_cleanup" in data, "Missing last_cleanup field"
        if data["last_cleanup"]:
            assert "toxic_removed" in data["last_cleanup"]
            assert "obsolete_removed" in data["last_cleanup"]
            print(f"Last cleanup: {data['last_cleanup']}")
    
    def test_memory_stats_without_auth_returns_403(self):
        """GET /api/accuracy/memory without auth should return 403"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    # ── Cleanup History Tests ──
    
    def test_cleanup_history_returns_runs(self):
        """GET /api/accuracy/memory/cleanup/history should return recent cleanup runs"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory/cleanup/history")
        assert response.status_code == 200, f"Cleanup history failed: {response.text}"
        
        data = response.json()
        assert "runs" in data, "Missing runs field"
        runs = data["runs"]
        
        # Should have at least 1 run from previous cleanup
        assert len(runs) >= 1, f"Expected at least 1 cleanup run, got {len(runs)}"
        
        # Verify first run structure
        first_run = runs[0]
        assert "toxic_removed" in first_run, "Missing toxic_removed in cleanup run"
        assert "obsolete_removed" in first_run, "Missing obsolete_removed in cleanup run"
        assert "total_before" in first_run, "Missing total_before in cleanup run"
        assert "total_after" in first_run, "Missing total_after in cleanup run"
        assert "run_at" in first_run, "Missing run_at timestamp"
        
        print(f"Cleanup history: {len(runs)} runs found")
        print(f"Latest run: toxic_removed={first_run.get('toxic_removed')}, obsolete_removed={first_run.get('obsolete_removed')}")
    
    def test_cleanup_history_without_auth_returns_403(self):
        """GET /api/accuracy/memory/cleanup/history without auth should return 403"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory/cleanup/history")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    # ── Cleanup Endpoint Tests (DO NOT actually run cleanup) ──
    
    def test_cleanup_endpoint_without_auth_returns_403(self):
        """POST /api/accuracy/memory/cleanup without auth should return 401/403"""
        response = requests.post(f"{BASE_URL}/api/accuracy/memory/cleanup")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    # NOTE: We do NOT test POST /api/accuracy/memory/cleanup with auth
    # because cleanup has already been run and we don't want to run it again
    
    # ── Regression Tests ──
    
    def test_regression_fear_greed_endpoint(self):
        """GET /api/sentiment/fear-greed should return fear_greed and vix"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed")
        assert response.status_code == 200, f"Fear-greed failed: {response.text}"
        
        data = response.json()
        assert "fear_greed" in data, "Missing fear_greed field"
        assert "vix" in data, "Missing vix field"
        
        fg = data["fear_greed"]
        assert "value" in fg, "Missing fear_greed.value"
        assert "classification" in fg, "Missing fear_greed.classification"
        
        vix = data["vix"]
        # VIX structure has 'vix' key with the value, not 'value'
        assert "vix" in vix or "value" in vix, "Missing vix value"
        vix_value = vix.get("vix") or vix.get("value")
        
        print(f"Fear-Greed: {fg.get('value')} ({fg.get('classification')}), VIX: {vix_value}")
    
    def test_regression_sectors_heatmap(self):
        """GET /api/sectors/heatmap should return 11 sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Sectors heatmap failed: {response.text}"
        
        data = response.json()
        assert "sectors" in data, "Missing sectors field"
        sectors = data["sectors"]
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        # Verify sector structure - uses change_1d, change_1w, etc.
        first_sector = sectors[0]
        assert "name" in first_sector, "Missing sector name"
        assert "change_1d" in first_sector or "change" in first_sector, "Missing sector change"
        
        print(f"Sectors: {len(sectors)} sectors returned")
    
    def test_regression_stock_quote_aapl(self):
        """GET /api/stocks/quote/AAPL should return price data"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL")
        assert response.status_code == 200, f"Stock quote failed: {response.text}"
        
        data = response.json()
        assert "price" in data or "c" in data, "Missing price field"
        print(f"AAPL quote: {data}")
    
    def test_regression_auth_login(self):
        """POST /api/auth/login should work with valid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        
        data = response.json()
        assert "access_token" in data or "user" in data, "Missing auth response fields"
        print("Auth login: PASS")
    
    def test_regression_auth_login_invalid(self):
        """POST /api/auth/login with invalid credentials should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("Auth login invalid: PASS (401)")


class TestCleanupEndpointStructure:
    """Verify cleanup endpoint exists and has correct structure"""
    
    def test_cleanup_endpoint_exists(self):
        """POST /api/accuracy/memory/cleanup endpoint should exist (returns 401/403 without auth)"""
        response = requests.post(f"{BASE_URL}/api/accuracy/memory/cleanup")
        # Should return 401/403 (auth required), not 404 (not found)
        assert response.status_code != 404, "Cleanup endpoint not found (404)"
        assert response.status_code in [401, 403, 422], f"Unexpected status: {response.status_code}"
        print(f"Cleanup endpoint exists, returns {response.status_code} without auth")
    
    def test_cleanup_history_endpoint_exists(self):
        """GET /api/accuracy/memory/cleanup/history endpoint should exist"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory/cleanup/history")
        assert response.status_code != 404, "Cleanup history endpoint not found (404)"
        assert response.status_code in [401, 403], f"Unexpected status: {response.status_code}"
        print(f"Cleanup history endpoint exists, returns {response.status_code} without auth")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
