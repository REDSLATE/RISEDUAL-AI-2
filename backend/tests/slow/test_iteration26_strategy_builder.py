"""
Iteration 26 Tests: AI Strategy Builder + Code Quality Score
Tests for:
- POST /api/strategy/generate — AI strategy generation with GPT-5.2
- POST /api/strategy/save — Save generated strategy
- GET /api/strategy/list — List saved strategies
- DELETE /api/strategy/{name} — Delete strategy by name
- GET /api/admin/code-quality — Code quality score (admin only)
- POST /api/auth/login — Owner login verification
- GET /api/hypothesis/AAPL?model=gpt-5.2 — Hypothesis still works after refactoring
- GET /api/gov-filings — Gov filings still works after refactoring
"""
import pytest
import requests
import os
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD


class TestOwnerLogin:
    """Verify owner login still works after all changes"""
    
    def test_owner_login_success(self):
        """POST /api/auth/login — owner login works"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "No access_token in response"
        # API returns user fields at top level, not nested in "user" key
        assert data.get("email") == OWNER_EMAIL or data.get("user", {}).get("email") == OWNER_EMAIL, "Email mismatch"
        role = data.get("role") or data.get("user", {}).get("role")
        assert role == "owner", f"Expected owner role, got {role}"
        print(f"SUCCESS: Owner login works, role={role}")


@pytest.fixture(scope="module")
def owner_token():
    """Get owner auth token for authenticated tests"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    if response.status_code != 200:
        pytest.skip(f"Owner login failed: {response.text}")
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def auth_headers(owner_token):
    """Auth headers with bearer token"""
    return {"Authorization": f"Bearer {owner_token}"}


class TestStrategyGenerate:
    """Test AI Strategy Builder generation endpoint"""
    
    def test_strategy_generate_requires_auth(self):
        """POST /api/strategy/generate — requires authentication"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/generate",
            json={"description": "Buy AAPL when RSI < 30"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("SUCCESS: Strategy generate requires auth")
    
    def test_strategy_generate_empty_description(self, auth_headers):
        """POST /api/strategy/generate — rejects empty description"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/generate",
            headers=auth_headers,
            json={"description": ""}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("SUCCESS: Empty description rejected")
    
    def test_strategy_generate_success(self, auth_headers):
        """POST /api/strategy/generate — generates structured strategy from plain English"""
        description = "Buy AAPL when RSI drops below 30 and MACD crosses bullish. Sell when RSI hits 70. Use 2% stop loss."
        
        response = requests.post(
            f"{BASE_URL}/api/strategy/generate",
            headers=auth_headers,
            json={"description": description, "model": "gpt-5.2"},
            timeout=60  # AI generation can take 5-10 seconds
        )
        
        assert response.status_code == 200, f"Strategy generation failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "strategy" in data, "No strategy in response"
        strategy = data["strategy"]
        
        # Required fields per PRD
        assert "name" in strategy, "Missing name"
        assert "summary" in strategy, "Missing summary"
        assert "timeframe" in strategy, "Missing timeframe"
        assert "indicators" in strategy, "Missing indicators"
        assert "entry_rules" in strategy, "Missing entry_rules"
        assert "exit_rules" in strategy, "Missing exit_rules"
        assert "risk_management" in strategy, "Missing risk_management"
        
        # Verify types
        assert isinstance(strategy["indicators"], list), "indicators should be list"
        assert isinstance(strategy["entry_rules"], list), "entry_rules should be list"
        assert isinstance(strategy["exit_rules"], list), "exit_rules should be list"
        assert isinstance(strategy["risk_management"], dict), "risk_management should be dict"
        
        # Verify model used
        assert data.get("model_used") == "gpt-5.2", f"Expected gpt-5.2, got {data.get('model_used')}"
        
        print(f"SUCCESS: Strategy generated - name='{strategy['name']}', indicators={len(strategy['indicators'])}, entry_rules={len(strategy['entry_rules'])}")
        
        # Store for save test
        pytest.generated_strategy = strategy
        pytest.generated_description = description


class TestStrategySaveAndList:
    """Test strategy save, list, and delete operations"""
    
    def test_strategy_save_success(self, auth_headers):
        """POST /api/strategy/save — saves generated strategy"""
        # Use a simple test strategy if generation didn't run
        strategy = getattr(pytest, 'generated_strategy', None) or {
            "name": "TEST_RSI_Strategy",
            "summary": "Test strategy for iteration 26",
            "timeframe": "Daily",
            "indicators": [{"name": "RSI", "period": 14}],
            "entry_rules": [{"condition": "RSI < 30", "description": "Buy oversold"}],
            "exit_rules": [{"condition": "RSI > 70", "description": "Sell overbought"}],
            "risk_management": {"stop_loss": "2%", "take_profit": "6%"}
        }
        description = getattr(pytest, 'generated_description', "Test strategy description")
        
        response = requests.post(
            f"{BASE_URL}/api/strategy/save",
            headers=auth_headers,
            json={"strategy": strategy, "description": description}
        )
        
        assert response.status_code == 200, f"Save failed: {response.text}"
        data = response.json()
        assert "message" in data, "No message in response"
        assert "name" in data, "No name in response"
        
        print(f"SUCCESS: Strategy saved - name='{data['name']}'")
        pytest.saved_strategy_name = data["name"]
    
    def test_strategy_list_success(self, auth_headers):
        """GET /api/strategy/list — lists saved strategies"""
        response = requests.get(
            f"{BASE_URL}/api/strategy/list",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        
        assert "strategies" in data, "No strategies in response"
        assert "count" in data, "No count in response"
        assert isinstance(data["strategies"], list), "strategies should be list"
        assert data["count"] >= 1, "Expected at least 1 saved strategy"
        
        print(f"SUCCESS: Listed {data['count']} strategies")
    
    def test_strategy_delete_success(self, auth_headers):
        """DELETE /api/strategy/{name} — deletes saved strategy"""
        name = getattr(pytest, 'saved_strategy_name', None)
        if not name:
            pytest.skip("No saved strategy to delete")
        
        import urllib.parse
        encoded_name = urllib.parse.quote(name)
        
        response = requests.delete(
            f"{BASE_URL}/api/strategy/{encoded_name}",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"Delete failed: {response.text}"
        data = response.json()
        assert "message" in data, "No message in response"
        
        print(f"SUCCESS: Strategy '{name}' deleted")
    
    def test_strategy_delete_not_found(self, auth_headers):
        """DELETE /api/strategy/{name} — returns 404 for non-existent strategy"""
        response = requests.delete(
            f"{BASE_URL}/api/strategy/NONEXISTENT_STRATEGY_12345",
            headers=auth_headers
        )
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("SUCCESS: Delete returns 404 for non-existent strategy")


class TestCodeQualityScore:
    """Test Code Quality Score admin endpoint"""
    
    def test_code_quality_requires_admin(self):
        """GET /api/admin/code-quality — requires admin/owner auth"""
        response = requests.get(f"{BASE_URL}/api/admin/code-quality")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("SUCCESS: Code quality requires auth")
    
    def test_code_quality_success(self, auth_headers):
        """GET /api/admin/code-quality — returns score, grade, metrics, breakdown"""
        response = requests.get(
            f"{BASE_URL}/api/admin/code-quality",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"Code quality failed: {response.text}"
        data = response.json()
        
        # Required fields per PRD
        assert "score" in data, "Missing score"
        assert "grade" in data, "Missing grade"
        assert "metrics" in data, "Missing metrics"
        assert "breakdown" in data, "Missing breakdown"
        
        # Verify score is valid
        assert isinstance(data["score"], (int, float)), "score should be numeric"
        assert 0 <= data["score"] <= 100, f"score should be 0-100, got {data['score']}"
        
        # Verify grade is valid
        valid_grades = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "D"]
        assert data["grade"] in valid_grades, f"Invalid grade: {data['grade']}"
        
        # Verify metrics structure
        metrics = data["metrics"]
        assert "backend_files" in metrics, "Missing backend_files"
        assert "frontend_files" in metrics, "Missing frontend_files"
        assert "test_files" in metrics, "Missing test_files"
        assert "service_modules" in metrics, "Missing service_modules"
        assert "components" in metrics, "Missing components"
        
        # Verify breakdown structure
        breakdown = data["breakdown"]
        assert "test_coverage" in breakdown, "Missing test_coverage breakdown"
        assert "modularity" in breakdown, "Missing modularity breakdown"
        assert "services" in breakdown, "Missing services breakdown"
        assert "components" in breakdown, "Missing components breakdown"
        
        print(f"SUCCESS: Code quality score={data['score']}, grade={data['grade']}")
        print(f"  Metrics: {metrics['backend_files']} backend files, {metrics['frontend_files']} frontend files, {metrics['test_files']} tests")


class TestExistingEndpointsAfterRefactoring:
    """Verify existing endpoints still work after ai.py and gov_filings_service.py refactoring"""
    
    def test_hypothesis_endpoint_works(self, auth_headers):
        """GET /api/hypothesis/AAPL?model=gpt-5.2 — hypothesis still works after ai.py refactoring"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2",
            headers=auth_headers,
            timeout=60  # AI generation can be slow
        )
        
        # Should return 200 or 403 (if not pro) - not 500
        assert response.status_code in [200, 403], f"Hypothesis endpoint error: {response.status_code} - {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            # Verify response has expected structure - actual API returns verdict, confidence, thesis, etc.
            expected_keys = ["verdict", "confidence", "thesis", "summary", "symbol"]
            has_expected = any(key in data for key in expected_keys)
            assert has_expected, f"Unexpected response structure: {data.keys()}"
            print(f"SUCCESS: Hypothesis endpoint works, verdict={data.get('verdict')}, confidence={data.get('confidence')}")
        else:
            print("SUCCESS: Hypothesis endpoint returns 403 (paywall working)")
    
    def test_gov_filings_endpoint_works(self):
        """GET /api/gov-filings — gov filings still works after service refactoring"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        
        # Should return 200 - not 500
        assert response.status_code == 200, f"Gov filings endpoint error: {response.status_code} - {response.text}"
        
        data = response.json()
        # Verify response has expected structure - actual API returns congressional_trades, earnings_count, etc.
        expected_keys = ["congressional_trades", "congressional_count", "earnings_count", "company_news_finnhub"]
        has_expected = any(key in data for key in expected_keys)
        assert has_expected, f"Unexpected response structure: {data.keys()}"
        
        print(f"SUCCESS: Gov filings endpoint works, congressional_count={data.get('congressional_count')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
