"""
Iteration 111: Visual Rule Builder - Custom Scanning Rules with AND/OR Logic
Tests for Phase 3 Step 2 features:
- GET /api/scanner/indicators - returns 23 indicators with types and operators
- POST /api/scanner/custom/run - evaluates custom rules with AND/OR logic
- POST /api/scanner/custom/save - saves custom rules
- GET /api/scanner/custom/rules - lists saved rules
- DELETE /api/scanner/custom/rules/{rule_id} - deletes a rule
- Regression: GET /api/scanner/strategies, POST /api/scanner/scan still work
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD


class TestAuth:
    """Authentication tests"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin returns 200"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "user" in data or "email" in data, "Response should contain user info"
        print("PASS: Admin login returns 200")


class TestIndicatorsEndpoint:
    """Tests for GET /api/scanner/indicators"""
    
    def test_indicators_returns_23_indicators(self):
        """GET /api/scanner/indicators returns 23 indicators"""
        response = requests.get(f"{BASE_URL}/api/scanner/indicators")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "indicators" in data, "Response should have 'indicators' key"
        indicators = data["indicators"]
        assert len(indicators) == 23, f"Expected 23 indicators, got {len(indicators)}"
        print(f"PASS: GET /api/scanner/indicators returns {len(indicators)} indicators")
    
    def test_indicators_have_required_fields(self):
        """Each indicator has id, name, type"""
        response = requests.get(f"{BASE_URL}/api/scanner/indicators")
        data = response.json()
        
        for ind in data["indicators"]:
            assert "id" in ind, f"Indicator missing 'id': {ind}"
            assert "name" in ind, f"Indicator missing 'name': {ind}"
            assert "type" in ind, f"Indicator missing 'type': {ind}"
        print("PASS: All indicators have id, name, type")
    
    def test_indicators_include_expected_types(self):
        """Indicators include number, price, and category types"""
        response = requests.get(f"{BASE_URL}/api/scanner/indicators")
        data = response.json()
        
        types = set(ind["type"] for ind in data["indicators"])
        assert "number" in types, "Should have 'number' type indicators"
        assert "price" in types, "Should have 'price' type indicators"
        assert "category" in types, "Should have 'category' type indicators"
        print(f"PASS: Indicators include types: {types}")
    
    def test_operators_returned_for_each_type(self):
        """Operators are returned for number, price, category types"""
        response = requests.get(f"{BASE_URL}/api/scanner/indicators")
        data = response.json()
        
        assert "operators" in data, "Response should have 'operators' key"
        operators = data["operators"]
        assert "number" in operators, "Should have operators for 'number' type"
        assert "price" in operators, "Should have operators for 'price' type"
        assert "category" in operators, "Should have operators for 'category' type"
        
        # Check number operators include gt, lt, etc.
        number_ops = [op["id"] for op in operators["number"]]
        assert "gt" in number_ops, "Number operators should include 'gt'"
        assert "lt" in number_ops, "Number operators should include 'lt'"
        
        # Check category operators include is, is_not
        category_ops = [op["id"] for op in operators["category"]]
        assert "is" in category_ops, "Category operators should include 'is'"
        print("PASS: Operators returned for all types")


class TestCustomRuleRun:
    """Tests for POST /api/scanner/custom/run"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session with auth cookies"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_custom_run_with_and_logic(self):
        """POST /api/scanner/custom/run with AND logic evaluates correctly"""
        rule = {
            "logic": "AND",
            "conditions": [
                {"indicator": "rsi", "operator": "lt", "value": "50"},
                {"indicator": "volume_ratio", "operator": "gt", "value": "0.5"}
            ],
            "groups": []
        }
        response = self.session.post(f"{BASE_URL}/api/scanner/custom/run", json={"rule": rule})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "matches" in data, "Response should have 'matches'"
        assert "scanned" in data, "Response should have 'scanned'"
        assert "match_count" in data, "Response should have 'match_count'"
        assert data["scanned"] > 0, "Should have scanned some symbols"
        print(f"PASS: AND logic scan - scanned {data['scanned']}, found {data['match_count']} matches")
    
    def test_custom_run_with_or_logic(self):
        """POST /api/scanner/custom/run with OR logic evaluates correctly"""
        rule = {
            "logic": "OR",
            "conditions": [
                {"indicator": "rsi", "operator": "lt", "value": "30"},
                {"indicator": "rsi", "operator": "gt", "value": "70"}
            ],
            "groups": []
        }
        response = self.session.post(f"{BASE_URL}/api/scanner/custom/run", json={"rule": rule})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "matches" in data, "Response should have 'matches'"
        assert data["scanned"] > 0, "Should have scanned some symbols"
        print(f"PASS: OR logic scan - scanned {data['scanned']}, found {data['match_count']} matches")
    
    def test_custom_run_with_category_indicator(self):
        """POST /api/scanner/custom/run with category indicator (trend is bullish) works"""
        rule = {
            "logic": "AND",
            "conditions": [
                {"indicator": "trend", "operator": "is", "value": "bullish"}
            ],
            "groups": []
        }
        response = self.session.post(f"{BASE_URL}/api/scanner/custom/run", json={"rule": rule})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "matches" in data, "Response should have 'matches'"
        # Verify matches have trend = bullish
        for match in data["matches"]:
            assert match.get("trend") in ["bullish", "strong_bullish"], f"Match should have bullish trend: {match}"
        print(f"PASS: Category indicator scan - found {data['match_count']} bullish matches")
    
    def test_custom_run_without_conditions_returns_400(self):
        """POST /api/scanner/custom/run without conditions returns 400"""
        rule = {
            "logic": "AND",
            "conditions": [],
            "groups": []
        }
        response = self.session.post(f"{BASE_URL}/api/scanner/custom/run", json={"rule": rule})
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: Empty conditions returns 400")
    
    def test_custom_run_without_auth_returns_401(self):
        """POST /api/scanner/custom/run without auth returns 401"""
        # Use a new session without login
        new_session = requests.Session()
        rule = {
            "logic": "AND",
            "conditions": [{"indicator": "rsi", "operator": "lt", "value": "30"}],
            "groups": []
        }
        response = new_session.post(f"{BASE_URL}/api/scanner/custom/run", json={"rule": rule})
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: Custom run without auth returns 401")


class TestCustomRuleSaveAndList:
    """Tests for POST /api/scanner/custom/save and GET /api/scanner/custom/rules"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session with auth cookies"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_save_custom_rule_returns_rule_id(self):
        """POST /api/scanner/custom/save saves a custom rule and returns rule_id"""
        rule_data = {
            "name": "TEST_RSI_Oversold_Rule",
            "description": "Test rule for RSI oversold",
            "rule": {
                "logic": "AND",
                "conditions": [{"indicator": "rsi", "operator": "lt", "value": "30"}],
                "groups": []
            },
            "signal_type": "bullish"
        }
        response = self.session.post(f"{BASE_URL}/api/scanner/custom/save", json=rule_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "rule_id" in data, "Response should have 'rule_id'"
        assert "name" in data, "Response should have 'name'"
        assert data["name"] == "TEST_RSI_Oversold_Rule"
        print(f"PASS: Saved rule with ID: {data['rule_id']}")
        
        # Store for cleanup
        self.saved_rule_id = data["rule_id"]
    
    def test_get_saved_rules_returns_list(self):
        """GET /api/scanner/custom/rules returns saved rules"""
        # First save a rule
        rule_data = {
            "name": "TEST_List_Rule",
            "rule": {
                "logic": "AND",
                "conditions": [{"indicator": "rsi", "operator": "gt", "value": "50"}],
                "groups": []
            }
        }
        save_response = self.session.post(f"{BASE_URL}/api/scanner/custom/save", json=rule_data)
        assert save_response.status_code == 200
        
        # Now list rules
        response = self.session.get(f"{BASE_URL}/api/scanner/custom/rules")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert isinstance(data, list), "Response should be a list"
        # Find our test rule
        test_rules = [r for r in data if r.get("name", "").startswith("TEST_")]
        assert len(test_rules) > 0, "Should have at least one test rule"
        print(f"PASS: GET /api/scanner/custom/rules returns {len(data)} rules")


class TestCustomRuleDelete:
    """Tests for DELETE /api/scanner/custom/rules/{rule_id}"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session with auth cookies"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_delete_custom_rule(self):
        """DELETE /api/scanner/custom/rules/{rule_id} deletes a rule"""
        # First save a rule
        rule_data = {
            "name": "TEST_Delete_Rule",
            "rule": {
                "logic": "AND",
                "conditions": [{"indicator": "rsi", "operator": "lt", "value": "25"}],
                "groups": []
            }
        }
        save_response = self.session.post(f"{BASE_URL}/api/scanner/custom/save", json=rule_data)
        assert save_response.status_code == 200
        rule_id = save_response.json()["rule_id"]
        
        # Delete the rule
        response = self.session.delete(f"{BASE_URL}/api/scanner/custom/rules/{rule_id}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("status") == "deleted", "Response should confirm deletion"
        print(f"PASS: Deleted rule {rule_id}")


class TestRegressionScanner:
    """Regression tests for existing scanner endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session with auth cookies"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_get_strategies_still_works(self):
        """GET /api/scanner/strategies still works (regression check)"""
        response = self.session.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "strategies" in data, "Response should have 'strategies'"
        assert len(data["strategies"]) == 10, f"Expected 10 strategies, got {len(data['strategies'])}"
        print(f"PASS: GET /api/scanner/strategies returns {len(data['strategies'])} strategies")
    
    def test_post_scan_still_works(self):
        """POST /api/scanner/scan still works (regression check)"""
        response = self.session.post(f"{BASE_URL}/api/scanner/scan", json={})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "strategies" in data, "Response should have 'strategies'"
        assert "scanned" in data, "Response should have 'scanned'"
        assert data["scanned"] > 0, "Should have scanned some symbols"
        print(f"PASS: POST /api/scanner/scan scanned {data['scanned']} symbols")


class TestCleanup:
    """Cleanup test data"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session with auth cookies"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_cleanup_test_rules(self):
        """Cleanup: Delete all TEST_ prefixed rules"""
        # Get all rules
        response = self.session.get(f"{BASE_URL}/api/scanner/custom/rules")
        if response.status_code != 200:
            print("SKIP: Could not get rules for cleanup")
            return
        
        rules = response.json()
        test_rules = [r for r in rules if r.get("name", "").startswith("TEST_")]
        
        deleted = 0
        for rule in test_rules:
            rule_id = rule.get("rule_id")
            if rule_id:
                del_response = self.session.delete(f"{BASE_URL}/api/scanner/custom/rules/{rule_id}")
                if del_response.status_code == 200:
                    deleted += 1
        
        print(f"PASS: Cleaned up {deleted} test rules")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
