"""
Iteration 112: AI Signal Validation Layer (Phase 3 Step 3)
Tests for POST /api/scanner/validate and GET /api/scanner/validate/stats endpoints.
AI validation uses GPT-5.2 to evaluate scanner matches with confidence scores, verdicts, risk levels.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestAuthLogin:
    """Test authentication works before testing validation endpoints"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "user" in data or "email" in data, "Response should contain user data"


class TestAISignalValidation:
    """Tests for POST /api/scanner/validate endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies for authenticated requests"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    
    def test_validate_without_auth_returns_401(self):
        """POST /api/scanner/validate without auth returns 401"""
        # Use a fresh session without cookies
        response = requests.post(
            f"{BASE_URL}/api/scanner/validate",
            json={"matches": [{"symbol": "AAPL", "price": 150}]}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    
    def test_validate_with_empty_matches_returns_400(self):
        """POST /api/scanner/validate with empty matches returns 400"""
        response = self.session.post(
            f"{BASE_URL}/api/scanner/validate",
            json={"matches": [], "strategy_name": "Test"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data, "Should have error detail"
    
    def test_validate_with_matches_returns_ai_scores(self):
        """POST /api/scanner/validate with matches returns AI confidence scores, verdicts, risk levels"""
        # Sample scanner matches to validate
        matches = [
            {"symbol": "AAPL", "price": 185.50, "rsi": 65, "vol_ratio": 1.2, "trend": "bullish", "detail": "RSI momentum"},
            {"symbol": "MSFT", "price": 420.00, "rsi": 72, "vol_ratio": 1.5, "trend": "strong_bullish", "detail": "Breakout"},
            {"symbol": "GOOGL", "price": 175.00, "rsi": 45, "vol_ratio": 0.8, "trend": "neutral", "detail": "Consolidation"},
        ]
        
        response = self.session.post(
            f"{BASE_URL}/api/scanner/validate",
            json={"matches": matches, "strategy_name": "RSI Momentum"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check response structure
        assert "matches" in data, "Response should have 'matches' array"
        assert "summary" in data, "Response should have 'summary' object"
        assert "validated_at" in data, "Response should have 'validated_at' timestamp"
        
        # Check matches have AI fields
        validated_matches = data["matches"]
        assert len(validated_matches) == 3, f"Expected 3 matches, got {len(validated_matches)}"
        
        for match in validated_matches:
            # Each match should have AI validation fields
            assert "ai_confidence" in match, f"Match {match.get('symbol')} missing ai_confidence"
            assert "ai_verdict" in match, f"Match {match.get('symbol')} missing ai_verdict"
            assert "ai_risk" in match, f"Match {match.get('symbol')} missing ai_risk"
            assert "ai_validated" in match, f"Match {match.get('symbol')} missing ai_validated"
            
            # Validate field values
            if match.get("ai_validated"):
                assert isinstance(match["ai_confidence"], (int, float)), "ai_confidence should be numeric"
                assert 0 <= match["ai_confidence"] <= 100, "ai_confidence should be 0-100"
                assert match["ai_verdict"] in ["strong_buy", "buy", "hold", "sell", "strong_sell", "avoid"], \
                    f"Invalid verdict: {match['ai_verdict']}"
                assert match["ai_risk"] in ["low", "medium", "high", "extreme"], \
                    f"Invalid risk level: {match['ai_risk']}"
    
    def test_validate_returns_summary_with_counts(self):
        """POST /api/scanner/validate returns summary with strong/moderate/weak counts and avg confidence"""
        matches = [
            {"symbol": "NVDA", "price": 900.00, "rsi": 78, "vol_ratio": 2.0, "trend": "strong_bullish"},
            {"symbol": "AMD", "price": 165.00, "rsi": 55, "vol_ratio": 1.1, "trend": "bullish"},
        ]
        
        response = self.session.post(
            f"{BASE_URL}/api/scanner/validate",
            json={"matches": matches, "strategy_name": "Momentum Breakout"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        summary = data.get("summary", {})
        
        # Check summary fields
        assert "total" in summary, "Summary should have 'total'"
        assert "strong_signals" in summary, "Summary should have 'strong_signals'"
        assert "moderate_signals" in summary, "Summary should have 'moderate_signals'"
        assert "weak_signals" in summary, "Summary should have 'weak_signals'"
        assert "avg_confidence" in summary, "Summary should have 'avg_confidence'"
        
        # Validate counts
        assert summary["total"] == 2, f"Expected total=2, got {summary['total']}"
        assert isinstance(summary["avg_confidence"], (int, float)), "avg_confidence should be numeric"
        
        # strong + moderate + weak should equal total (for validated matches)
        validated_count = sum(1 for m in data["matches"] if m.get("ai_validated"))
        signal_sum = summary["strong_signals"] + summary["moderate_signals"] + summary["weak_signals"]
        assert signal_sum == validated_count, f"Signal counts ({signal_sum}) should equal validated count ({validated_count})"
    
    def test_validate_sorts_by_confidence_descending(self):
        """POST /api/scanner/validate returns matches sorted by AI confidence (highest first)"""
        matches = [
            {"symbol": "LOW", "price": 50.00, "rsi": 25, "vol_ratio": 0.5, "trend": "bearish"},
            {"symbol": "HIGH", "price": 200.00, "rsi": 80, "vol_ratio": 2.5, "trend": "strong_bullish"},
            {"symbol": "MID", "price": 100.00, "rsi": 50, "vol_ratio": 1.0, "trend": "neutral"},
        ]
        
        response = self.session.post(
            f"{BASE_URL}/api/scanner/validate",
            json={"matches": matches, "strategy_name": "Test Sort"}
        )
        assert response.status_code == 200
        
        data = response.json()
        validated_matches = data["matches"]
        
        # Check sorting (highest confidence first)
        confidences = [m.get("ai_confidence") or 0 for m in validated_matches]
        assert confidences == sorted(confidences, reverse=True), \
            f"Matches should be sorted by confidence descending: {confidences}"


class TestValidationStats:
    """Tests for GET /api/scanner/validate/stats endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
    
    def test_validation_stats_returns_history(self):
        """GET /api/scanner/validate/stats returns validation history stats"""
        response = self.session.get(f"{BASE_URL}/api/scanner/validate/stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Check stats fields
        assert "total_validations" in data, "Stats should have 'total_validations'"
        assert "avg_confidence" in data, "Stats should have 'avg_confidence'"
        
        # Values should be numeric
        assert isinstance(data["total_validations"], int), "total_validations should be int"
        assert isinstance(data["avg_confidence"], (int, float)), "avg_confidence should be numeric"
    
    def test_validation_stats_without_auth_returns_401(self):
        """GET /api/scanner/validate/stats without auth returns 401"""
        response = requests.get(f"{BASE_URL}/api/scanner/validate/stats")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestRegressionScanner:
    """Regression tests for existing scanner endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
    
    def test_get_strategies_still_works(self):
        """GET /api/scanner/strategies still works (regression)"""
        response = self.session.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "strategies" in data, "Response should have 'strategies'"
        assert len(data["strategies"]) > 0, "Should have at least one strategy"
    
    def test_custom_run_still_works(self):
        """POST /api/scanner/custom/run still works (regression)"""
        rule = {
            "logic": "AND",
            "conditions": [
                {"indicator": "rsi", "operator": "lt", "value": 30}
            ]
        }
        
        response = self.session.post(
            f"{BASE_URL}/api/scanner/custom/run",
            json={"rule": rule}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "matches" in data, "Response should have 'matches'"
        assert "match_count" in data, "Response should have 'match_count'"
    
    def test_scan_endpoint_still_works(self):
        """POST /api/scanner/scan still works (regression)"""
        response = self.session.post(
            f"{BASE_URL}/api/scanner/scan",
            json={}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "scanned" in data, "Response should have 'scanned'"
        assert "strategies" in data, "Response should have 'strategies'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
