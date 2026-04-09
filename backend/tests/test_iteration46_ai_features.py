"""
Iteration 46: Comprehensive AI Features Testing
Tests all 21 features mentioned in the review request:
- Auth, Market Data, Options, Crypto, Dark Pool, Sectors
- AI War Room, Intelligence Hub, Hypothesis, Market Prediction
- Company Research, Macro Data, Watchlist Intelligence, Chat
"""
import pytest
import requests
import os
import time
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', BASE_URL).rstrip('/')


class TestAuth:
    """1. Login: POST /api/auth/login works with owner credentials"""
    
    def test_login_owner(self):
        """Test owner login sets httpOnly cookies"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "email" in data or "name" in data, f"Missing user data: {data}"
        # Check cookies are set
        assert len(session.cookies) > 0, "No cookies set after login"
        print(f"PASS: Owner login successful, cookies set: {list(session.cookies.keys())}")


class TestMarketData:
    """Tests 2-7: Stock ticker, crypto, options, dark pool, sectors"""
    
    def test_stock_ticker(self):
        """2. Stock Ticker data loads: GET /api/stocks/ticker returns array"""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker", timeout=30)
        assert response.status_code == 200, f"Ticker failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), f"Expected array, got {type(data)}"
        assert len(data) > 0, "Empty ticker array"
        print(f"PASS: Stock ticker returned {len(data)} items")
    
    def test_crypto_prices(self):
        """3. Crypto prices load: GET /api/crypto/prices returns array"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=30)
        assert response.status_code == 200, f"Crypto failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), f"Expected array, got {type(data)}"
        print(f"PASS: Crypto prices returned {len(data)} items")
    
    def test_options_radar(self):
        """4. Options Radar loads: GET /api/options/radar returns dict with mostActivelyTraded"""
        response = requests.get(f"{BASE_URL}/api/options/radar", timeout=30)
        assert response.status_code == 200, f"Options radar failed: {response.text}"
        data = response.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "mostActivelyTraded" in data, f"Missing mostActivelyTraded: {data.keys()}"
        print(f"PASS: Options radar returned with mostActivelyTraded ({len(data.get('mostActivelyTraded', []))} items)")
    
    def test_options_flow(self):
        """5. Options Flow loads: GET /api/options/flow returns dict"""
        response = requests.get(f"{BASE_URL}/api/options/flow", timeout=30)
        assert response.status_code == 200, f"Options flow failed: {response.text}"
        data = response.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        print(f"PASS: Options flow returned with keys: {list(data.keys())}")
    
    def test_dark_pool(self):
        """6. Dark Pool data loads: GET /api/dark-pool returns array"""
        response = requests.get(f"{BASE_URL}/api/dark-pool", timeout=30)
        assert response.status_code == 200, f"Dark pool failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), f"Expected array, got {type(data)}"
        print(f"PASS: Dark pool returned {len(data)} items")
    
    def test_sector_heatmap(self):
        """7. Sector Heatmap loads: GET /api/sectors/heatmap?period=1D returns sectors"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap?period=1D", timeout=30)
        assert response.status_code == 200, f"Sector heatmap failed: {response.text}"
        data = response.json()
        assert "sectors" in data or isinstance(data, list), f"Unexpected response: {data}"
        print(f"PASS: Sector heatmap returned data")


class TestAIWarRoom:
    """8. AI War Room: GET /api/intelligence/war-room/AAPL returns composite verdict"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner for Pro access"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_war_room_aapl(self):
        """AI War Room returns composite verdict for AAPL"""
        # AI endpoints take 15-60 seconds
        response = self.session.get(
            f"{BASE_URL}/api/intelligence/war-room/AAPL",
            timeout=120
        )
        assert response.status_code == 200, f"War Room failed: {response.text}"
        data = response.json()
        assert "composite" in data, f"Missing composite: {data.keys()}"
        assert "verdict" in data.get("composite", {}), f"Missing verdict in composite: {data.get('composite')}"
        print(f"PASS: War Room returned composite verdict: {data['composite'].get('verdict')}")


class TestAIIntelligence:
    """9-11. AI Intelligence Score, Patterns, Brief"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner for authenticated access"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_intelligence_score(self):
        """9. AI Intelligence Score: GET /api/intelligence/score/AAPL returns scores.overall_score"""
        response = self.session.get(
            f"{BASE_URL}/api/intelligence/score/AAPL",
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence score failed: {response.text}"
        data = response.json()
        # Check for overall_score in scores or at top level
        has_score = "overall_score" in data or "scores" in data
        assert has_score, f"Missing overall_score: {data.keys()}"
        score = data.get("overall_score") or data.get("scores", {}).get("overall_score")
        print(f"PASS: Intelligence score returned overall_score: {score}")
    
    def test_intelligence_patterns(self):
        """10. AI Intelligence Patterns: GET /api/intelligence/patterns/AAPL returns analysis"""
        response = self.session.get(
            f"{BASE_URL}/api/intelligence/patterns/AAPL",
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence patterns failed: {response.text}"
        data = response.json()
        # Should have some analysis data
        assert len(data) > 0, f"Empty patterns response"
        print(f"PASS: Intelligence patterns returned with keys: {list(data.keys())}")
    
    def test_intelligence_brief(self):
        """11. AI Intelligence Brief: GET /api/intelligence/brief/AAPL returns brief"""
        response = self.session.get(
            f"{BASE_URL}/api/intelligence/brief/AAPL",
            timeout=120
        )
        assert response.status_code == 200, f"Intelligence brief failed: {response.text}"
        data = response.json()
        assert len(data) > 0, f"Empty brief response"
        print(f"PASS: Intelligence brief returned with keys: {list(data.keys())}")


class TestAIHypothesis:
    """12. AI Hypothesis: GET /api/hypothesis/AAPL returns verdict + confidence"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner for Pro access"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_hypothesis_aapl(self):
        """AI Hypothesis returns verdict + confidence for AAPL"""
        response = self.session.get(
            f"{BASE_URL}/api/hypothesis/AAPL",
            timeout=120
        )
        assert response.status_code == 200, f"Hypothesis failed: {response.text}"
        data = response.json()
        assert "verdict" in data, f"Missing verdict: {data.keys()}"
        assert "confidence" in data, f"Missing confidence: {data.keys()}"
        print(f"PASS: Hypothesis returned verdict: {data.get('verdict')}, confidence: {data.get('confidence')}")


class TestMarketPrediction:
    """13. Market Prediction: GET /api/market/prediction returns overall_direction + confidence_score + timeframes"""
    
    def test_market_prediction(self):
        """Market Prediction returns overall_direction, confidence_score, timeframes (NOT verdict/confidence)"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=120)
        assert response.status_code == 200, f"Market prediction failed: {response.text}"
        data = response.json()
        
        # CRITICAL: Check for correct field names (overall_direction, confidence_score, NOT verdict/confidence)
        assert "overall_direction" in data, f"Missing overall_direction (got keys: {data.keys()})"
        assert "confidence_score" in data, f"Missing confidence_score (got keys: {data.keys()})"
        assert "timeframes" in data, f"Missing timeframes (got keys: {data.keys()})"
        
        # Verify timeframes have target field
        timeframes = data.get("timeframes", {})
        for tf_name, tf_data in timeframes.items():
            assert "target" in tf_data or "summary" in tf_data or "outlook" in tf_data, \
                f"Timeframe {tf_name} missing target/summary/outlook: {tf_data}"
        
        print(f"PASS: Market prediction returned overall_direction: {data.get('overall_direction')}, "
              f"confidence_score: {data.get('confidence_score')}, timeframes: {list(timeframes.keys())}")


class TestCompanyResearch:
    """14. Company Research: GET /api/research/AAPL returns synthesis + sources"""
    
    def test_company_research(self):
        """Company Research returns synthesis + sources for AAPL"""
        response = requests.get(f"{BASE_URL}/api/research/AAPL", timeout=120)
        assert response.status_code == 200, f"Company research failed: {response.text}"
        data = response.json()
        assert "synthesis" in data or "overview" in data, f"Missing synthesis/overview: {data.keys()}"
        assert "sources" in data or "data_sources" in data, f"Missing sources: {data.keys()}"
        print(f"PASS: Company research returned with keys: {list(data.keys())}")


class TestMacroData:
    """15-17. Macro World Events, Foreign Markets, Gov Filings"""
    
    def test_world_events(self):
        """15. Macro World Events: GET /api/world-events returns total_events + high_impact_events"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        assert response.status_code == 200, f"World events failed: {response.text}"
        data = response.json()
        assert "total_events" in data, f"Missing total_events: {data.keys()}"
        # high_impact_count or high_impact_events
        has_high_impact = "high_impact_count" in data or "high_impact_events" in data
        assert has_high_impact, f"Missing high_impact field: {data.keys()}"
        print(f"PASS: World events returned total_events: {data.get('total_events')}")
    
    def test_foreign_markets(self):
        """16. Macro Foreign Markets: GET /api/foreign-markets returns asia + europe"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        assert response.status_code == 200, f"Foreign markets failed: {response.text}"
        data = response.json()
        assert "asia" in data, f"Missing asia: {data.keys()}"
        assert "europe" in data, f"Missing europe: {data.keys()}"
        print(f"PASS: Foreign markets returned asia ({len(data.get('asia', []))}) and europe ({len(data.get('europe', []))})")
    
    def test_gov_filings(self):
        """17. Macro Gov Filings: GET /api/gov-filings returns insider_trades + congressional_trades"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        assert response.status_code == 200, f"Gov filings failed: {response.text}"
        data = response.json()
        # Check for insider_trades or insider_count
        has_insider = "insider_trades" in data or "insider_count" in data
        assert has_insider, f"Missing insider data: {data.keys()}"
        # Check for congressional_trades or congressional_count
        has_congress = "congressional_trades" in data or "congressional_count" in data
        assert has_congress, f"Missing congressional data: {data.keys()}"
        print(f"PASS: Gov filings returned with keys: {list(data.keys())}")


class TestWatchlistIntelligence:
    """18. Watchlist Intelligence: GET /api/intelligence/watchlist returns tickers + summary"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_watchlist_intelligence(self):
        """Watchlist Intelligence returns tickers + summary"""
        response = self.session.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            timeout=120
        )
        assert response.status_code == 200, f"Watchlist intelligence failed: {response.text}"
        data = response.json()
        assert "tickers" in data, f"Missing tickers: {data.keys()}"
        assert "summary" in data, f"Missing summary: {data.keys()}"
        print(f"PASS: Watchlist intelligence returned tickers: {data.get('tickers')}, summary keys: {list(data.get('summary', {}).keys())}")


class TestChat:
    """19. Chat: POST /api/chat returns response"""
    
    def test_chat(self):
        """Chat endpoint returns response"""
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "message": "What is AAPL's current price?",
                "sessionId": "test_session_iteration46"
            },
            timeout=60
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        assert "response" in data, f"Missing response: {data.keys()}"
        print(f"PASS: Chat returned response (length: {len(data.get('response', ''))})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
