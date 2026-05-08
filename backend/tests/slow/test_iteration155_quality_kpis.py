"""
Iteration 155 Tests: Risk Quality KPIs + OUTCOME_VERIFIED Proof Chain

Tests:
1. GET /api/admin/conviction/quality-kpis - owner-only, returns correct shape
2. GET /api/admin/conviction/quality-kpis - 403 for non-owners
3. GET /api/admin/guard-shadow/summary - regression test
4. GET /api/admin/proof-chain/entities - regression test
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from test_credentials.md
OWNER_EMAIL = "admin@risedual.ai"
OWNER_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def session():
    """Create a requests session with cookies."""
    return requests.Session()


@pytest.fixture(scope="module")
def owner_session(session):
    """Login as owner and return authenticated session."""
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert login_resp.status_code == 200, f"Owner login failed: {login_resp.text}"
    return session


class TestQualityKPIsEndpoint:
    """Tests for /api/admin/conviction/quality-kpis endpoint."""

    def test_quality_kpis_owner_access(self, owner_session):
        """Owner should be able to access quality-kpis endpoint."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis?weeks=8")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        
        # Verify response shape
        assert "weeks" in data, "Missing 'weeks' field"
        assert "unique_failure_patterns" in data, "Missing 'unique_failure_patterns' field"
        assert "calibration_gap" in data, "Missing 'calibration_gap' field"
        assert "summary" in data, "Missing 'summary' field"
        
        # Verify weeks value
        assert data["weeks"] == 8, f"Expected weeks=8, got {data['weeks']}"
        
        # Verify unique_failure_patterns is a list
        assert isinstance(data["unique_failure_patterns"], list), "unique_failure_patterns should be a list"
        
        # Verify calibration_gap is a list
        assert isinstance(data["calibration_gap"], list), "calibration_gap should be a list"
        
        # Verify summary structure
        summary = data["summary"]
        assert "trend" in summary, "Missing 'trend' in summary"
        assert "current_gap" in summary, "Missing 'current_gap' in summary"
        assert "current_unique_failures" in summary, "Missing 'current_unique_failures' in summary"
        
        # Verify trend is one of expected values
        valid_trends = ["improving", "stable", "degrading", "insufficient_data"]
        assert summary["trend"] in valid_trends, f"Invalid trend: {summary['trend']}"
        
        print(f"✓ Quality KPIs endpoint returns correct shape")
        print(f"  - weeks: {data['weeks']}")
        print(f"  - unique_failure_patterns count: {len(data['unique_failure_patterns'])}")
        print(f"  - calibration_gap count: {len(data['calibration_gap'])}")
        print(f"  - summary.trend: {summary['trend']}")
        print(f"  - summary.current_gap: {summary['current_gap']}")
        print(f"  - summary.current_unique_failures: {summary['current_unique_failures']}")

    def test_quality_kpis_failure_patterns_structure(self, owner_session):
        """Verify unique_failure_patterns entries have correct structure."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis?weeks=4")
        assert resp.status_code == 200
        
        data = resp.json()
        patterns = data["unique_failure_patterns"]
        
        if len(patterns) > 0:
            entry = patterns[0]
            assert "week_start" in entry, "Missing 'week_start' in failure pattern entry"
            assert "count" in entry, "Missing 'count' in failure pattern entry"
            assert "patterns" in entry, "Missing 'patterns' in failure pattern entry"
            print(f"✓ Failure patterns structure verified: {len(patterns)} weeks")
        else:
            print("✓ No failure patterns data (empty list is valid)")

    def test_quality_kpis_calibration_gap_structure(self, owner_session):
        """Verify calibration_gap entries have correct structure."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis?weeks=4")
        assert resp.status_code == 200
        
        data = resp.json()
        gaps = data["calibration_gap"]
        
        if len(gaps) > 0:
            entry = gaps[0]
            assert "week_start" in entry, "Missing 'week_start' in calibration gap entry"
            assert "n" in entry, "Missing 'n' in calibration gap entry"
            assert "avg_confidence" in entry, "Missing 'avg_confidence' in calibration gap entry"
            assert "accuracy" in entry, "Missing 'accuracy' in calibration gap entry"
            assert "gap" in entry, "Missing 'gap' in calibration gap entry"
            print(f"✓ Calibration gap structure verified: {len(gaps)} weeks")
        else:
            print("✓ No calibration gap data (empty list is valid)")

    def test_quality_kpis_weeks_parameter(self, owner_session):
        """Verify weeks parameter is respected (max 26)."""
        # Test with max weeks
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis?weeks=26")
        assert resp.status_code == 200
        data = resp.json()
        assert data["weeks"] == 26, f"Expected weeks=26, got {data['weeks']}"
        
        # Test with weeks > 26 (should be capped)
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis?weeks=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["weeks"] == 26, f"Expected weeks capped at 26, got {data['weeks']}"
        
        print("✓ Weeks parameter validation works (max 26)")

    def test_quality_kpis_non_owner_forbidden(self):
        """Non-owner should get 403 Forbidden."""
        # Create a new session without auth
        session = requests.Session()
        resp = session.get(f"{BASE_URL}/api/admin/conviction/quality-kpis")
        assert resp.status_code in [401, 403], f"Expected 401/403 for unauthenticated, got {resp.status_code}"
        print("✓ Unauthenticated access correctly blocked")


class TestGuardShadowRegression:
    """Regression tests for guard-shadow endpoints."""

    def test_guard_shadow_summary(self, owner_session):
        """GET /api/admin/guard-shadow/summary should still work."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/guard-shadow/summary?hours=24")
        assert resp.status_code == 200, f"Guard shadow summary failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        # Verify expected fields exist
        assert "total" in data or "decisions" in data or "would_allow" in data, \
            f"Guard shadow summary missing expected fields: {data.keys()}"
        
        print(f"✓ Guard shadow summary endpoint working")
        print(f"  - Response keys: {list(data.keys())}")


class TestProofChainRegression:
    """Regression tests for proof-chain endpoints."""

    def test_proof_chain_entities(self, owner_session):
        """GET /api/admin/proof-chain/entities should still work."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/proof-chain/entities?limit=10")
        assert resp.status_code == 200, f"Proof chain entities failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        # Verify expected fields exist
        assert "entities" in data or "items" in data or "total" in data, \
            f"Proof chain entities missing expected fields: {data.keys()}"
        
        print(f"✓ Proof chain entities endpoint working")
        print(f"  - Response keys: {list(data.keys())}")

    def test_proof_chain_stats(self, owner_session):
        """GET /api/admin/proof-chain/stats should still work."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/proof-chain/stats")
        assert resp.status_code == 200, f"Proof chain stats failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        print(f"✓ Proof chain stats endpoint working")
        print(f"  - Response keys: {list(data.keys())}")


class TestConvictionCalibrationRegression:
    """Regression tests for existing conviction calibration endpoint."""

    def test_conviction_calibration(self, owner_session):
        """GET /api/admin/conviction/calibration should still work."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/conviction/calibration?days=30")
        assert resp.status_code == 200, f"Conviction calibration failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        # Verify expected fields
        assert "by_conviction" in data, "Missing 'by_conviction' field"
        assert "by_confidence" in data, "Missing 'by_confidence' field"
        assert "total_verified" in data, "Missing 'total_verified' field"
        
        print(f"✓ Conviction calibration endpoint working")
        print(f"  - total_verified: {data.get('total_verified')}")
        print(f"  - by_conviction buckets: {len(data.get('by_conviction', []))}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
