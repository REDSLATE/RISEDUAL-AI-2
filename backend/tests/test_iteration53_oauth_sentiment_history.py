"""
Iteration 53: Broker OAuth 2.0 Infrastructure & Historical Sentiment Tracking Tests
Tests for:
- GET /api/broker/oauth/{broker_id}/status - OAuth availability check
- GET /api/sectors/sentiment/history - Historical sentiment snapshots
- Regression tests for broker connect, connections, sector heatmap, sentiment
"""
import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD
# Test credentials from test_credentials.md
@pytest.fixture(scope="module")
def session():
    """Create a session with cookies for authenticated requests."""
    s = requests.Session()
    # Login to get httpOnly cookies
    response = s.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if response.status_code == 200:
        data = response.json()
        # Also set Bearer token for endpoints that check headers
        token = data.get("access_token") or data.get("token")
        if token:
            s.headers.update({"Authorization": f"Bearer {token}"})
        return s
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


# ============================================================
# OAUTH STATUS ENDPOINT TESTS
# ============================================================

class TestOAuthStatus:
    """Test GET /api/broker/oauth/{broker_id}/status endpoint."""
    
    def test_alpaca_oauth_status_structure(self):
        """GET /api/broker/oauth/alpaca/status should return proper structure."""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/alpaca/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "available" in data, "Response should have 'available' field"
        assert "broker_id" in data, "Response should have 'broker_id' field"
        assert data["broker_id"] == "alpaca"
        
        # Since no ALPACA_OAUTH_CLIENT_ID is configured, available should be False
        assert not data["available"], "OAuth should not be available without client ID"
        assert "configured" in data, "Response should have 'configured' field"
        assert not data["configured"]
        
        print(f"PASS: Alpaca OAuth status: {data}")
    
    def test_kraken_oauth_not_supported(self):
        """GET /api/broker/oauth/kraken/status should return not supported."""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/kraken/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Kraken is not in OAUTH_CONFIGS, so should return not supported
        assert not data["available"]
        assert "reason" in data
        assert "not supported" in data["reason"].lower()
        
        print(f"PASS: Kraken OAuth status: {data}")
    
    def test_unsupported_broker_oauth_status(self):
        """GET /api/broker/oauth/nonexistent/status should return not supported."""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/nonexistent/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert not data["available"]
        assert "reason" in data
        
        print(f"PASS: Nonexistent broker OAuth status: {data}")
    
    def test_schwab_oauth_not_supported(self):
        """GET /api/broker/oauth/schwab/status should return not supported (not in OAUTH_CONFIGS)."""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/schwab/status")
        assert response.status_code == 200
        data = response.json()
        
        assert not data["available"]
        print(f"PASS: Schwab OAuth status: {data}")


# ============================================================
# SENTIMENT HISTORY ENDPOINT TESTS
# ============================================================

class TestSentimentHistory:
    """Test GET /api/sectors/sentiment/history endpoint."""
    
    def test_sentiment_history_structure(self):
        """GET /api/sectors/sentiment/history should return proper structure."""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment/history")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "snapshots_count" in data, "Response should have 'snapshots_count'"
        assert "sector_trends" in data, "Response should have 'sector_trends'"
        assert "latest_rotation_call" in data, "Response should have 'latest_rotation_call'"
        assert "latest_risk_regime" in data, "Response should have 'latest_risk_regime'"
        
        assert isinstance(data["snapshots_count"], int)
        assert isinstance(data["sector_trends"], dict)
        
        print(f"PASS: Sentiment history structure - {data['snapshots_count']} snapshots")
    
    def test_sentiment_history_with_limit(self):
        """GET /api/sectors/sentiment/history?limit=5 should respect limit param."""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment/history?limit=5")
        assert response.status_code == 200
        data = response.json()
        
        # snapshots_count should be <= 5
        assert data["snapshots_count"] <= 5, f"Expected <= 5 snapshots, got {data['snapshots_count']}"
        
        print(f"PASS: Sentiment history with limit=5 - {data['snapshots_count']} snapshots")
    
    def test_sentiment_history_sector_trends_structure(self):
        """Verify sector_trends has proper structure when snapshots exist."""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment/history?limit=20")
        assert response.status_code == 200
        data = response.json()
        
        if data["snapshots_count"] > 0:
            # sector_trends should have sector symbols as keys
            for symbol, trend_data in data["sector_trends"].items():
                assert isinstance(trend_data, list), f"Trend data for {symbol} should be a list"
                if len(trend_data) > 0:
                    point = trend_data[0]
                    assert "timestamp" in point, "Trend point should have timestamp"
                    assert "score" in point, "Trend point should have score"
                    assert "heatmap_value" in point, "Trend point should have heatmap_value"
                    assert "label" in point, "Trend point should have label"
            print(f"PASS: Sector trends structure verified for {len(data['sector_trends'])} sectors")
        else:
            print("PASS: No snapshots yet - structure is valid but empty")


# ============================================================
# BROKER CONNECT REGRESSION TESTS
# ============================================================

class TestBrokerConnectRegression:
    """Regression tests for broker connection endpoints."""
    
    def test_broker_connect_requires_auth(self):
        """POST /api/broker/connect should return 401 without auth."""
        response = requests.post(f"{BASE_URL}/api/broker/connect", json={
            "broker_id": "alpaca",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "paper": True
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/connect requires authentication")
    
    def test_broker_connections_list(self, session):
        """GET /api/broker/connections should return list of connections."""
        response = session.get(f"{BASE_URL}/api/broker/connections")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "connections" in data
        assert isinstance(data["connections"], list)
        
        # Verify no encrypted keys are exposed
        for conn in data["connections"]:
            assert "api_key_enc" not in conn
            assert "api_secret_enc" not in conn
        
        print(f"PASS: /api/broker/connections returns {len(data['connections'])} connections")
    
    def test_broker_connect_invalid_keys(self, session):
        """POST /api/broker/connect with invalid keys should return 400."""
        response = session.post(f"{BASE_URL}/api/broker/connect", json={
            "broker_id": "alpaca",
            "api_key": "INVALID_KEY_12345",
            "api_secret": "INVALID_SECRET_67890",
            "paper": True
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"PASS: Invalid broker keys return 400: {data['detail'][:50]}...")


# ============================================================
# SECTOR HEATMAP REGRESSION TESTS
# ============================================================

class TestSectorHeatmapRegression:
    """Regression tests for sector heatmap endpoint."""
    
    def test_sector_heatmap_returns_11_sectors(self):
        """GET /api/sectors/heatmap should return 11 S&P 500 sectors."""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "sectors" in data
        assert len(data["sectors"]) == 11, f"Expected 11 sectors, got {len(data['sectors'])}"
        
        # Verify expected sector symbols
        symbols = [s["symbol"] for s in data["sectors"]]
        expected = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLRE", "XLB", "XLU"]
        for exp in expected:
            assert exp in symbols, f"Missing sector {exp}"
        
        # Verify each sector has price data
        for sector in data["sectors"]:
            assert "price" in sector, f"Sector {sector['symbol']} missing price"
            assert sector["price"] > 0, f"Sector {sector['symbol']} has invalid price"
        
        print("PASS: Sector heatmap returns 11 sectors with real prices")
    
    def test_sector_heatmap_market_summary(self):
        """GET /api/sectors/heatmap should include market summary."""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200
        data = response.json()
        
        assert "market_summary" in data
        summary = data["market_summary"]
        assert "best_sector" in summary
        assert "worst_sector" in summary
        assert "total_sectors" in summary
        assert summary["total_sectors"] == 11
        
        print(f"PASS: Market summary - Best: {summary['best_sector']['name']}, Worst: {summary['worst_sector']['name']}")


# ============================================================
# SECTOR SENTIMENT REGRESSION TESTS
# ============================================================

class TestSectorSentimentRegression:
    """Regression tests for sector sentiment endpoint (cached version)."""
    
    def test_sector_sentiment_returns_ai_data(self):
        """GET /api/sectors/sentiment should return AI sentiment data (cached)."""
        # Use cached version to avoid slow LLM calls
        # Retry up to 2 times in case of transient 502 errors
        for attempt in range(2):
            response = requests.get(f"{BASE_URL}/api/sectors/sentiment", timeout=60)
            if response.status_code == 200:
                break
            elif response.status_code == 502 and attempt < 1:
                print(f"Got 502, retrying... (attempt {attempt + 1})")
                import time
                time.sleep(2)
                continue
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "sectors" in data, "Response should have 'sectors'"
        assert "rotation_call" in data, "Response should have 'rotation_call'"
        assert "risk_regime" in data, "Response should have 'risk_regime'"
        
        # If we have sector data, verify structure
        if data.get("sectors"):
            for symbol, sentiment in data["sectors"].items():
                assert "score" in sentiment, f"Sector {symbol} missing score"
                assert "heatmap_value" in sentiment, f"Sector {symbol} missing heatmap_value"
                assert "label" in sentiment, f"Sector {symbol} missing label"
        
        print(f"PASS: Sector sentiment returns AI data for {len(data.get('sectors', {}))} sectors")


# ============================================================
# AUTH LOGIN REGRESSION TEST
# ============================================================

class TestAuthRegression:
    """Regression test for authentication."""
    
    def test_auth_login_returns_token(self):
        """POST /api/auth/login should return access token and user data."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "access_token" in data or "token" in data, "Response should have token"
        # User data may be nested under "user" or at root level
        user_email = data.get("user", {}).get("email") or data.get("email")
        assert user_email == ADMIN_EMAIL, f"Expected email {ADMIN_EMAIL}, got {user_email}"
        
        # Check cookies are set
        cookies = response.cookies
        assert "access_token" in cookies or len(cookies) > 0, "Should set httpOnly cookies"
        
        print(f"PASS: Auth login returns token and user data for {user_email}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
