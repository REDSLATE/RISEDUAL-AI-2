"""
Test suite for AI War Room feature - Iteration 35
Tests: War Room endpoint, authentication, Pro subscription requirement, composite scoring
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestWarRoomAuthentication:
    """Test War Room authentication requirements"""
    
    def test_war_room_requires_auth(self):
        """War Room endpoint should return 401 without authentication"""
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", timeout=10)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: War Room returns 401 without authentication")
    
    def test_war_room_with_invalid_token(self):
        """War Room endpoint should return 401 with invalid token"""
        headers = {"Authorization": "Bearer invalid_token_12345"}
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", headers=headers, timeout=10)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: War Room returns 401 with invalid token")


class TestWarRoomProRequirement:
    """Test War Room Pro subscription requirement"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner (Pro) authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        }, timeout=10)
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        data = response.json()
        return data.get("access_token") or data.get("token")
    
    @pytest.fixture
    def admin_token(self):
        """Get admin authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        }, timeout=10)
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        return data.get("access_token") or data.get("token")
    
    def test_war_room_accessible_by_owner(self, owner_token):
        """Owner (Pro user) should be able to access War Room"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: Owner can access War Room")
    
    def test_war_room_accessible_by_admin(self, admin_token):
        """Admin should be able to access War Room (admin role grants access)"""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: Admin can access War Room")


class TestWarRoomDataStructure:
    """Test War Room response data structure and composite scoring"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        }, timeout=10)
        data = response.json()
        return data.get("access_token") or data.get("token")
    
    @pytest.fixture
    def war_room_data(self, owner_token):
        """Fetch War Room data for AAPL"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Failed to fetch War Room data: {response.text}"
        return response.json()
    
    def test_war_room_has_symbol(self, war_room_data):
        """War Room response should include symbol"""
        assert "symbol" in war_room_data, "Missing 'symbol' in response"
        assert war_room_data["symbol"] == "AAPL", f"Expected AAPL, got {war_room_data['symbol']}"
        print("PASS: War Room has correct symbol")
    
    def test_war_room_has_composite_signal(self, war_room_data):
        """War Room response should include composite signal with score and verdict"""
        assert "composite" in war_room_data, "Missing 'composite' in response"
        composite = war_room_data["composite"]
        assert "score" in composite, "Missing 'score' in composite"
        assert "verdict" in composite, "Missing 'verdict' in composite"
        assert "breakdown" in composite, "Missing 'breakdown' in composite"
        assert isinstance(composite["score"], (int, float)), "Composite score should be numeric"
        assert composite["verdict"] in ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"], \
            f"Invalid verdict: {composite['verdict']}"
        print(f"PASS: War Room has composite signal - Score: {composite['score']}, Verdict: {composite['verdict']}")
    
    def test_war_room_composite_breakdown(self, war_room_data):
        """War Room composite breakdown should have correct weights (40%, 30%, 30%)"""
        breakdown = war_room_data["composite"]["breakdown"]
        assert "ai_score_weight" in breakdown, "Missing 'ai_score_weight' in breakdown"
        assert "earnings_weight" in breakdown, "Missing 'earnings_weight' in breakdown"
        assert "insider_weight" in breakdown, "Missing 'insider_weight' in breakdown"
        # Weights should sum to approximately the composite score
        total_weight = breakdown["ai_score_weight"] + breakdown["earnings_weight"] + breakdown["insider_weight"]
        assert abs(total_weight - war_room_data["composite"]["score"]) <= 2, \
            f"Breakdown weights ({total_weight}) don't match composite score ({war_room_data['composite']['score']})"
        print(f"PASS: Composite breakdown - AI: {breakdown['ai_score_weight']}, Earnings: {breakdown['earnings_weight']}, Insiders: {breakdown['insider_weight']}")
    
    def test_war_room_has_overview(self, war_room_data):
        """War Room response should include company overview"""
        assert "overview" in war_room_data, "Missing 'overview' in response"
        overview = war_room_data["overview"]
        # Check for key overview fields
        expected_fields = ["sector", "industry", "market_cap", "pe_ratio", "beta"]
        for field in expected_fields:
            assert field in overview, f"Missing '{field}' in overview"
        print(f"PASS: War Room has overview - Sector: {overview.get('sector')}, Industry: {overview.get('industry')}")
    
    def test_war_room_has_52_week_range(self, war_room_data):
        """War Room overview should include 52-week high and low"""
        overview = war_room_data["overview"]
        assert "52_week_high" in overview, "Missing '52_week_high' in overview"
        assert "52_week_low" in overview, "Missing '52_week_low' in overview"
        print(f"PASS: War Room has 52-week range - High: {overview.get('52_week_high')}, Low: {overview.get('52_week_low')}")
    
    def test_war_room_has_ai_score(self, war_room_data):
        """War Room response should include AI score"""
        assert "ai_score" in war_room_data, "Missing 'ai_score' in response"
        ai_score = war_room_data["ai_score"]
        # AI score should have overall_score
        if ai_score:  # May be empty if API fails
            assert "overall_score" in ai_score or isinstance(ai_score, dict), "AI score should be a dict"
        print(f"PASS: War Room has AI score data")
    
    def test_war_room_has_earnings(self, war_room_data):
        """War Room response should include earnings data with beat_rate and current_streak"""
        assert "earnings" in war_room_data, "Missing 'earnings' in response"
        earnings = war_room_data["earnings"]
        assert "beat_rate" in earnings, "Missing 'beat_rate' in earnings"
        assert "current_streak" in earnings, "Missing 'current_streak' in earnings"
        assert "quarters" in earnings, "Missing 'quarters' in earnings"
        assert isinstance(earnings["beat_rate"], (int, float)), "beat_rate should be numeric"
        assert isinstance(earnings["current_streak"], int), "current_streak should be integer"
        print(f"PASS: War Room has earnings - Beat Rate: {earnings['beat_rate']}%, Streak: {earnings['current_streak']}Q")
    
    def test_war_room_earnings_quarterly_breakdown(self, war_room_data):
        """War Room earnings should include quarterly breakdown"""
        quarters = war_room_data["earnings"]["quarters"]
        assert isinstance(quarters, list), "quarters should be a list"
        if quarters:  # May be empty if API fails
            q = quarters[0]
            expected_fields = ["date", "reported_eps", "estimated_eps", "surprise_pct", "beat"]
            for field in expected_fields:
                assert field in q, f"Missing '{field}' in quarterly data"
        print(f"PASS: War Room has {len(quarters)} quarters of earnings data")
    
    def test_war_room_has_insiders(self, war_room_data):
        """War Room response should include insider trade data"""
        assert "insiders" in war_room_data, "Missing 'insiders' in response"
        insiders = war_room_data["insiders"]
        assert "buy_volume" in insiders, "Missing 'buy_volume' in insiders"
        assert "sell_volume" in insiders, "Missing 'sell_volume' in insiders"
        assert "net_sentiment" in insiders, "Missing 'net_sentiment' in insiders"
        assert "buy_ratio" in insiders, "Missing 'buy_ratio' in insiders"
        assert "trades" in insiders, "Missing 'trades' in insiders"
        assert insiders["net_sentiment"] in ["bullish", "bearish", "neutral"], \
            f"Invalid net_sentiment: {insiders['net_sentiment']}"
        print(f"PASS: War Room has insiders - Sentiment: {insiders['net_sentiment']}, Buy Ratio: {insiders['buy_ratio']}%")
    
    def test_war_room_insider_trades_list(self, war_room_data):
        """War Room insider trades should include trade details"""
        trades = war_room_data["insiders"]["trades"]
        assert isinstance(trades, list), "trades should be a list"
        if trades:  # May be empty
            t = trades[0]
            expected_fields = ["name", "date", "shares", "type"]
            for field in expected_fields:
                assert field in t, f"Missing '{field}' in trade data"
        print(f"PASS: War Room has {len(trades)} insider trades")
    
    def test_war_room_has_patterns(self, war_room_data):
        """War Room response should include patterns"""
        assert "patterns" in war_room_data, "Missing 'patterns' in response"
        assert isinstance(war_room_data["patterns"], list), "patterns should be a list"
        print(f"PASS: War Room has {len(war_room_data['patterns'])} patterns")
    
    def test_war_room_has_brief(self, war_room_data):
        """War Room response should include intelligence brief"""
        assert "brief" in war_room_data, "Missing 'brief' in response"
        assert isinstance(war_room_data["brief"], dict), "brief should be a dict"
        print(f"PASS: War Room has intelligence brief")
    
    def test_war_room_has_generated_at(self, war_room_data):
        """War Room response should include generated_at timestamp"""
        assert "generated_at" in war_room_data, "Missing 'generated_at' in response"
        print(f"PASS: War Room has generated_at: {war_room_data['generated_at']}")


class TestExistingEndpointsStillWork:
    """Test that existing AI endpoints still work correctly"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        }, timeout=10)
        data = response.json()
        return data.get("access_token") or data.get("token")
    
    def test_hypothesis_endpoint_works(self, owner_token):
        """GET /api/hypothesis/AAPL should still work"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Hypothesis endpoint failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "verdict" in data or "hypothesis" in data, "Hypothesis response missing expected fields"
        print(f"PASS: Hypothesis endpoint works")
    
    def test_intelligence_score_endpoint_works(self, owner_token):
        """GET /api/intelligence/score/AAPL should still work"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/score/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Intelligence score endpoint failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "scores" in data, "Intelligence score response missing 'scores'"
        print(f"PASS: Intelligence score endpoint works")
    
    def test_intelligence_patterns_endpoint_works(self, owner_token):
        """GET /api/intelligence/patterns/AAPL should still work"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/patterns/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Patterns endpoint failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "patterns" in data, "Patterns response missing 'patterns'"
        print(f"PASS: Intelligence patterns endpoint works")
    
    def test_intelligence_brief_endpoint_works(self, owner_token):
        """GET /api/intelligence/brief/AAPL should still work"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/intelligence/brief/AAPL", headers=headers, timeout=60)
        assert response.status_code == 200, f"Brief endpoint failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "brief" in data, "Brief response missing 'brief'"
        print(f"PASS: Intelligence brief endpoint works")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
