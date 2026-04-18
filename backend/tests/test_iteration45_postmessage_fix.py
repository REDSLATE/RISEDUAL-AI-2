"""
Iteration 45: PostMessage Bug Fix Verification Tests

Tests to verify the fix for "Failed to execute postMessage on Window: Request object could not be cloned" error.
The fix involved:
1. Replacing AbortController.signal with Promise.race timeout in authFetch/fetchWithRetry
2. Removing fetch handler from service-worker.js (v3)

This test verifies all AI endpoints work correctly after the fix.
"""

import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

class TestAuthEndpoints:
    """Test authentication endpoints with cookies"""
    
    def test_login_success(self):
        """Test login sets httpOnly cookies"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        
        # Check cookies are set
        cookies = response.cookies
        assert 'access_token' in cookies or 'access_token' in response.headers.get('Set-Cookie', ''), "access_token cookie not set"
        
        data = response.json()
        assert 'email' in data, "Response should contain user email"
        assert data['email'] == ADMIN_EMAIL
        print(f"Login successful for {data['email']}")
    
    def test_auth_me_with_cookies(self):
        """Test /auth/me endpoint with cookie authentication"""
        # First login to get cookies
        session = requests.Session()
        login_response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert login_response.status_code == 200
        
        # Now test /auth/me
        me_response = session.get(f"{BASE_URL}/api/auth/me")
        assert me_response.status_code == 200, f"Auth me failed: {me_response.text}"
        
        data = me_response.json()
        assert data['email'] == ADMIN_EMAIL
        print(f"Auth me successful: {data['email']}, role: {data.get('role')}")


class TestAIWarRoom:
    """Test AI War Room endpoint - was broken by postMessage error"""
    
    @pytest.fixture
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        return session
    
    def test_war_room_aapl(self, auth_session):
        """Test War Room analysis for AAPL - this was failing with postMessage error"""
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/war-room/AAPL",
            timeout=90  # AI endpoints can take 15-30 seconds
        )
        assert response.status_code == 200, f"War Room failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        assert data['symbol'] == 'AAPL'
        assert 'composite' in data
        assert 'verdict' in data['composite']
        assert 'ai_score' in data
        assert 'earnings' in data
        assert 'insiders' in data
        print(f"War Room AAPL: Verdict={data['composite']['verdict']}, Score={data['composite']['score']}")


class TestAIIntelligenceHub:
    """Test AI Intelligence Hub endpoints - were broken by postMessage error"""
    
    @pytest.fixture
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        return session
    
    def test_intelligence_score_aapl(self, auth_session):
        """Test AI Score endpoint for AAPL"""
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/score/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"Intelligence Score failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        assert data['symbol'] == 'AAPL'
        assert 'scores' in data
        assert 'overall_score' in data['scores']
        assert 'recommendation' in data['scores']
        print(f"Intelligence Score AAPL: Overall={data['scores']['overall_score']}, Rec={data['scores']['recommendation']}")
    
    def test_intelligence_patterns_aapl(self, auth_session):
        """Test Patterns endpoint for AAPL"""
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/patterns/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"Intelligence Patterns failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        print(f"Intelligence Patterns AAPL: {len(data.get('patterns', []))} patterns found")
    
    def test_intelligence_brief_aapl(self, auth_session):
        """Test Quick Brief endpoint for AAPL"""
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/brief/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"Intelligence Brief failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        assert 'brief' in data or 'headline' in data
        print(f"Intelligence Brief AAPL: {data.get('headline', data.get('brief', {}).get('headline', 'N/A'))[:50]}...")


class TestAIHypothesis:
    """Test AI Hypothesis endpoint - was broken by postMessage error"""
    
    @pytest.fixture
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        return session
    
    def test_hypothesis_aapl(self, auth_session):
        """Test Hypothesis analysis for AAPL"""
        response = auth_session.get(
            f"{BASE_URL}/api/hypothesis/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"Hypothesis failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        assert data['symbol'] == 'AAPL'
        assert 'verdict' in data
        assert 'thesis' in data
        assert 'catalysts' in data
        assert 'risks' in data
        print(f"Hypothesis AAPL: Verdict={data['verdict']}, Confidence={data.get('confidence')}")


class TestMarketPredictions:
    """Test Market Predictions endpoint - was broken by postMessage error"""
    
    @pytest.fixture
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        return session
    
    def test_market_prediction(self, auth_session):
        """Test Market Prediction endpoint"""
        response = auth_session.get(
            f"{BASE_URL}/api/market/prediction",
            timeout=90
        )
        assert response.status_code == 200, f"Market Prediction failed: {response.text}"
        
        data = response.json()
        assert 'overall_direction' in data
        assert 'confidence_score' in data
        assert 'timeframes' in data
        assert 'key_signals' in data
        print(f"Market Prediction: Direction={data['overall_direction']}, Confidence={data['confidence_score']}")


class TestCompanyResearch:
    """Test Company Research endpoint - was broken by postMessage error"""
    
    @pytest.fixture
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        return session
    
    def test_research_aapl(self, auth_session):
        """Test Company Research for AAPL"""
        response = auth_session.get(
            f"{BASE_URL}/api/research/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"Company Research failed: {response.text}"
        
        data = response.json()
        assert 'symbol' in data
        assert data['symbol'] == 'AAPL'
        assert 'company_name' in data
        assert 'overview' in data
        assert 'synthesis' in data
        print(f"Company Research AAPL: {data['company_name']}")


class TestServiceWorkerFix:
    """Verify service worker configuration is correct"""
    
    def test_service_worker_no_fetch_handler(self):
        """Verify service-worker.js doesn't have fetch handler that causes postMessage errors"""
        # Read the service worker file
        sw_path = "/app/frontend/public/service-worker.js"
        with open(sw_path, 'r') as f:
            content = f.read()
        
        # Check it's v3 (no fetch handler)
        assert "risedualai-v3" in content, "Service worker should be v3"
        assert "addEventListener('fetch'" not in content, "Service worker should NOT have fetch handler"
        assert "skipWaiting" in content, "Service worker should have skipWaiting"
        print("Service worker v3 verified - no fetch handler")
    
    def test_build_service_worker_matches(self):
        """Verify build service-worker.js matches public version"""
        public_sw = "/app/frontend/public/service-worker.js"
        build_sw = "/app/frontend/build/service-worker.js"
        
        with open(public_sw, 'r') as f:
            public_content = f.read()
        
        with open(build_sw, 'r') as f:
            build_content = f.read()
        
        assert public_content == build_content, "Build service-worker.js should match public version"
        print("Build service-worker.js matches public version")


class TestAuthFetchFix:
    """Verify AuthContext.jsx fix for AbortController removal"""
    
    def test_authfetch_no_abortcontroller(self):
        """Verify authFetch doesn't use AbortController.signal in actual fetch calls"""
        auth_context_path = "/app/frontend/src/contexts/AuthContext.jsx"
        with open(auth_context_path, 'r') as f:
            content = f.read()
        
        # Check AbortController is not used in fetch calls (only in comments)
        # The word "AbortController" should only appear in comments explaining the fix
        assert "new AbortController" not in content, "AuthContext should NOT create AbortController"
        assert "signal:" not in content, "AuthContext should NOT use signal property"
        assert "Promise.race" in content, "AuthContext should use Promise.race for timeout"
        assert "credentials: 'include'" in content, "AuthContext should include credentials"
        print("AuthContext.jsx verified - uses Promise.race instead of AbortController")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
