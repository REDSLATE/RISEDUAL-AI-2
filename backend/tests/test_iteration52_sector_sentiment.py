"""
Iteration 52: Sector Sentiment AI Multi-Agent Crew Testing
Tests:
- GET /api/sectors/sentiment - NEW AI sentiment endpoint (multi-agent crew)
- GET /api/sectors/heatmap - Regression check for price data
- GET /api/stocks/quote/AAPL - Regression check for price_provider
- GET /api/crypto/prices - Regression check for crypto yfinance fallback
"""

import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

class TestSectorSentimentCrew:
    """Test the NEW AI-powered sector sentiment multi-agent crew endpoint"""
    
    def test_sector_sentiment_endpoint_returns_200(self):
        """GET /api/sectors/sentiment should return 200 with AI sentiment data"""
        # This endpoint is SLOW (20-40s) on first call due to 4 LLM calls
        # Subsequent calls return from cache (15min TTL)
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment", timeout=90)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        print(f"Sentiment response keys: {list(data.keys())}")
        
        # Verify required fields in response
        assert "sectors" in data, "Response missing 'sectors' field"
        assert "rotation_call" in data, "Response missing 'rotation_call' field"
        assert "risk_regime" in data, "Response missing 'risk_regime' field"
        assert "multi_agent" in data, "Response missing 'multi_agent' field"
        assert "agents_used" in data, "Response missing 'agents_used' field"
        assert "agent_analyses" in data, "Response missing 'agent_analyses' field"
        
        # Verify multi_agent == True
        assert data["multi_agent"] == True, f"Expected multi_agent=True, got {data['multi_agent']}"
        
        # Verify agents_used == 4 (3 analysts + 1 strategist)
        assert data["agents_used"] == 4, f"Expected 4 agents, got {data['agents_used']}"
        
        print(f"Multi-agent: {data['multi_agent']}, Agents used: {data['agents_used']}")
        print(f"Risk regime: {data['risk_regime']}")
        print(f"Rotation call: {data.get('rotation_call', 'N/A')[:100]}...")
    
    def test_sector_sentiment_has_all_11_sectors(self):
        """Verify sentiment data includes all 11 S&P 500 sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment", timeout=90)
        assert response.status_code == 200
        
        data = response.json()
        sectors = data.get("sectors", {})
        
        expected_sectors = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLRE", "XLB", "XLU"]
        
        for symbol in expected_sectors:
            assert symbol in sectors, f"Missing sector {symbol} in sentiment data"
            sector_data = sectors[symbol]
            
            # Verify each sector has required fields
            assert "score" in sector_data, f"Sector {symbol} missing 'score'"
            assert "heatmap_value" in sector_data, f"Sector {symbol} missing 'heatmap_value'"
            assert "label" in sector_data, f"Sector {symbol} missing 'label'"
            assert "reasoning" in sector_data, f"Sector {symbol} missing 'reasoning'"
            
            # Verify score is in valid range [-1.0, 1.0]
            score = sector_data["score"]
            assert -1.0 <= score <= 1.0, f"Sector {symbol} score {score} out of range [-1, 1]"
            
            # Verify heatmap_value is in valid range [0, 100]
            heatmap_val = sector_data["heatmap_value"]
            assert 0 <= heatmap_val <= 100, f"Sector {symbol} heatmap_value {heatmap_val} out of range [0, 100]"
            
            print(f"{symbol}: score={score:.2f}, heatmap={heatmap_val:.0f}, label={sector_data['label']}")
        
        print(f"All {len(expected_sectors)} sectors have valid sentiment data")
    
    def test_sector_sentiment_agent_analyses(self):
        """Verify agent_analyses contains output from all 3 analysts"""
        response = requests.get(f"{BASE_URL}/api/sectors/sentiment", timeout=90)
        assert response.status_code == 200
        
        data = response.json()
        agent_analyses = data.get("agent_analyses", [])
        
        assert len(agent_analyses) == 3, f"Expected 3 agent analyses, got {len(agent_analyses)}"
        
        expected_roles = [
            "Technical Sector Analyst",
            "Macro Sector Strategist", 
            "Flow & Sentiment Analyst"
        ]
        
        actual_roles = [a.get("role") for a in agent_analyses]
        
        for role in expected_roles:
            assert role in actual_roles, f"Missing agent role: {role}"
        
        for analysis in agent_analyses:
            assert "role" in analysis, "Agent analysis missing 'role'"
            assert "summary" in analysis, "Agent analysis missing 'summary'"
            assert len(analysis["summary"]) > 50, f"Agent {analysis['role']} summary too short"
            print(f"Agent: {analysis['role']}, Summary length: {len(analysis['summary'])} chars")


class TestSectorHeatmapRegression:
    """Regression tests for GET /api/sectors/heatmap (pre-existing endpoint)"""
    
    def test_sector_heatmap_returns_200(self):
        """GET /api/sectors/heatmap should return 200 with 11 sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "sectors" in data, "Response missing 'sectors' field"
        assert "market_summary" in data, "Response missing 'market_summary' field"
        
        sectors = data["sectors"]
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        
        print(f"Heatmap returned {len(sectors)} sectors")
    
    def test_sector_heatmap_has_price_data(self):
        """Verify each sector has real price data"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        sectors = data["sectors"]
        
        for sector in sectors:
            assert "symbol" in sector, "Sector missing 'symbol'"
            assert "name" in sector, "Sector missing 'name'"
            assert "price" in sector, f"Sector {sector.get('symbol')} missing 'price'"
            assert "change_1d" in sector, f"Sector {sector.get('symbol')} missing 'change_1d'"
            assert "change_1w" in sector, f"Sector {sector.get('symbol')} missing 'change_1w'"
            assert "change_1m" in sector, f"Sector {sector.get('symbol')} missing 'change_1m'"
            assert "change_3m" in sector, f"Sector {sector.get('symbol')} missing 'change_3m'"
            assert "change_ytd" in sector, f"Sector {sector.get('symbol')} missing 'change_ytd'"
            
            # Verify price is a positive number
            assert sector["price"] > 0, f"Sector {sector['symbol']} has invalid price: {sector['price']}"
            
            print(f"{sector['symbol']}: ${sector['price']:.2f}, 1D: {sector['change_1d']:+.2f}%")


class TestStockQuoteRegression:
    """Regression tests for GET /api/stocks/quote/{symbol}"""
    
    def test_aapl_quote_returns_200(self):
        """GET /api/stocks/quote/AAPL should return real price data"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "price" in data, "Response missing 'price'"
        assert "change" in data, "Response missing 'change'"
        assert "changePercent" in data, "Response missing 'changePercent'"
        
        assert data["price"] > 0, f"Invalid AAPL price: {data['price']}"
        print(f"AAPL: ${data['price']:.2f}, Change: {data.get('change', 0):+.2f}")
    
    def test_invalid_symbol_returns_404(self):
        """GET /api/stocks/quote/INVALID should return 404"""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/INVALIDXYZ123", timeout=30)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"


class TestCryptoPricesRegression:
    """Regression tests for GET /api/crypto/prices"""
    
    def test_crypto_prices_returns_200(self):
        """GET /api/crypto/prices should return 200 (may be empty due to rate limits)"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # May return empty array due to Alpha Vantage rate limits
        print(f"Crypto prices returned {len(data)} items")


class TestAuthRegression:
    """Regression tests for authentication"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials should return 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD
            },
            timeout=30
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Response structure: {email, role, subscription, access_token, ...} (flat, no nested 'user')
        assert "email" in data, "Response missing 'email'"
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        
        # Verify access_token is returned
        assert "access_token" in data, "Response missing 'access_token'"
        
        print(f"Admin login successful: {data['email']}, role: {data['role']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
