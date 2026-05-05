"""
Iteration 157: Blocks Prevented Card + Auto-Promotion Suggestions + Outcome-Grading Wiring

Tests:
1. /api/admin/blocks-prevented/summary — owner-only, returns correct shape
2. /api/admin/blocks-prevented/summary?days=N — days clamped to [1, 365]
3. /api/admin/guard-shadow/policy — includes suggestions array
4. Regression: /api/admin/proof-chain/stats, /api/admin/guard-shadow/summary, /api/admin/conviction/quality-kpis
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
OWNER_EMAIL = "admin@risedual.ai"
OWNER_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def owner_session():
    """Authenticate as owner and return session with cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    if login_resp.status_code != 200:
        pytest.skip(f"Owner login failed: {login_resp.status_code} - {login_resp.text}")
    
    return session


class TestBlocksPreventedSummary:
    """Tests for /api/admin/blocks-prevented/summary endpoint."""

    def test_blocks_prevented_summary_returns_200(self, owner_session):
        """GET /api/admin/blocks-prevented/summary returns 200 for owner."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"PASS: blocks-prevented/summary returns 200")

    def test_blocks_prevented_summary_shape(self, owner_session):
        """Response has correct shape: window, total_blocked, estimated_exposure_prevented_usd, etc."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary")
        assert resp.status_code == 200
        data = resp.json()
        
        # Required top-level fields
        required_fields = [
            "window", "total_blocked", "estimated_exposure_prevented_usd",
            "by_reason", "by_asset_class", "top_symbols", "weekly_series", "generated_at"
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        # Window sub-fields
        window = data["window"]
        assert "label" in window, "window.label missing"
        assert "days" in window, "window.days missing"
        assert "since" in window, "window.since missing"
        assert "until" in window, "window.until missing"
        
        # Type checks
        assert isinstance(data["total_blocked"], int), "total_blocked should be int"
        assert isinstance(data["estimated_exposure_prevented_usd"], (int, float)), "exposure should be numeric"
        assert isinstance(data["by_reason"], list), "by_reason should be list"
        assert isinstance(data["by_asset_class"], list), "by_asset_class should be list"
        assert isinstance(data["top_symbols"], list), "top_symbols should be list"
        assert isinstance(data["weekly_series"], list), "weekly_series should be list"
        
        print(f"PASS: blocks-prevented/summary has correct shape")
        print(f"  window.label: {window['label']}")
        print(f"  total_blocked: {data['total_blocked']}")
        print(f"  estimated_exposure_prevented_usd: {data['estimated_exposure_prevented_usd']}")

    def test_blocks_prevented_summary_with_days_param(self, owner_session):
        """GET /api/admin/blocks-prevented/summary?days=7 uses custom window."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary?days=7")
        assert resp.status_code == 200
        data = resp.json()
        
        # Window should reflect 7 days
        assert data["window"]["days"] == 7, f"Expected 7 days, got {data['window']['days']}"
        assert "7 day" in data["window"]["label"].lower() or "last 7" in data["window"]["label"].lower(), \
            f"Window label should mention 7 days: {data['window']['label']}"
        
        print(f"PASS: blocks-prevented/summary?days=7 works correctly")

    def test_blocks_prevented_summary_days_clamped_min(self, owner_session):
        """days=0 should be clamped to 1."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary?days=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["window"]["days"] >= 1, f"days should be clamped to >=1, got {data['window']['days']}"
        print(f"PASS: days=0 clamped to {data['window']['days']}")

    def test_blocks_prevented_summary_days_clamped_max(self, owner_session):
        """days=999 should be clamped to 365."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary?days=999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["window"]["days"] <= 365, f"days should be clamped to <=365, got {data['window']['days']}"
        print(f"PASS: days=999 clamped to {data['window']['days']}")

    def test_blocks_prevented_summary_owner_only(self):
        """Non-authenticated request should fail."""
        session = requests.Session()
        resp = session.get(f"{BASE_URL}/api/admin/blocks-prevented/summary")
        assert resp.status_code in [401, 403], f"Expected 401/403 for unauthenticated, got {resp.status_code}"
        print(f"PASS: blocks-prevented/summary is owner-only (got {resp.status_code})")


class TestGuardShadowPolicySuggestions:
    """Tests for auto-promotion suggestions in /api/admin/guard-shadow/policy."""

    def test_policy_includes_suggestions_array(self, owner_session):
        """GET /api/admin/guard-shadow/policy includes suggestions array."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/guard-shadow/policy")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "suggestions" in data, "policy response should include 'suggestions' field"
        assert isinstance(data["suggestions"], list), "suggestions should be a list"
        
        print(f"PASS: policy includes suggestions array (length={len(data['suggestions'])})")
        
        # If suggestions exist, verify their shape
        if data["suggestions"]:
            s = data["suggestions"][0]
            expected_fields = ["flag", "would_block_rate", "n_evaluations", "n_would_block", "rationale"]
            for field in expected_fields:
                assert field in s, f"Suggestion missing field: {field}"
            print(f"  First suggestion: {s['flag']} - {s['rationale'][:50]}...")

    def test_policy_still_has_effective_overrides_env_defaults(self, owner_session):
        """Policy response still has effective, overrides, env_defaults, history."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/guard-shadow/policy")
        assert resp.status_code == 200
        data = resp.json()
        
        required = ["effective", "overrides", "env_defaults", "history", "valid_flags"]
        for field in required:
            assert field in data, f"Missing field: {field}"
        
        print(f"PASS: policy has all required fields")


class TestRegressionEndpoints:
    """Regression tests for previously-tested endpoints."""

    def test_proof_chain_stats(self, owner_session):
        """GET /api/admin/proof-chain/stats still works."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/proof-chain/stats")
        assert resp.status_code == 200, f"proof-chain/stats failed: {resp.status_code}"
        data = resp.json()
        assert "total_events" in data or "total" in data or "stats" in data, \
            f"Unexpected response shape: {list(data.keys())}"
        print(f"PASS: /api/admin/proof-chain/stats returns 200")

    def test_guard_shadow_summary(self, owner_session):
        """GET /api/admin/guard-shadow/summary still works."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/guard-shadow/summary")
        assert resp.status_code == 200, f"guard-shadow/summary failed: {resp.status_code}"
        data = resp.json()
        assert "total" in data, f"Missing 'total' in response"
        assert "would_allow" in data, f"Missing 'would_allow' in response"
        assert "would_block" in data, f"Missing 'would_block' in response"
        print(f"PASS: /api/admin/guard-shadow/summary returns 200 (total={data['total']})")

    def test_conviction_quality_kpis(self, owner_session):
        """GET /api/admin/conviction/quality-kpis still works."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis")
        assert resp.status_code == 200, f"conviction/quality-kpis failed: {resp.status_code}"
        print(f"PASS: /api/admin/conviction/quality-kpis returns 200")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
