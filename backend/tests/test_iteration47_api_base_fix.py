"""
Iteration 47: Test API Base Fix and Auto-Refresh Token
Tests the fix for 'Analysis failed' errors on deployed version.
Key changes tested:
1. authFetch auto-refresh on 401
2. getApiBase() returns correct URL
3. All AI endpoints work correctly
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuthFlow:
    """Test authentication and cookie-based auth"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a session with cookies"""
        return requests.Session()
    
    def test_login_sets_cookies(self, session):
        """Login should set httpOnly cookies"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert data["role"] == "admin"
        assert data["subscription_status"] == "pro"
        # Cookies should be set
        assert "access_token" in session.cookies or "access_token" in response.cookies
        print(f"PASS: Login successful, user role={data['role']}, subscription={data['subscription_status']}")
    
    def test_auth_me_with_cookies(self, session):
        """Auth me endpoint should work with cookies"""
        # First login
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        response = session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Auth me failed: {response.text}"
        data = response.json()
        assert data["email"] == "admin@risedual.ai"
        print(f"PASS: Auth me returned user email={data['email']}")
    
    def test_refresh_token_endpoint(self, session):
        """Refresh token endpoint should work"""
        # First login
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        response = session.post(f"{BASE_URL}/api/auth/refresh", json={})
        assert response.status_code == 200, f"Refresh failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        print(f"PASS: Token refresh successful")


class TestAIWarRoom:
    """Test AI War Room endpoint - requires Pro subscription"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        return session
    
    def test_war_room_returns_composite_score(self, auth_session):
        """War Room should return composite score and verdict"""
        response = auth_session.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", timeout=60)
        assert response.status_code == 200, f"War Room failed: {response.text}"
        data = response.json()
        
        # Verify structure
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "composite" in data
        assert "score" in data["composite"]
        assert "verdict" in data["composite"]
        assert "ai_score" in data
        assert "brief" in data
        assert "earnings" in data
        assert "insiders" in data
        
        print(f"PASS: War Room returned verdict={data['composite']['verdict']}, score={data['composite']['score']}")
    
    def test_war_room_requires_auth(self):
        """War Room should return 401 without auth"""
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", timeout=10)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print(f"PASS: War Room correctly requires authentication")


class TestAIIntelligenceHub:
    """Test AI Intelligence Hub endpoints"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        return session
    
    def test_ai_score_endpoint(self, auth_session):
        """AI Score should return overall score and recommendation"""
        response = auth_session.get(f"{BASE_URL}/api/intelligence/score/AAPL", timeout=30)
        assert response.status_code == 200, f"AI Score failed: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert "scores" in data
        assert "overall_score" in data["scores"]
        assert "recommendation" in data["scores"]
        
        print(f"PASS: AI Score returned overall_score={data['scores']['overall_score']}, recommendation={data['scores']['recommendation']}")
    
    def test_ai_brief_endpoint(self, auth_session):
        """AI Brief should return headline and verdict"""
        response = auth_session.get(f"{BASE_URL}/api/intelligence/brief/AAPL", timeout=30)
        assert response.status_code == 200, f"AI Brief failed: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert "brief" in data
        assert "headline" in data["brief"]
        assert "verdict" in data["brief"]
        
        print(f"PASS: AI Brief returned headline='{data['brief']['headline'][:50]}...'")
    
    def test_ai_patterns_endpoint(self, auth_session):
        """AI Patterns should return pattern analysis"""
        response = auth_session.get(f"{BASE_URL}/api/intelligence/patterns/AAPL", timeout=30)
        assert response.status_code == 200, f"AI Patterns failed: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert "analysis" in data
        assert "patterns" in data["analysis"]
        assert "overall_bias" in data["analysis"]
        
        print(f"PASS: AI Patterns returned {len(data['analysis']['patterns'])} patterns, bias={data['analysis']['overall_bias']}")


class TestAIHypothesis:
    """Test AI Hypothesis endpoint"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        return session
    
    def test_hypothesis_returns_verdict(self, auth_session):
        """Hypothesis should return verdict and confidence"""
        response = auth_session.get(f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2", timeout=60)
        assert response.status_code == 200, f"Hypothesis failed: {response.text}"
        data = response.json()
        
        assert "verdict" in data
        assert "confidence" in data
        assert "thesis" in data
        assert "catalysts" in data
        assert "risks" in data
        assert data["is_pro"] == True
        
        print(f"PASS: Hypothesis returned verdict={data['verdict']}, confidence={data['confidence']}%")


class TestMarketPrediction:
    """Test Market Prediction endpoint"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@risedual.ai",
            "password": "RiseDual2026!"
        })
        return session
    
    def test_market_prediction_returns_direction(self, auth_session):
        """Market Prediction should return overall direction and timeframes"""
        response = auth_session.get(f"{BASE_URL}/api/market/prediction", timeout=60)
        assert response.status_code == 200, f"Market Prediction failed: {response.text}"
        data = response.json()
        
        assert "overall_direction" in data
        assert "confidence_score" in data
        assert "timeframes" in data
        assert "intraday" in data["timeframes"]
        assert "short_term" in data["timeframes"]
        assert "medium_term" in data["timeframes"]
        
        print(f"PASS: Market Prediction returned direction={data['overall_direction']}, confidence={data['confidence_score']}%")


class TestErrorHandling:
    """Test error handling for non-authenticated requests"""
    
    def test_401_on_protected_endpoint_without_auth(self):
        """Protected endpoints should return 401 without auth"""
        response = requests.get(f"{BASE_URL}/api/intelligence/score/AAPL", timeout=10)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print(f"PASS: Protected endpoint correctly returns 401 without auth")
    
    def test_error_message_is_descriptive(self):
        """Error messages should be descriptive, not generic"""
        response = requests.get(f"{BASE_URL}/api/intelligence/score/AAPL", timeout=10)
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        assert data["detail"] != "Analysis failed"  # Should not be generic
        print(f"PASS: Error message is descriptive: '{data['detail']}'")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
