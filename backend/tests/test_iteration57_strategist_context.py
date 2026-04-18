"""
Iteration 57: Strategist Context Feature Testing

Tests the NEW 'Strategist Context' feature that queries ChromaDB for ONLY successful
past predictions (outcome='hit') to inject win patterns into AI crew prompts.

Features tested:
- GET /api/sentiment/fear-greed — returns fear_greed and vix data
- GET /api/accuracy/memory — should show ~2974 episodes, initialized=true
- GET /api/sectors/heatmap — regression: 11 sectors with real prices
- GET /api/stocks/quote/AAPL — regression
- GET /api/crypto/prices — regression: 7 cryptos
- POST /api/auth/login — regression
- GET /api/broker/oauth/alpaca/status — regression

Code review:
- market_memory_service.py: get_strategist_context() filters for outcome='hit' only
- crew_definitions.py: War Room, Hypothesis, Prediction crews inject win_context/strategist_context
- market_prediction_service.py: analyze_market() fetches both memory_context and strategist_context
"""

import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from test_credentials.md
class TestFearGreedEndpoint:
    """Test GET /api/sentiment/fear-greed endpoint"""
    
    def test_fear_greed_endpoint_returns_200(self):
        """GET /api/sentiment/fear-greed should return 200"""
        response = requests.get(f"{BASE_URL}/api/sentiment/fear-greed", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/sentiment/fear-greed returned 200")
    
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
        assert "level" in vix, "vix object missing 'level'"
        
        print(f"✓ Response structure valid: fear_greed value={fg.get('value')}, vix={vix.get('vix')}")


class TestMemoryStats:
    """Test GET /api/accuracy/memory endpoint"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session by logging in"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30
        )
        if response.status_code != 200:
            pytest.skip(f"Login failed: {response.status_code}")
        return session
    
    def test_memory_stats_requires_auth(self):
        """GET /api/accuracy/memory should require authentication"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory", timeout=30)
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print(f"✓ GET /api/accuracy/memory correctly requires auth (got {response.status_code})")
    
    def test_memory_episode_count_and_initialized(self, auth_session):
        """Verify memory has ~2974 episodes and initialized=true"""
        response = auth_session.get(f"{BASE_URL}/api/accuracy/memory", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        total_episodes = data.get("total_episodes", 0)
        initialized = data.get("initialized", False)
        
        # Per review request: should show ~2974 episodes
        assert total_episodes >= 2970, f"Expected at least 2970 episodes, got {total_episodes}"
        assert initialized == True, f"Expected initialized=true, got {initialized}"
        
        print(f"✓ Memory has {total_episodes} episodes, initialized={initialized}")
        print(f"  - Collection: {data.get('collection_name')}")
        print(f"  - Embedding model: {data.get('embedding_model')}")


class TestSectorsHeatmap:
    """Test GET /api/sectors/heatmap endpoint"""
    
    def test_sectors_heatmap_returns_200(self):
        """GET /api/sectors/heatmap should return 200"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/sectors/heatmap returned 200")
    
    def test_sectors_heatmap_returns_11_sectors(self):
        """GET /api/sectors/heatmap should return 11 sectors with real prices"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        sectors = data.get("sectors", [])
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        # Verify each sector has required fields
        for sector in sectors:
            assert "symbol" in sector, f"Sector missing 'symbol': {sector}"
            assert "price" in sector, f"Sector missing 'price': {sector}"
            assert sector["price"] > 0, f"Sector price should be > 0: {sector}"
        
        print(f"✓ GET /api/sectors/heatmap returned {len(sectors)} sectors with real prices")
        for s in sectors[:3]:
            print(f"  - {s.get('symbol')}: ${s.get('price', 0):.2f}")


class TestStocksQuote:
    """Test GET /api/stocks/quote/AAPL endpoint"""
    
    def test_stocks_quote_aapl_returns_200(self):
        """GET /api/stocks/quote/AAPL should return 200"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/stocks/quote/AAPL returned 200")
    
    def test_stocks_quote_aapl_has_price(self):
        """GET /api/stocks/quote/AAPL should return price data"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        # Check for price field (could be 'price' or 'regularMarketPrice')
        price = data.get("price") or data.get("regularMarketPrice")
        assert price is not None, f"Response missing price: {data}"
        assert price > 0, f"Price should be > 0, got {price}"
        
        print(f"✓ AAPL quote: ${price:.2f}")


class TestCryptoPrices:
    """Test GET /api/crypto/prices endpoint"""
    
    def test_crypto_prices_returns_200(self):
        """GET /api/crypto/prices should return 200"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/crypto/prices returned 200")
    
    def test_crypto_prices_returns_7_cryptos(self):
        """GET /api/crypto/prices should return 7 cryptos"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) >= 7, f"Expected at least 7 cryptos, got {len(data)}"
        
        print(f"✓ GET /api/crypto/prices returned {len(data)} cryptos")
        for crypto in data[:3]:
            print(f"  - {crypto.get('symbol', 'N/A')}: ${crypto.get('price', 0):.2f}")


class TestAuthLogin:
    """Test POST /api/auth/login endpoint"""
    
    def test_auth_login_success(self):
        """POST /api/auth/login with valid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30
        )
        assert response.status_code == 200, f"Login failed: {response.status_code}: {response.text}"
        
        # Check cookies are set (httpOnly cookies)
        cookies = response.cookies
        has_token = "access_token" in cookies or response.json().get("access_token")
        assert has_token, "No access_token in response"
        
        print(f"✓ POST /api/auth/login succeeded for {ADMIN_EMAIL}")
    
    def test_auth_login_invalid_credentials(self):
        """POST /api/auth/login with invalid credentials should fail"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"},
            timeout=30
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ POST /api/auth/login correctly rejects invalid credentials")


class TestBrokerOAuthStatus:
    """Test GET /api/broker/oauth/alpaca/status endpoint"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session by logging in"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30
        )
        if response.status_code != 200:
            pytest.skip(f"Login failed: {response.status_code}")
        return session
    
    def test_broker_oauth_status_returns_200(self):
        """GET /api/broker/oauth/alpaca/status should return 200"""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/alpaca/status", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/broker/oauth/alpaca/status returned 200")
    
    def test_broker_oauth_status_response_structure(self):
        """GET /api/broker/oauth/alpaca/status should return broker status fields"""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/alpaca/status", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        # Response has: available, broker_id, configured
        assert "broker_id" in data, f"Response missing 'broker_id' field: {data}"
        assert data.get("broker_id") == "alpaca", f"Expected broker_id='alpaca', got {data.get('broker_id')}"
        
        # Check for available or configured field
        has_status = "available" in data or "configured" in data or "connected" in data
        assert has_status, f"Response missing status field: {data}"
        
        print(f"✓ GET /api/broker/oauth/alpaca/status: broker_id={data.get('broker_id')}, available={data.get('available')}, configured={data.get('configured')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
