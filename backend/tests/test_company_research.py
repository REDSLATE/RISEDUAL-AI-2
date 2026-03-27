"""
Test Company Research API - Perplexity-style research feature
Tests GET /api/research/{symbol} endpoint
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCompanyResearchAPI:
    """Tests for the Perplexity-style company research endpoint"""
    
    def test_api_root_accessible(self):
        """Verify API is running"""
        response = requests.get(f"{BASE_URL}/api/", timeout=30)
        assert response.status_code == 200
        data = response.json()
        assert "RISEDUALAI" in data.get("message", "")
        print("TEST PASSED: API root accessible")
    
    def test_research_aapl_returns_data(self):
        """Test research endpoint with AAPL ticker"""
        response = requests.get(f"{BASE_URL}/api/research/AAPL", timeout=90)
        assert response.status_code == 200
        data = response.json()
        
        # Verify required fields exist
        assert "symbol" in data, "Missing 'symbol' field"
        assert "company_name" in data, "Missing 'company_name' field"
        assert "overview" in data, "Missing 'overview' field"
        assert "synthesis" in data, "Missing 'synthesis' field"
        assert "sources" in data, "Missing 'sources' field"
        
        # Verify symbol is correct
        assert data["symbol"] == "AAPL"
        # company_name may be just the symbol if Alpha Vantage rate limited
        assert "AAPL" in data["company_name"] or "Apple" in data["company_name"]
        
        # Verify synthesis is a non-empty string
        assert isinstance(data["synthesis"], str)
        assert len(data["synthesis"]) > 50, "Synthesis should be substantial"
        
        # Verify sources is a list
        assert isinstance(data["sources"], list)
        
        print(f"TEST PASSED: AAPL research returned - company: {data['company_name']}, sources: {len(data['sources'])}")
    
    def test_research_msft_returns_data(self):
        """Test research endpoint with MSFT ticker"""
        response = requests.get(f"{BASE_URL}/api/research/MSFT", timeout=90)
        assert response.status_code == 200
        data = response.json()
        
        # Verify required fields
        assert data["symbol"] == "MSFT"
        # company_name may be just the symbol if Alpha Vantage rate limited
        assert "MSFT" in data["company_name"] or "Microsoft" in data["company_name"]
        assert isinstance(data["synthesis"], str)
        assert len(data["synthesis"]) > 50
        
        print(f"TEST PASSED: MSFT research returned - company: {data['company_name']}")
    
    def test_research_response_structure(self):
        """Verify the complete response structure"""
        response = requests.get(f"{BASE_URL}/api/research/GOOGL", timeout=90)
        assert response.status_code == 200
        data = response.json()
        
        # Check all expected top-level fields
        expected_fields = ["symbol", "company_name", "overview", "quote", "synthesis", "sources", "news_count"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        # Verify news_count is an integer
        assert isinstance(data["news_count"], int)
        
        print(f"TEST PASSED: Response structure verified for GOOGL - news_count: {data['news_count']}")
    
    def test_research_overview_financial_metrics(self):
        """Verify overview contains key financial metrics (may be None due to rate limits)"""
        response = requests.get(f"{BASE_URL}/api/research/AAPL", timeout=60)
        assert response.status_code == 200
        data = response.json()
        
        overview = data.get("overview")
        
        # Overview may be None due to Alpha Vantage rate limits (5 calls/min)
        if overview is None:
            print("TEST PASSED: Overview is None (likely due to Alpha Vantage rate limit)")
            return
        
        # Check for key financial metrics
        financial_fields = [
            "market_cap", "pe_ratio", "eps", "sector", "industry",
            "week_52_high", "week_52_low", "dividend_yield", "beta"
        ]
        
        present_fields = [f for f in financial_fields if f in overview]
        assert len(present_fields) >= 5, f"Expected at least 5 financial fields, got {len(present_fields)}"
        
        print(f"TEST PASSED: Overview has {len(present_fields)} financial metrics")
    
    def test_research_sources_structure(self):
        """Verify sources array structure"""
        response = requests.get(f"{BASE_URL}/api/research/NVDA", timeout=90)
        assert response.status_code == 200
        data = response.json()
        
        sources = data.get("sources", [])
        
        # Sources should be a list
        assert isinstance(sources, list)
        
        # If sources exist, verify structure
        if len(sources) > 0:
            source = sources[0]
            assert "number" in source, "Source missing 'number' field"
            assert "title" in source, "Source missing 'title' field"
            assert "source" in source, "Source missing 'source' field"
            print(f"TEST PASSED: Sources structure verified - {len(sources)} sources found")
        else:
            print("TEST PASSED: Sources array is empty (may be due to no relevant news)")
    
    def test_research_lowercase_symbol(self):
        """Test that lowercase symbols work"""
        response = requests.get(f"{BASE_URL}/api/research/tsla", timeout=90)
        assert response.status_code == 200
        data = response.json()
        
        # Symbol should be uppercased in response
        assert data["symbol"] == "TSLA"
        print("TEST PASSED: Lowercase symbol 'tsla' converted to 'TSLA'")


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
