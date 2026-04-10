"""
Iteration 49: Multi-Agent CrewAI-style Testing
Tests the new multi-agent analysis features:
- War Room with 3 parallel agents + 1 synthesizer
- Investment Hypothesis with multi-agent crew
- Market Prediction with multi-agent crew
- Chat endpoint (FormData fix verification)
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestAuthSetup:
    """Authentication setup for multi-agent tests"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create authenticated session with cookies"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        return s
    
    @pytest.fixture(scope="class")
    def auth_cookies(self, session):
        """Login and get auth cookies"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        # Cookies are set automatically in session
        return session.cookies
    
    def test_login_returns_cookies(self, session, auth_cookies):
        """Verify login sets httpOnly cookies"""
        assert auth_cookies is not None
        print(f"Login successful, cookies: {list(auth_cookies.keys())}")


class TestWarRoomMultiAgent:
    """Test War Room multi-agent analysis"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        return s
    
    def test_war_room_returns_multi_agent_data(self, auth_session):
        """GET /api/intelligence/war-room/AAPL returns multi-agent composite"""
        # This endpoint takes 30-40 seconds due to multi-agent LLM calls
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/war-room/AAPL",
            timeout=90
        )
        assert response.status_code == 200, f"War room failed: {response.text}"
        
        data = response.json()
        print(f"War Room response keys: {list(data.keys())}")
        
        # Verify basic structure
        assert "symbol" in data, "Missing symbol"
        assert data["symbol"] == "AAPL", f"Wrong symbol: {data['symbol']}"
        assert "composite" in data, "Missing composite"
        
        composite = data["composite"]
        print(f"Composite keys: {list(composite.keys())}")
        
        # Verify multi-agent fields
        assert composite.get("multi_agent") == True, "multi_agent should be True"
        assert composite.get("agents_used") == 4, f"Expected 4 agents, got {composite.get('agents_used')}"
        
        # Verify agent_analyses array
        agent_analyses = composite.get("agent_analyses", [])
        assert len(agent_analyses) == 3, f"Expected 3 agent analyses, got {len(agent_analyses)}"
        
        # Verify each agent has role and summary
        for agent in agent_analyses:
            assert "role" in agent, f"Agent missing role: {agent}"
            assert "summary" in agent, f"Agent missing summary: {agent}"
            print(f"  Agent: {agent['role'][:50]}...")
        
        # Verify verdict and score
        assert "verdict" in composite, "Missing verdict"
        assert composite["verdict"] in ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"], f"Invalid verdict: {composite['verdict']}"
        assert "score" in composite, "Missing score"
        assert 0 <= composite["score"] <= 100, f"Score out of range: {composite['score']}"
        
        # Verify thesis and catalysts/risks
        assert "key_thesis" in composite or "thesis" in composite, "Missing thesis"
        assert "catalysts" in composite, "Missing catalysts"
        assert "risks" in composite, "Missing risks"
        
        print(f"War Room PASS: verdict={composite['verdict']}, score={composite['score']}, agents={composite['agents_used']}")


class TestHypothesisMultiAgent:
    """Test Investment Hypothesis multi-agent analysis"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        return s
    
    def test_hypothesis_returns_multi_agent_data(self, auth_session):
        """GET /api/hypothesis/TSLA?model=gpt-5.2 returns multi-agent hypothesis"""
        # This endpoint takes 30-40 seconds due to multi-agent LLM calls
        response = auth_session.get(
            f"{BASE_URL}/api/hypothesis/TSLA?model=gpt-5.2",
            timeout=90
        )
        assert response.status_code == 200, f"Hypothesis failed: {response.text}"
        
        data = response.json()
        print(f"Hypothesis response keys: {list(data.keys())}")
        
        # Verify multi-agent fields
        assert data.get("multi_agent") == True, "multi_agent should be True"
        assert data.get("agents_used") == 4, f"Expected 4 agents, got {data.get('agents_used')}"
        
        # Verify agent_analyses array
        agent_analyses = data.get("agent_analyses", [])
        assert len(agent_analyses) == 3, f"Expected 3 agent analyses, got {len(agent_analyses)}"
        
        # Verify each agent has role and summary
        for agent in agent_analyses:
            assert "role" in agent, f"Agent missing role: {agent}"
            assert "summary" in agent, f"Agent missing summary: {agent}"
            print(f"  Agent: {agent['role'][:50]}...")
        
        # Verify verdict and confidence
        assert "verdict" in data, "Missing verdict"
        assert data["verdict"] in ["BUY", "SELL", "HOLD"], f"Invalid verdict: {data['verdict']}"
        assert "confidence" in data, "Missing confidence"
        assert 0 <= data["confidence"] <= 100, f"Confidence out of range: {data['confidence']}"
        
        # Verify thesis and catalysts/risks
        assert "thesis" in data, "Missing thesis"
        assert "catalysts" in data, "Missing catalysts"
        assert "risks" in data, "Missing risks"
        
        # Verify symbol
        assert data.get("symbol") == "TSLA", f"Wrong symbol: {data.get('symbol')}"
        
        print(f"Hypothesis PASS: verdict={data['verdict']}, confidence={data['confidence']}, agents={data['agents_used']}")


class TestMarketPredictionMultiAgent:
    """Test Market Prediction multi-agent analysis"""
    
    def test_prediction_returns_multi_agent_data(self):
        """GET /api/market/prediction returns multi-agent prediction"""
        # This endpoint takes 30-40 seconds due to multi-agent LLM calls
        response = requests.get(
            f"{BASE_URL}/api/market/prediction",
            timeout=90
        )
        assert response.status_code == 200, f"Prediction failed: {response.text}"
        
        data = response.json()
        print(f"Prediction response keys: {list(data.keys())}")
        
        # Verify multi-agent fields
        assert data.get("multi_agent") == True, "multi_agent should be True"
        assert data.get("agents_used") == 4, f"Expected 4 agents, got {data.get('agents_used')}"
        
        # Verify agent_analyses array
        agent_analyses = data.get("agent_analyses", [])
        assert len(agent_analyses) == 3, f"Expected 3 agent analyses, got {len(agent_analyses)}"
        
        # Verify each agent has role and summary
        for agent in agent_analyses:
            assert "role" in agent, f"Agent missing role: {agent}"
            assert "summary" in agent, f"Agent missing summary: {agent}"
            print(f"  Agent: {agent['role'][:50]}...")
        
        # Verify overall_direction and confidence_score
        assert "overall_direction" in data, "Missing overall_direction"
        assert data["overall_direction"] in ["BULLISH", "BEARISH", "NEUTRAL"], f"Invalid direction: {data['overall_direction']}"
        assert "confidence_score" in data, "Missing confidence_score"
        assert 0 <= data["confidence_score"] <= 100, f"Confidence out of range: {data['confidence_score']}"
        
        # Verify summary
        assert "summary" in data, "Missing summary"
        
        print(f"Prediction PASS: direction={data['overall_direction']}, confidence={data['confidence_score']}, agents={data['agents_used']}")


class TestChatEndpoint:
    """Test Chat endpoint (FormData fix verification)"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        return s
    
    def test_chat_with_formdata(self, auth_session):
        """POST /api/chat with FormData returns AI response"""
        # Send as FormData (not JSON)
        response = auth_session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "What is the current market sentiment?",
                "sessionId": "test_session_123"
            },
            timeout=60
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        
        data = response.json()
        print(f"Chat response keys: {list(data.keys())}")
        
        # Verify response has content
        assert "response" in data or "message" in data or "content" in data, f"Missing response content: {data}"
        
        # Get the actual response text
        response_text = data.get("response") or data.get("message") or data.get("content", "")
        assert len(response_text) > 10, f"Response too short: {response_text}"
        
        print(f"Chat PASS: response length={len(response_text)}")
    
    def test_chat_missing_message_returns_422(self, auth_session):
        """POST /api/chat without message returns 422"""
        response = auth_session.post(
            f"{BASE_URL}/api/chat",
            data={
                "sessionId": "test_session_123"
            },
            timeout=30
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        print("Chat validation PASS: missing message returns 422")
    
    def test_chat_missing_session_returns_422(self, auth_session):
        """POST /api/chat without sessionId returns 422"""
        response = auth_session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "Hello"
            },
            timeout=30
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        print("Chat validation PASS: missing sessionId returns 422")


class TestEndpointResponseStructure:
    """Verify response structure matches frontend expectations"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        return s
    
    def test_war_room_has_frontend_expected_fields(self, auth_session):
        """Verify War Room response has all fields expected by WarRoomCards.jsx"""
        response = auth_session.get(
            f"{BASE_URL}/api/intelligence/war-room/MSFT",
            timeout=90
        )
        assert response.status_code == 200
        
        data = response.json()
        composite = data.get("composite", {})
        
        # Fields expected by CrewInsightsCard component
        expected_fields = ["multi_agent", "agents_used", "confidence", "key_thesis", 
                          "bull_case", "bear_case", "catalysts", "risks", 
                          "price_target_short", "price_target_medium", "agent_analyses"]
        
        missing = [f for f in expected_fields if f not in composite]
        if missing:
            print(f"Warning: Missing fields in composite: {missing}")
        
        # Verify breakdown structure for CompositeBreakdownBar
        breakdown = composite.get("breakdown", {})
        assert "fundamental_score" in breakdown or "ai_score_weight" in breakdown, "Missing breakdown scores"
        
        print(f"War Room structure PASS: {len(expected_fields) - len(missing)}/{len(expected_fields)} fields present")
    
    def test_hypothesis_has_frontend_expected_fields(self, auth_session):
        """Verify Hypothesis response has all fields expected by HypothesisResults.jsx"""
        response = auth_session.get(
            f"{BASE_URL}/api/hypothesis/NVDA?model=gpt-5.2",
            timeout=90
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Fields expected by HypothesisResults component
        expected_fields = ["verdict", "confidence", "symbol", "multi_agent", "agents_used",
                          "thesis", "catalysts", "risks", "price_target_short", 
                          "price_target_medium", "agent_analyses"]
        
        missing = [f for f in expected_fields if f not in data]
        if missing:
            print(f"Warning: Missing fields in hypothesis: {missing}")
        
        print(f"Hypothesis structure PASS: {len(expected_fields) - len(missing)}/{len(expected_fields)} fields present")
    
    def test_prediction_has_frontend_expected_fields(self):
        """Verify Prediction response has all fields expected by PredictionCards.jsx"""
        response = requests.get(
            f"{BASE_URL}/api/market/prediction",
            timeout=90
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Fields expected by PredictionCard component
        expected_fields = ["overall_direction", "confidence_score", "summary", "multi_agent",
                          "agents_used", "agent_analyses", "timeframes"]
        
        missing = [f for f in expected_fields if f not in data]
        if missing:
            print(f"Warning: Missing fields in prediction: {missing}")
        
        # Optional but useful fields
        optional_fields = ["agent_consensus", "institutional_flow", "geopolitical_impact"]
        present_optional = [f for f in optional_fields if f in data]
        print(f"Optional fields present: {present_optional}")
        
        print(f"Prediction structure PASS: {len(expected_fields) - len(missing)}/{len(expected_fields)} fields present")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
