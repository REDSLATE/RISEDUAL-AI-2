"""
Iteration 156: Per-Patent Enforcement Policy + Conviction Calibration Polish Tests

Tests:
1. GET /api/admin/guard-shadow/policy - returns effective, overrides, env_defaults, history, valid_flags
2. POST /api/admin/guard-shadow/policy/promote - sets flag override
3. POST /api/admin/guard-shadow/policy/clear - clears flag override
4. Invalid flag returns 400
5. Regression: /api/admin/guard-shadow/summary still works
6. Regression: /api/admin/conviction/quality-kpis still works
7. Regression: /api/admin/proof-chain/stats still works
8. Smart orders with mode=paper still works
"""

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials
OWNER_EMAIL = "admin@risedual.ai"
OWNER_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def auth_session():
    """Get authenticated session for owner user."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login
    response = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    
    # Set auth header
    token = data.get("access_token")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    
    return session


class TestGuardShadowPolicy:
    """Tests for the Per-Patent Enforcement Policy endpoints."""
    
    def test_get_policy_returns_correct_shape(self, auth_session):
        """GET /api/admin/guard-shadow/policy returns expected fields."""
        response = auth_session.get(f"{BASE_URL}/api/admin/guard-shadow/policy")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        
        # Check required fields
        assert "effective" in data, "Missing 'effective' field"
        assert "overrides" in data, "Missing 'overrides' field"
        assert "env_defaults" in data, "Missing 'env_defaults' field"
        assert "history" in data, "Missing 'history' field"
        assert "valid_flags" in data, "Missing 'valid_flags' field"
        
        # Check valid_flags contains all 5 flags
        expected_flags = {
            "enforce_adversarial",
            "enforce_auditor",
            "enforce_authority",
            "enforce_failure_mode",
            "enforce_risk_budget",
        }
        assert set(data["valid_flags"]) == expected_flags, f"Invalid flags: {data['valid_flags']}"
        
        # Check effective has all flags
        for flag in expected_flags:
            assert flag in data["effective"], f"Missing flag in effective: {flag}"
            assert isinstance(data["effective"][flag], bool), f"Flag {flag} should be bool"
    
    def test_promote_flag_demote(self, auth_session):
        """POST /api/admin/guard-shadow/policy/promote can demote a flag."""
        # Demote enforce_auditor to shadow
        response = auth_session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/promote",
            json={
                "flag": "enforce_auditor",
                "value": False,
                "note": "Test demote from pytest",
            },
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("ok") is True, "Expected ok=true"
        assert "applied" in data, "Missing 'applied' field"
        assert data["applied"]["flag"] == "enforce_auditor"
        assert data["applied"]["value"] is False
        
        # Verify the override is in place
        policy_response = auth_session.get(f"{BASE_URL}/api/admin/guard-shadow/policy")
        policy = policy_response.json()
        assert policy["effective"]["enforce_auditor"] is False, "Flag should be shadow"
        assert "enforce_auditor" in policy["overrides"], "Override should be present"
    
    def test_clear_flag_reverts_to_default(self, auth_session):
        """POST /api/admin/guard-shadow/policy/clear reverts to env default."""
        # First ensure there's an override
        auth_session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/promote",
            json={"flag": "enforce_auditor", "value": False},
        )
        
        # Clear the override
        response = auth_session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/clear",
            json={"flag": "enforce_auditor"},
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("ok") is True, "Expected ok=true"
        assert data.get("cleared") == "enforce_auditor"
        
        # Verify the override is cleared
        policy_response = auth_session.get(f"{BASE_URL}/api/admin/guard-shadow/policy")
        policy = policy_response.json()
        assert "enforce_auditor" not in policy["overrides"], "Override should be cleared"
    
    def test_promote_invalid_flag_returns_400(self, auth_session):
        """POST /api/admin/guard-shadow/policy/promote with invalid flag returns 400."""
        response = auth_session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/promote",
            json={"flag": "invalid_flag", "value": True},
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data, "Missing error detail"
    
    def test_clear_invalid_flag_returns_400(self, auth_session):
        """POST /api/admin/guard-shadow/policy/clear with invalid flag returns 400."""
        response = auth_session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/clear",
            json={"flag": "invalid_flag"},
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"


class TestRegressionEndpoints:
    """Regression tests for existing endpoints."""
    
    def test_guard_shadow_summary_still_works(self, auth_session):
        """GET /api/admin/guard-shadow/summary returns expected shape."""
        response = auth_session.get(f"{BASE_URL}/api/admin/guard-shadow/summary")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "shadow_mode_enabled" in data
        assert "total" in data
        assert "would_allow" in data
        assert "would_block" in data
    
    def test_conviction_quality_kpis_still_works(self, auth_session):
        """GET /api/admin/conviction/quality-kpis returns expected shape."""
        response = auth_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "weeks" in data
        assert "unique_failure_patterns" in data
        assert "calibration_gap" in data
        assert "summary" in data
    
    def test_proof_chain_stats_still_works(self, auth_session):
        """GET /api/admin/proof-chain/stats returns expected shape."""
        response = auth_session.get(f"{BASE_URL}/api/admin/proof-chain/stats")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "total" in data
        assert "by_event_type" in data


class TestSmartOrdersRegression:
    """Regression tests for smart orders with paper mode."""
    
    def test_smart_order_paper_mode_works(self, auth_session):
        """POST /api/smart-orders with mode=paper still works."""
        response = auth_session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 1,
                "order_type": "market",
                "mode": "paper",
            },
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("mode") == "paper"
        assert data.get("symbol") == "AAPL"
        assert "order_id" in data or "user_id" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
