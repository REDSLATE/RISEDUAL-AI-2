"""
Iteration 29: AI Intelligence Feature Tests
Tests for AI Stock Scoring, Pattern Recognition, and Quick Briefs endpoints.
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


class TestAIIntelligenceAuth:
    """Test authentication requirements for AI Intelligence endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup for each test"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_login_returns_access_token(self):
        """POST /api/auth/login with owner credentials returns access_token"""
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "Response missing access_token"
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        print("✓ Login successful, got access_token")
    
    def test_score_endpoint_requires_auth(self):
        """GET /api/intelligence/score/AAPL returns 401 without token"""
        response = self.session.get(f"{BASE_URL}/api/intelligence/score/AAPL")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Score endpoint correctly requires auth (401)")
    
    def test_patterns_endpoint_requires_auth(self):
        """GET /api/intelligence/patterns/AAPL returns 401 without token"""
        response = self.session.get(f"{BASE_URL}/api/intelligence/patterns/AAPL")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Patterns endpoint correctly requires auth (401)")
    
    def test_brief_endpoint_requires_auth(self):
        """GET /api/intelligence/brief/AAPL returns 401 without token"""
        response = self.session.get(f"{BASE_URL}/api/intelligence/brief/AAPL")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Brief endpoint correctly requires auth (401)")


class TestAIScoreEndpoint:
    """Test AI Stock Scoring endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get token
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            token = response.json().get("access_token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    def test_ai_score_valid_symbol(self):
        """GET /api/intelligence/score/AAPL returns AI score with required fields"""
        # Note: This endpoint calls Alpha Vantage + Emergent LLM, takes 10-15 seconds
        response = self.session.get(f"{BASE_URL}/api/intelligence/score/AAPL", timeout=60)
        assert response.status_code == 200, f"Score request failed: {response.text}"
        
        data = response.json()
        
        # Verify top-level structure
        assert "symbol" in data, "Missing symbol"
        assert data["symbol"] == "AAPL"
        assert "scores" in data, "Missing scores object"
        assert "generated_at" in data, "Missing generated_at timestamp"
        
        # Verify scores object has required fields
        scores = data["scores"]
        assert "overall_score" in scores, "Missing overall_score"
        assert 1 <= scores["overall_score"] <= 10, f"overall_score {scores['overall_score']} not in 1-10 range"
        
        assert "technical_score" in scores, "Missing technical_score"
        assert "fundamental_score" in scores, "Missing fundamental_score"
        assert "sentiment_score" in scores, "Missing sentiment_score"
        
        assert "recommendation" in scores, "Missing recommendation"
        assert scores["recommendation"] in ["buy", "hold", "sell"], f"Invalid recommendation: {scores['recommendation']}"
        
        assert "factors" in scores, "Missing factors array"
        assert isinstance(scores["factors"], list), "factors should be an array"
        
        assert "target_range" in scores, "Missing target_range"
        if scores["target_range"]:
            assert "low" in scores["target_range"], "target_range missing low"
            assert "high" in scores["target_range"], "target_range missing high"
        
        print(f"✓ AI Score for AAPL: {scores['overall_score']}/10, recommendation: {scores['recommendation']}")
    
    def test_ai_score_invalid_symbol(self):
        """GET /api/intelligence/score/INVALID returns 400 or meaningful error"""
        response = self.session.get(f"{BASE_URL}/api/intelligence/score/INVALIDXYZ123", timeout=30)
        # Should return 400 for invalid symbol or 500 with meaningful error
        assert response.status_code in [400, 500], f"Expected 400/500, got {response.status_code}"
        print(f"✓ Invalid symbol correctly returns error ({response.status_code})")


class TestPatternsEndpoint:
    """Test Pattern Recognition endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get token
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            token = response.json().get("access_token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    def test_patterns_valid_symbol(self):
        """GET /api/intelligence/patterns/AAPL returns patterns with required fields"""
        # Add delay to avoid Alpha Vantage rate limit (5/min)
        time.sleep(15)
        
        response = self.session.get(f"{BASE_URL}/api/intelligence/patterns/AAPL", timeout=60)
        assert response.status_code == 200, f"Patterns request failed: {response.text}"
        
        data = response.json()
        
        # Verify top-level structure
        assert "symbol" in data, "Missing symbol"
        assert data["symbol"] == "AAPL"
        assert "analysis" in data, "Missing analysis object"
        assert "analyzed_at" in data, "Missing analyzed_at timestamp"
        
        # Verify analysis object
        analysis = data["analysis"]
        assert "patterns" in analysis, "Missing patterns array"
        assert isinstance(analysis["patterns"], list), "patterns should be an array"
        
        assert "key_levels" in analysis, "Missing key_levels"
        if analysis["key_levels"]:
            assert "support" in analysis["key_levels"] or "resistance" in analysis["key_levels"], \
                "key_levels should have support or resistance arrays"
        
        # If patterns exist, verify structure
        if len(analysis["patterns"]) > 0:
            pattern = analysis["patterns"][0]
            assert "name" in pattern, "Pattern missing name"
            assert "type" in pattern, "Pattern missing type"
            assert "direction" in pattern, "Pattern missing direction"
            assert "confidence" in pattern, "Pattern missing confidence"
            assert 0 <= pattern["confidence"] <= 100, f"Confidence {pattern['confidence']} not in 0-100 range"
        
        print(f"✓ Patterns for AAPL: {len(analysis['patterns'])} patterns detected")


class TestQuickBriefEndpoint:
    """Test Quick Brief endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get token
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            token = response.json().get("access_token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    def test_brief_valid_symbol(self):
        """GET /api/intelligence/brief/AAPL returns quick brief with required fields"""
        # Add delay to avoid Alpha Vantage rate limit (5/min)
        time.sleep(15)
        
        response = self.session.get(f"{BASE_URL}/api/intelligence/brief/AAPL", timeout=60)
        assert response.status_code == 200, f"Brief request failed: {response.text}"
        
        data = response.json()
        
        # Verify top-level structure
        assert "symbol" in data, "Missing symbol"
        assert data["symbol"] == "AAPL"
        assert "brief" in data, "Missing brief object"
        assert "performance" in data, "Missing performance object"
        assert "generated_at" in data, "Missing generated_at timestamp"
        
        # Verify brief object
        brief = data["brief"]
        assert "headline" in brief, "Missing headline"
        assert "verdict" in brief, "Missing verdict"
        assert brief["verdict"] in ["buy", "hold", "sell"], f"Invalid verdict: {brief['verdict']}"
        
        assert "confidence" in brief, "Missing confidence"
        assert 0 <= brief["confidence"] <= 100, f"Confidence {brief['confidence']} not in 0-100 range"
        
        assert "brief" in brief, "Missing brief text"
        assert "key_metrics" in brief, "Missing key_metrics array"
        assert isinstance(brief["key_metrics"], list), "key_metrics should be an array"
        
        # Verify performance object
        performance = data["performance"]
        assert "1w" in performance, "Missing 1w performance"
        assert "1m" in performance, "Missing 1m performance"
        assert "3m" in performance, "Missing 3m performance"
        
        # Optional fields
        if "catalysts" in brief:
            assert isinstance(brief["catalysts"], list), "catalysts should be an array"
        if "risks" in brief:
            assert isinstance(brief["risks"], list), "risks should be an array"
        
        print(f"✓ Quick Brief for AAPL: verdict={brief['verdict']}, confidence={brief['confidence']}%")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
