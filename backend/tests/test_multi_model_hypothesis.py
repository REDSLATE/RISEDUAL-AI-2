"""
Multi-Model AI Hypothesis Feature Tests
Tests model access control, individual model generation, and consensus mode
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://risedual-trading.preview.emergentagent.com').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
FREE_EMAIL = "freeuser_test@test.com"
FREE_PASSWORD = "Test1234!"


@pytest.fixture(scope="module")
def owner_token():
    """Get Pro/Owner user token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip("Owner login failed")


@pytest.fixture(scope="module")
def free_token():
    """Get Free user token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": FREE_EMAIL,
        "password": FREE_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip("Free user login failed")


class TestModelAccessControl:
    """Test that free users can only access GPT-5.2, Pro users can access all models"""
    
    def test_free_user_claude_returns_403(self, free_token):
        """Free user should get 403 when trying Claude"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=claude-sonnet-4.5",
            headers={"Authorization": f"Bearer {free_token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert "Premium AI models require a Pro subscription" in data.get("detail", "")
    
    def test_free_user_gemini_returns_403(self, free_token):
        """Free user should get 403 when trying Gemini"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=gemini-pro",
            headers={"Authorization": f"Bearer {free_token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert "Premium AI models require a Pro subscription" in data.get("detail", "")
    
    def test_free_user_consensus_returns_403(self, free_token):
        """Free user should get 403 when trying Consensus Mode"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=consensus",
            headers={"Authorization": f"Bearer {free_token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert "Premium AI models require a Pro subscription" in data.get("detail", "")
    
    def test_free_user_gpt52_returns_teaser(self, free_token):
        """Free user with GPT-5.2 should get teaser (locked) response"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2",
            headers={"Authorization": f"Bearer {free_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("is_pro") == False
        assert "teaser" in data
        assert data["teaser"]["verdict"] == "LOCKED"
        assert "data_sources_count" in data["teaser"]
        assert "world_events_count" in data["teaser"]
        assert "congressional_trades_count" in data["teaser"]


class TestProUserModelAccess:
    """Test that Pro users can access all 4 model options"""
    
    @pytest.mark.timeout(90)
    def test_pro_user_gpt52_returns_full_hypothesis(self, owner_token):
        """Pro user with GPT-5.2 should get full hypothesis"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/TSLA?model=gpt-5.2",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify full hypothesis structure
        assert data.get("is_pro") == True
        assert data.get("model") == "GPT-5.2"
        assert data.get("model_key") == "gpt-5.2"
        assert data.get("verdict") in ["BUY", "SELL", "HOLD", "NEUTRAL"]
        assert isinstance(data.get("confidence"), int)
        assert 0 <= data.get("confidence", 0) <= 100
        assert data.get("thesis") is not None
        assert isinstance(data.get("catalysts"), list)
        assert isinstance(data.get("risks"), list)
    
    @pytest.mark.timeout(90)
    def test_pro_user_claude_returns_full_hypothesis(self, owner_token):
        """Pro user with Claude Sonnet 4.5 should get full hypothesis"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/MSFT?model=claude-sonnet-4.5",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify model attribution
        assert data.get("is_pro") == True
        assert data.get("model") == "Claude Sonnet 4.5"
        assert data.get("model_key") == "claude-sonnet-4.5"
        assert data.get("verdict") in ["BUY", "SELL", "HOLD", "NEUTRAL"]
        assert isinstance(data.get("confidence"), int)
    
    @pytest.mark.timeout(90)
    def test_pro_user_gemini_returns_full_hypothesis(self, owner_token):
        """Pro user with Gemini Pro should get full hypothesis"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/NVDA?model=gemini-pro",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify model attribution
        assert data.get("is_pro") == True
        assert data.get("model") == "Gemini Pro"
        assert data.get("model_key") == "gemini-pro"
        assert data.get("verdict") in ["BUY", "SELL", "HOLD", "NEUTRAL"]
        assert isinstance(data.get("confidence"), int)


class TestConsensusMode:
    """Test Consensus Mode which runs all 3 models with weighted voting"""
    
    @pytest.mark.timeout(180)
    def test_consensus_returns_individual_results(self, owner_token):
        """Consensus mode should return individual_results array with all 3 models"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/GOOGL?model=consensus",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify consensus structure
        assert data.get("is_pro") == True
        assert data.get("model") == "Consensus"
        assert data.get("model_key") == "consensus"
        
        # Verify individual_results array
        individual_results = data.get("individual_results", [])
        assert len(individual_results) == 3
        
        # Verify each model is present
        model_keys = [r.get("model_key") for r in individual_results]
        assert "gpt-5.2" in model_keys
        assert "claude-sonnet-4.5" in model_keys
        assert "gemini-pro" in model_keys
        
        # Verify each result has required fields
        for result in individual_results:
            assert "model" in result
            assert "model_key" in result
            assert "verdict" in result
            assert "confidence" in result
            assert "error" in result
    
    @pytest.mark.timeout(180)
    def test_consensus_has_agreement_percentage(self, owner_token):
        """Consensus mode should have agreement percentage"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AMZN?model=consensus",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify agreement field exists
        assert "agreement" in data
        assert isinstance(data.get("agreement"), int)
        assert 0 <= data.get("agreement", 0) <= 100
    
    @pytest.mark.timeout(180)
    def test_consensus_has_weighted_confidence(self, owner_token):
        """Consensus mode should have weighted confidence from all models"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/META?model=consensus",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify confidence is weighted average
        assert "confidence" in data
        assert isinstance(data.get("confidence"), int)
        assert 0 <= data.get("confidence", 0) <= 100
        
        # Verify verdict is from weighted voting
        assert data.get("verdict") in ["BUY", "SELL", "HOLD", "NEUTRAL"]


class TestInvalidModelHandling:
    """Test handling of invalid model parameters"""
    
    def test_invalid_model_defaults_to_gpt52(self, owner_token):
        """Invalid model parameter should default to GPT-5.2"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=invalid-model",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        # Should default to GPT-5.2
        assert data.get("model_key") == "gpt-5.2" or data.get("model") == "GPT-5.2"


class TestHypothesisResponseStructure:
    """Test that hypothesis responses have all required fields"""
    
    @pytest.mark.timeout(90)
    def test_hypothesis_has_all_required_fields(self, owner_token):
        """Verify hypothesis response has all expected fields"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/BTC?model=gpt-5.2",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        # Required fields
        assert "symbol" in data
        assert "is_pro" in data
        assert "verdict" in data
        assert "confidence" in data
        assert "model" in data
        assert "model_key" in data
        
        # Optional but expected fields
        assert "thesis" in data or data.get("verdict") == "ERROR"
        assert "catalysts" in data
        assert "risks" in data
