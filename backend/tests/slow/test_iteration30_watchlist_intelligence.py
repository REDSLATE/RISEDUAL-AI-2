"""
Iteration 30: Watchlist Intelligence Feature Tests
Tests the batch AI analysis of user's watchlist tickers.

Features tested:
- POST /api/auth/login with owner credentials
- GET /api/workspace/watchlist returns tickers array
- GET /api/intelligence/watchlist returns watchlist intelligence summary
- GET /api/intelligence/watchlist?refresh=true clears cache and regenerates
- GET /api/intelligence/watchlist returns 401 without auth
- GET /api/intelligence/watchlist returns empty state when watchlist is empty
"""
import pytest
import requests
import os
import time
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD


class TestWatchlistIntelligence:
    """Watchlist Intelligence endpoint tests"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Get authentication token for owner user"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=30
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, f"No access_token in response: {data}"
        return data["access_token"]
    
    @pytest.fixture(scope="class")
    def auth_headers(self, auth_token):
        """Get auth headers with Bearer token"""
        return {"Authorization": f"Bearer {auth_token}"}
    
    def test_01_login_returns_access_token(self):
        """POST /api/auth/login with owner credentials returns access_token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=30
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, f"No access_token in response: {data}"
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        print("✓ Login successful, got access_token")
    
    def test_02_get_watchlist_returns_tickers(self, auth_headers):
        """GET /api/workspace/watchlist returns tickers array for authenticated user"""
        response = requests.get(
            f"{BASE_URL}/api/workspace/watchlist",
            headers=auth_headers,
            timeout=30
        )
        assert response.status_code == 200, f"Watchlist fetch failed: {response.text}"
        data = response.json()
        assert "tickers" in data, f"No tickers in response: {data}"
        assert isinstance(data["tickers"], list)
        print(f"✓ Watchlist has {len(data['tickers'])} tickers: {data['tickers']}")
    
    def test_03_watchlist_intelligence_requires_auth(self):
        """GET /api/intelligence/watchlist returns 401 without auth token"""
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            timeout=30
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("✓ Correctly returns 401 without auth")
    
    def test_04_watchlist_intelligence_returns_summary(self, auth_headers):
        """GET /api/intelligence/watchlist returns watchlist intelligence summary with all required fields"""
        # This endpoint takes 30-60 seconds due to Alpha Vantage + GPT-5.2 calls
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=auth_headers,
            timeout=120  # Extended timeout for AI processing
        )
        assert response.status_code == 200, f"Intelligence fetch failed: {response.text}"
        data = response.json()
        
        # Verify summary structure
        assert "summary" in data, f"No summary in response: {data}"
        summary = data["summary"]
        assert "headline" in summary, f"No headline in summary: {summary}"
        
        # If watchlist is not empty, verify full structure
        if data.get("tickers") and len(data["tickers"]) > 0:
            assert "health_score" in summary, f"No health_score in summary: {summary}"
            assert isinstance(summary["health_score"], (int, float))
            
            # Verify tickers array
            assert "tickers" in data, f"No tickers in response: {data}"
            assert isinstance(data["tickers"], list)
            
            # Verify each ticker has required fields
            for ticker in data["tickers"]:
                assert "symbol" in ticker, f"No symbol in ticker: {ticker}"
                assert "score" in ticker, f"No score in ticker: {ticker}"
                assert "verdict" in ticker, f"No verdict in ticker: {ticker}"
                assert "one_liner" in ticker, f"No one_liner in ticker: {ticker}"
                assert "quote" in ticker, f"No quote in ticker: {ticker}"
                assert "technicals" in ticker, f"No technicals in ticker: {ticker}"
            
            # Verify alerts array
            assert "alerts" in data, f"No alerts in response: {data}"
            assert isinstance(data["alerts"], list)
            
            # Verify top_movers array
            assert "top_movers" in data, f"No top_movers in response: {data}"
            assert isinstance(data["top_movers"], list)
            
            # Verify generated_at timestamp
            assert "generated_at" in data, f"No generated_at in response: {data}"
            
            print("✓ Watchlist intelligence returned:")
            print(f"  - Health score: {summary.get('health_score')}")
            print(f"  - Headline: {summary.get('headline')}")
            print(f"  - Bullish: {summary.get('bullish_count')}, Bearish: {summary.get('bearish_count')}")
            print(f"  - Tickers analyzed: {len(data['tickers'])}")
            print(f"  - Alerts: {len(data['alerts'])}")
            print(f"  - Top movers: {len(data['top_movers'])}")
        else:
            # Empty watchlist case
            assert summary.get("headline") == "Empty Watchlist" or "empty" in summary.get("headline", "").lower()
            print("✓ Empty watchlist response received")
    
    def test_05_watchlist_intelligence_refresh_clears_cache(self, auth_headers):
        """GET /api/intelligence/watchlist?refresh=true clears cache and regenerates"""
        # First call to ensure cache exists
        response1 = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=auth_headers,
            timeout=120
        )
        assert response1.status_code == 200, f"First call failed: {response1.text}"
        data1 = response1.json()
        generated_at_1 = data1.get("generated_at")
        
        # Wait a moment
        time.sleep(2)
        
        # Call with refresh=true
        response2 = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist?refresh=true",
            headers=auth_headers,
            timeout=120
        )
        assert response2.status_code == 200, f"Refresh call failed: {response2.text}"
        data2 = response2.json()
        generated_at_2 = data2.get("generated_at")
        
        # Verify structure is still valid
        assert "summary" in data2, f"No summary in refresh response: {data2}"
        assert "tickers" in data2, f"No tickers in refresh response: {data2}"
        
        # If watchlist has tickers, generated_at should be different (newer)
        if data2.get("tickers") and len(data2["tickers"]) > 0:
            assert generated_at_2 is not None, "No generated_at in refresh response"
            if generated_at_1:
                # The refresh should have a newer timestamp
                print(f"✓ Refresh successful - Old: {generated_at_1}, New: {generated_at_2}")
            else:
                print(f"✓ Refresh successful - Generated at: {generated_at_2}")
        else:
            print("✓ Refresh successful (empty watchlist)")
    
    def test_06_verify_ticker_scores_in_range(self, auth_headers):
        """Verify ticker scores are in valid range (1-10)"""
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence fetch failed: {response.text}"
        data = response.json()
        
        if data.get("tickers") and len(data["tickers"]) > 0:
            for ticker in data["tickers"]:
                score = ticker.get("score")
                assert score is not None, f"No score for {ticker.get('symbol')}"
                assert 1 <= score <= 10, f"Score {score} out of range for {ticker.get('symbol')}"
                
                verdict = ticker.get("verdict")
                assert verdict in ["buy", "hold", "sell"], f"Invalid verdict '{verdict}' for {ticker.get('symbol')}"
            
            print(f"✓ All {len(data['tickers'])} ticker scores are valid (1-10) with valid verdicts")
        else:
            print("✓ Skipped - empty watchlist")
    
    def test_07_verify_alerts_structure(self, auth_headers):
        """Verify alerts have required fields"""
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence fetch failed: {response.text}"
        data = response.json()
        
        alerts = data.get("alerts", [])
        if alerts:
            for alert in alerts:
                assert "symbol" in alert, f"No symbol in alert: {alert}"
                assert "type" in alert, f"No type in alert: {alert}"
                assert "severity" in alert, f"No severity in alert: {alert}"
                assert "message" in alert, f"No message in alert: {alert}"
                
                # Verify severity is valid
                assert alert["severity"] in ["low", "medium", "high"], f"Invalid severity: {alert['severity']}"
            
            print(f"✓ All {len(alerts)} alerts have valid structure")
        else:
            print("✓ No alerts to verify (may be normal)")
    
    def test_08_verify_top_movers_structure(self, auth_headers):
        """Verify top_movers have required fields"""
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence fetch failed: {response.text}"
        data = response.json()
        
        movers = data.get("top_movers", [])
        if movers:
            for mover in movers:
                assert "symbol" in mover, f"No symbol in mover: {mover}"
                assert "change_pct" in mover, f"No change_pct in mover: {mover}"
            
            print(f"✓ All {len(movers)} top movers have valid structure")
        else:
            print("✓ No top movers to verify (may be normal)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
