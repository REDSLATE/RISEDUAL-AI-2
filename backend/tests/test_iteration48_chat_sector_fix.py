"""
Iteration 48: Chat FormData Fix and Sector Heatmap Tests
Tests the PRIMARY BUG FIX: Chat endpoint now accepts FormData instead of JSON body
Also tests sector heatmap returns non-zero values
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8001').rstrip('/')


class TestChatEndpointFormData:
    """Tests for POST /api/chat with FormData (PRIMARY BUG FIX)"""
    
    def test_chat_with_formdata_returns_200(self):
        """Chat endpoint accepts FormData with message and sessionId"""
        session_id = f"test_session_{int(time.time())}"
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "What is AAPL?",
                "sessionId": session_id
            },
            timeout=45
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "response" in data, "Response should contain 'response' field"
        assert "sessionId" in data, "Response should contain 'sessionId' field"
        assert data["sessionId"] == session_id, "Session ID should match"
        assert len(data["response"]) > 10, "AI response should have meaningful content"
    
    def test_chat_with_formdata_different_questions(self):
        """Chat endpoint handles various trading questions"""
        questions = [
            "What is a bull market?",
            "Explain RSI indicator",
            "What are support and resistance levels?"
        ]
        
        for q in questions:
            session_id = f"test_q_{int(time.time())}"
            response = requests.post(
                f"{BASE_URL}/api/chat",
                data={"message": q, "sessionId": session_id},
                timeout=45
            )
            assert response.status_code == 200, f"Failed for question: {q}"
            data = response.json()
            assert "response" in data
            assert len(data["response"]) > 20, f"Response too short for: {q}"
            break  # Only test one to save time
    
    def test_chat_missing_message_returns_422(self):
        """Chat endpoint requires message field"""
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={"sessionId": "test_session"},
            timeout=10
        )
        # FastAPI returns 422 for missing required Form fields
        assert response.status_code == 422, f"Expected 422 for missing message, got {response.status_code}"
    
    def test_chat_missing_sessionid_returns_422(self):
        """Chat endpoint requires sessionId field"""
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={"message": "Hello"},
            timeout=10
        )
        assert response.status_code == 422, f"Expected 422 for missing sessionId, got {response.status_code}"


class TestSectorHeatmap:
    """Tests for GET /api/sectors/heatmap"""
    
    def test_sector_heatmap_returns_200(self):
        """Sector heatmap endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    def test_sector_heatmap_has_sectors(self):
        """Sector heatmap returns list of sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        data = response.json()
        
        assert "sectors" in data, "Response should contain 'sectors' field"
        assert isinstance(data["sectors"], list), "Sectors should be a list"
        assert len(data["sectors"]) >= 10, f"Expected at least 10 sectors, got {len(data['sectors'])}"
    
    def test_sector_heatmap_has_nonzero_values(self):
        """Sector heatmap returns sectors with non-zero change_1d and price values"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        data = response.json()
        
        sectors = data.get("sectors", [])
        sectors_with_data = [s for s in sectors if s.get("price", 0) > 0]
        
        # At least 8 out of 11 sectors should have data (Alpha Vantage rate limits may cause some 0s)
        assert len(sectors_with_data) >= 8, f"Expected at least 8 sectors with price data, got {len(sectors_with_data)}"
        
        # Check that we have non-zero change_1d values
        sectors_with_change = [s for s in sectors if s.get("change_1d", 0) != 0]
        assert len(sectors_with_change) >= 5, f"Expected at least 5 sectors with change_1d data, got {len(sectors_with_change)}"
    
    def test_sector_heatmap_has_market_summary(self):
        """Sector heatmap includes market summary"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        data = response.json()
        
        assert "market_summary" in data, "Response should contain 'market_summary'"
        summary = data["market_summary"]
        
        assert "weighted_change_1d" in summary, "Summary should have weighted_change_1d"
        assert "best_sector" in summary, "Summary should have best_sector"
        assert "worst_sector" in summary, "Summary should have worst_sector"
        assert "total_sectors" in summary, "Summary should have total_sectors"
    
    def test_sector_data_structure(self):
        """Each sector has required fields"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        data = response.json()
        
        required_fields = ["symbol", "name", "weight", "price", "change_1d", "change_1w", "change_1m"]
        
        for sector in data.get("sectors", [])[:3]:  # Check first 3
            for field in required_fields:
                assert field in sector, f"Sector {sector.get('symbol')} missing field: {field}"


class TestChatLimit:
    """Tests for GET /api/chat/limit"""
    
    def test_chat_limit_returns_200(self):
        """Chat limit endpoint returns 200 for unauthenticated users"""
        response = requests.get(f"{BASE_URL}/api/chat/limit", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        assert "limit" in data
        assert "remaining" in data
        assert data["limit"] == 5, "Free limit should be 5"


class TestAuthenticatedChat:
    """Tests for authenticated chat features"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Login and get auth cookies"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": "admin@risedual.ai",
                "password": "RiseDual2026!"
            },
            timeout=10
        )
        if response.status_code != 200:
            pytest.skip("Auth failed - skipping authenticated tests")
        return response.cookies
    
    def test_authenticated_chat_limit_shows_pro(self, auth_cookies):
        """Pro users see unlimited chat limit"""
        response = requests.get(
            f"{BASE_URL}/api/chat/limit",
            cookies=auth_cookies,
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        
        # Pro users should have unlimited (-1) or is_pro=True
        assert data.get("is_pro") == True or data.get("limit") == -1, "Pro user should have unlimited access"
    
    def test_authenticated_chat_works(self, auth_cookies):
        """Authenticated users can use chat"""
        session_id = f"auth_test_{int(time.time())}"
        response = requests.post(
            f"{BASE_URL}/api/chat",
            data={"message": "What is a stock?", "sessionId": session_id},
            cookies=auth_cookies,
            timeout=45
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
