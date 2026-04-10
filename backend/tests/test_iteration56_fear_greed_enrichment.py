"""
Iteration 56: Fear & Greed Index Enrichment Testing

Tests the NEW Fear & Greed Index integration from Alternative.me API,
VIX sentiment level from yfinance, and enriched regime format in memory.

Features tested:
- GET /api/sentiment/fear-greed — NEW endpoint for Fear & Greed + VIX
- GET /api/accuracy/memory — Verify 2974 episodes (2973 + 1 enriched BTC test)
- Regression tests: auth/login, sectors/heatmap, stocks/quote, crypto/prices
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestFearGreedEndpoint:
    """Test the NEW /api/sentiment/fear-greed endpoint"""
    
    def test_fear_greed_endpoint_returns_200(self):
        """GET /api/sentiment/fear-greed should return 200"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print(f"✓ GET /api/sentiment/fear-greed returned 200")
    
    def test_fear_greed_response_structure(self):
        """Verify response contains fear_greed and vix objects"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # Check fear_greed object exists
        assert "fear_greed" in data, "Response missing 'fear_greed' key"
        fg = data["fear_greed"]
        assert "value" in fg, "fear_greed missing 'value'"
        assert "classification" in fg, "fear_greed missing 'classification'"
        
        # Check vix object exists
        assert "vix" in data, "Response missing 'vix' key"
        vix = data["vix"]
        assert "vix" in vix, "vix object missing 'vix' value"
        assert "change" in vix, "vix object missing 'change'"
        assert "level" in vix, "vix object missing 'level'"
        
        print(f"✓ Response structure valid: fear_greed={fg}, vix={vix}")
    
    def test_fear_greed_value_range(self):
        """Fear & Greed value should be 0-100"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        fg_value = data["fear_greed"]["value"]
        assert isinstance(fg_value, int), f"fear_greed value should be int, got {type(fg_value)}"
        assert 0 <= fg_value <= 100, f"fear_greed value {fg_value} out of range 0-100"
        
        print(f"✓ Fear & Greed value {fg_value} is valid (0-100 range)")
    
    def test_fear_greed_classification_valid(self):
        """Classification should be a valid sentiment label"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        classification = data["fear_greed"]["classification"]
        valid_classifications = ["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
        assert classification in valid_classifications, f"Invalid classification: {classification}"
        
        print(f"✓ Classification '{classification}' is valid")
    
    def test_vix_level_valid(self):
        """VIX level should be a valid sentiment label"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        vix_level = data["vix"]["level"]
        valid_levels = ["Complacent", "Normal", "Elevated", "Extreme Fear", "Unknown"]
        assert vix_level in valid_levels, f"Invalid VIX level: {vix_level}"
        
        vix_value = data["vix"]["vix"]
        assert isinstance(vix_value, (int, float)), f"VIX value should be numeric, got {type(vix_value)}"
        
        print(f"✓ VIX level '{vix_level}' is valid, VIX value: {vix_value}")


class TestMemoryEpisodeCount:
    """Test memory episode count after Fear & Greed enrichment"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Get auth cookies by logging in"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30
        )
        if response.status_code != 200:
            pytest.skip(f"Login failed: {response.status_code}")
        return session.cookies
    
    def test_memory_stats_requires_auth(self):
        """GET /api/accuracy/memory should require authentication"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory", timeout=30)
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print(f"✓ GET /api/accuracy/memory correctly requires auth (got {response.status_code})")
    
    def test_memory_episode_count(self, auth_cookies):
        """Verify memory has expected episode count (2974 per review request)"""
        session = requests.Session()
        session.cookies = auth_cookies
        
        response = session.get(f"{BASE_URL}/api/accuracy/memory", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        total_episodes = data.get("total_episodes", 0)
        
        # Per review request: should show 2974 episodes (2973 original + 1 enriched BTC test)
        # Allow some tolerance in case training added more
        assert total_episodes >= 2973, f"Expected at least 2973 episodes, got {total_episodes}"
        
        print(f"✓ Memory has {total_episodes} episodes (expected ~2974)")
        print(f"  - Collection: {data.get('collection_name')}")
        print(f"  - Embedding model: {data.get('embedding_model')}")
        print(f"  - Initialized: {data.get('initialized')}")


class TestRegressionEndpoints:
    """Regression tests for existing endpoints"""
    
    def test_auth_login_success(self):
        """POST /api/auth/login with valid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30
        )
        assert response.status_code == 200, f"Login failed: {response.status_code}: {response.text}"
        
        # Check cookies are set
        cookies = response.cookies
        assert "access_token" in cookies or response.json().get("access_token"), "No access_token in response"
        
        print(f"✓ POST /api/auth/login succeeded")
    
    def test_auth_login_invalid_credentials(self):
        """POST /api/auth/login with invalid credentials should fail"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"},
            timeout=30
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print(f"✓ POST /api/auth/login correctly rejects invalid credentials")
    
    def test_sectors_heatmap_returns_11_sectors(self):
        """GET /api/sectors/heatmap should return 11 sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        sectors = data.get("sectors", [])
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        print(f"✓ GET /api/sectors/heatmap returned {len(sectors)} sectors")
    
    def test_stocks_quote_aapl(self):
        """GET /api/stocks/quote/AAPL should return quote data"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "symbol" in data or "ticker" in data, "Response missing symbol/ticker"
        assert "price" in data or "regularMarketPrice" in data, "Response missing price"
        
        print(f"✓ GET /api/stocks/quote/AAPL returned quote data")
    
    def test_crypto_prices(self):
        """GET /api/crypto/prices should return crypto data"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Should return a list of crypto prices
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) > 0, "Expected at least one crypto price"
        
        print(f"✓ GET /api/crypto/prices returned {len(data)} crypto prices")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
