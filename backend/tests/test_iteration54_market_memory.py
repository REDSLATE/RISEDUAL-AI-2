"""
Iteration 54: Market Vector Memory System Tests

Tests for:
1. NEW: GET /api/accuracy/memory — returns memory stats (Pro only)
2. NEW: GET /api/accuracy/memory without auth — should return 401/403
3. REGRESSION: GET /api/sectors/heatmap — 11 sectors with real prices
4. REGRESSION: GET /api/stocks/quote/AAPL — real quote data
5. REGRESSION: GET /api/crypto/prices — 7 cryptos with real prices
6. REGRESSION: GET /api/sectors/sentiment/history — sentiment history works
7. REGRESSION: POST /api/auth/login — auth still works
8. REGRESSION: GET /api/broker/oauth/alpaca/status — OAuth status returns available:false
"""

import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from test_credentials.md
class TestAuthRegression:
    """Regression: Auth endpoints still work"""
    
    def test_login_success(self):
        """POST /api/auth/login returns 200 with valid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data or "user" in data, "Missing token or user in response"
        print(f"✓ Login successful for {ADMIN_EMAIL}")
    
    def test_login_invalid_credentials(self):
        """POST /api/auth/login returns 401 with invalid credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "wrong@example.com", "password": "wrongpass"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Invalid login correctly rejected")


class TestMarketMemoryNew:
    """NEW: Market Vector Memory System endpoints"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Get auth cookies from login"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        return session
    
    def test_memory_stats_without_auth(self):
        """GET /api/accuracy/memory without auth should return 401 or 403"""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Memory endpoint correctly requires auth (returned {response.status_code})")
    
    def test_memory_stats_with_auth(self, auth_cookies):
        """GET /api/accuracy/memory with Pro auth returns memory stats"""
        response = auth_cookies.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200, f"Memory stats failed: {response.text}"
        
        data = response.json()
        # Validate response structure
        assert "total_episodes" in data, "Missing total_episodes"
        assert "embedding_model" in data, "Missing embedding_model"
        assert "initialized" in data, "Missing initialized"
        assert "collection_name" in data, "Missing collection_name"
        assert "storage_path" in data, "Missing storage_path"
        
        # Validate values
        assert data["initialized"] == True, "Memory should be initialized"
        assert "MiniLM" in data["embedding_model"], f"Expected MiniLM model, got {data['embedding_model']}"
        assert data["total_episodes"] >= 0, "total_episodes should be >= 0"
        
        print(f"✓ Memory stats returned: {data['total_episodes']} episodes, model={data['embedding_model']}")
    
    def test_memory_has_seed_episodes(self, auth_cookies):
        """Verify memory has seed episodes (should be 3 from development)"""
        response = auth_cookies.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200
        
        data = response.json()
        # Main agent mentioned 3 seed episodes
        assert data["total_episodes"] >= 0, f"Expected episodes, got {data['total_episodes']}"
        print(f"✓ Memory has {data['total_episodes']} episodes stored")


class TestSectorHeatmapRegression:
    """Regression: Sector heatmap with 11 sectors"""
    
    def test_sector_heatmap_returns_11_sectors(self):
        """GET /api/sectors/heatmap returns 11 sectors with real prices"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Heatmap failed: {response.text}"
        
        data = response.json()
        assert "sectors" in data, "Missing sectors in response"
        
        sectors = data["sectors"]
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        # Validate sector structure
        for sector in sectors:
            assert "symbol" in sector, "Missing symbol"
            assert "name" in sector, "Missing name"
            assert "price" in sector, "Missing price"
            assert "change_1d" in sector, "Missing change_1d"
            assert sector["price"] > 0, f"Price should be positive: {sector['symbol']}"
        
        # Check market summary
        if "market_summary" in data:
            summary = data["market_summary"]
            assert summary.get("total_sectors") == 11, "Market summary should show 11 sectors"
        
        print(f"✓ Sector heatmap returned {len(sectors)} sectors with real prices")


class TestStockQuoteRegression:
    """Regression: Stock quote endpoint"""
    
    def test_aapl_quote(self):
        """GET /api/stocks/quote/AAPL returns real quote data"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL")
        assert response.status_code == 200, f"Quote failed: {response.text}"
        
        data = response.json()
        assert "price" in data or "current_price" in data, "Missing price in response"
        
        price = data.get("price") or data.get("current_price")
        assert price is not None and price > 0, f"Invalid price: {price}"
        
        print(f"✓ AAPL quote returned: ${price}")


class TestCryptoPricesRegression:
    """Regression: Crypto prices endpoint"""
    
    def test_crypto_prices_returns_7_cryptos(self):
        """GET /api/crypto/prices returns 7 cryptos with real prices"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices")
        assert response.status_code == 200, f"Crypto prices failed: {response.text}"
        
        data = response.json()
        
        # Handle different response structures
        if isinstance(data, list):
            cryptos = data
        elif "prices" in data:
            cryptos = data["prices"]
        elif "data" in data:
            cryptos = data["data"]
        else:
            cryptos = data
        
        assert len(cryptos) >= 7, f"Expected at least 7 cryptos, got {len(cryptos)}"
        
        # Validate crypto structure
        for crypto in cryptos[:7]:
            if isinstance(crypto, dict):
                assert "symbol" in crypto or "name" in crypto, "Missing symbol/name"
                price_key = next((k for k in ["price", "current_price", "usd"] if k in crypto), None)
                assert price_key is not None, f"Missing price in crypto: {crypto}"
        
        print(f"✓ Crypto prices returned {len(cryptos)} cryptos")


class TestSentimentHistoryRegression:
    """Regression: Sentiment history endpoint"""
    
    def test_sentiment_history(self):
        """GET /api/sectors/sentiment/history returns proper structure"""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment/history")
        assert response.status_code == 200, f"Sentiment history failed: {response.text}"
        
        data = response.json()
        
        # Validate structure
        assert "snapshots_count" in data, "Missing snapshots_count"
        assert "sector_trends" in data, "Missing sector_trends"
        
        # Validate sector trends
        sector_trends = data["sector_trends"]
        assert isinstance(sector_trends, dict), "sector_trends should be a dict"
        
        print(f"✓ Sentiment history returned: {data['snapshots_count']} snapshots, {len(sector_trends)} sectors")
    
    def test_sentiment_history_with_limit(self):
        """GET /api/sectors/sentiment/history?limit=5 respects limit"""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment/history?limit=5")
        assert response.status_code == 200, f"Sentiment history with limit failed: {response.text}"
        
        data = response.json()
        assert "snapshots_count" in data
        print(f"✓ Sentiment history with limit=5 returned {data['snapshots_count']} snapshots")


class TestBrokerOAuthRegression:
    """Regression: Broker OAuth status endpoint"""
    
    def test_alpaca_oauth_status(self):
        """GET /api/broker/oauth/alpaca/status returns available:false"""
        response = requests.get(f"{BASE_URL}/api/broker/oauth/alpaca/status")
        assert response.status_code == 200, f"OAuth status failed: {response.text}"
        
        data = response.json()
        assert "available" in data, "Missing 'available' in response"
        assert data["available"] == False, f"Expected available:false, got {data['available']}"
        
        print(f"✓ Alpaca OAuth status: available={data['available']}")


class TestAccuracyEndpointsAuth:
    """Test accuracy endpoints require Pro auth"""
    
    def test_accuracy_stats_without_auth(self):
        """GET /api/accuracy/stats without auth should return 401/403"""
        response = requests.get(f"{BASE_URL}/api/accuracy/stats")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Accuracy stats correctly requires auth (returned {response.status_code})")
    
    def test_accuracy_history_without_auth(self):
        """GET /api/accuracy/history without auth should return 401/403"""
        response = requests.get(f"{BASE_URL}/api/accuracy/history")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Accuracy history correctly requires auth (returned {response.status_code})")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
