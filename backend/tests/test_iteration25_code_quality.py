"""
Iteration 25: Code Quality Refactoring Tests
Tests to verify that code quality refactoring preserved existing functionality:
- Component splitting (AIHypothesis, MarketPrediction, TradeGPTChat)
- AuthContext useCallback/useMemo changes
- Python function refactoring (ai.py, gov_filings_service.py, digest_service.py)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"


class TestAuthEndpoints:
    """Test auth endpoints after AuthContext refactoring"""
    
    def test_login_returns_tokens(self):
        """POST /api/auth/login returns access_token and refresh_token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        
        # Verify token structure
        assert "access_token" in data, "Missing access_token"
        assert "refresh_token" in data, "Missing refresh_token"
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        assert isinstance(data["refresh_token"], str)
        assert len(data["refresh_token"]) > 0
        
        # Verify user data
        assert data.get("email") == OWNER_EMAIL
        assert data.get("role") == "owner"
        assert data.get("subscription_status") == "pro"


class TestHypothesisEndpoint:
    """Test hypothesis endpoint after ai.py refactoring"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Authentication failed")
    
    def test_hypothesis_gpt52_returns_data(self, auth_token):
        """GET /api/hypothesis/AAPL?model=gpt-5.2 returns hypothesis data"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2", headers=headers, timeout=60)
        
        assert response.status_code == 200, f"Hypothesis failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "symbol" in data, "Missing symbol"
        assert data["symbol"] == "AAPL"
        assert "is_pro" in data, "Missing is_pro flag"
        assert data["is_pro"] == True, "Should be pro response"
        
        # Verify hypothesis content
        assert "verdict" in data, "Missing verdict"
        assert data["verdict"] in ["BUY", "SELL", "HOLD"], f"Invalid verdict: {data.get('verdict')}"
        assert "confidence" in data, "Missing confidence"
        assert isinstance(data["confidence"], (int, float))
        
    def test_hypothesis_teaser_for_unauthenticated(self):
        """GET /api/hypothesis/AAPL without auth returns teaser"""
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=30)
        
        assert response.status_code == 200, f"Hypothesis teaser failed: {response.text}"
        data = response.json()
        
        # Verify teaser structure
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "is_pro" in data
        assert data["is_pro"] == False, "Should be teaser (not pro)"
        assert "teaser" in data, "Missing teaser object"
        
        teaser = data["teaser"]
        assert "data_sources_count" in teaser
        assert "world_events_count" in teaser
        assert "congressional_trades_count" in teaser


class TestMarketPredictionEndpoint:
    """Test market prediction endpoint after ai.py refactoring"""
    
    def test_prediction_returns_macro_data(self):
        """GET /api/market/prediction returns prediction with macro_data section"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=60)
        
        assert response.status_code == 200, f"Prediction failed: {response.text}"
        data = response.json()
        
        # Verify prediction structure
        assert "verdict" in data or "summary" in data, "Missing verdict or summary"
        
        # Verify macro_data section exists (from _enrich_prediction_metadata)
        assert "macro_data" in data, "Missing macro_data section"
        macro = data["macro_data"]
        
        # Verify macro_data structure
        assert "world_events" in macro, "Missing world_events in macro_data"
        assert "foreign_markets" in macro, "Missing foreign_markets in macro_data"
        assert "gov_filings" in macro, "Missing gov_filings in macro_data"
        
        # Verify gov_filings structure
        gf = macro["gov_filings"]
        assert "congressional_trades" in gf
        assert "fed_announcements" in gf
        assert "insider_trades" in gf


class TestGovFilingsEndpoint:
    """Test gov-filings endpoint after gov_filings_service.py refactoring"""
    
    def test_gov_filings_returns_source(self):
        """GET /api/gov-filings returns source containing 'finnhub' or 'scraping'"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        
        assert response.status_code == 200, f"Gov filings failed: {response.text}"
        data = response.json()
        
        # Verify source field
        assert "source" in data, "Missing source field"
        source = data["source"]
        assert "finnhub" in source or "scraping" in source, f"Invalid source: {source}"
        
        # Verify congressional_count
        assert "congressional_count" in data, "Missing congressional_count"
        assert data["congressional_count"] >= 0, "congressional_count should be >= 0"
        
        # Verify other counts
        assert "insider_count" in data
        assert "fed_count" in data


class TestWorkspaceEndpoint:
    """Test workspace endpoints after UserWorkspace refactoring"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Authentication failed")
    
    def test_watchlist_returns_tickers_array(self, auth_token):
        """GET /api/workspace/watchlist returns watchlist with tickers array"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{BASE_URL}/api/workspace/watchlist", headers=headers)
        
        assert response.status_code == 200, f"Watchlist failed: {response.text}"
        data = response.json()
        
        # Verify tickers array exists
        assert "tickers" in data, "Missing tickers array"
        assert isinstance(data["tickers"], list), "tickers should be a list"
        
        # Verify no _id field (MongoDB projection)
        assert "_id" not in data, "Response should not contain _id"


class TestChatHistoryEndpoint:
    """Test chat history endpoint after TradeGPTChat refactoring"""
    
    def test_chat_history_no_id(self):
        """GET /api/chat/history/test-session returns messages array without _id"""
        response = requests.get(f"{BASE_URL}/api/chat/history/test-session")
        
        assert response.status_code == 200, f"Chat history failed: {response.text}"
        data = response.json()
        
        # Verify messages array exists
        assert "messages" in data, "Missing messages array"
        assert isinstance(data["messages"], list), "messages should be a list"
        
        # Verify no _id field (MongoDB projection)
        assert "_id" not in data, "Response should not contain _id"


class TestRefactoredHelperFunctions:
    """Test that refactored helper functions work correctly"""
    
    def test_world_events_endpoint(self):
        """GET /api/world-events returns valid data"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=30)
        
        assert response.status_code == 200, f"World events failed: {response.text}"
        data = response.json()
        
        # Verify structure
        assert "total_events" in data or "events" in data
    
    def test_foreign_markets_endpoint(self):
        """GET /api/foreign-markets returns valid data"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=30)
        
        assert response.status_code == 200, f"Foreign markets failed: {response.text}"
        data = response.json()
        
        # Verify structure - should have regional data
        assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
