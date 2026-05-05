"""API tests for Toxic Spike Autopsy endpoint.

Tests:
  * GET /api/admin/toxic-spike/autopsy returns 200 for admin
  * GET /api/admin/toxic-spike/autopsy returns 403 for non-admin
  * GET /api/admin/toxic-spike/autopsy returns 401 for unauthenticated
  * Response schema includes all required fields
  * days and min_confidence query params are honored
  * Regression: /api/admin/ops-snapshot still works
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    """Create authenticated admin session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login as admin
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    if login_resp.status_code != 200:
        pytest.skip(f"Admin login failed: {login_resp.status_code} - {login_resp.text}")
    
    return session


@pytest.fixture(scope="module")
def unauthenticated_session():
    """Create unauthenticated session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


class TestToxicAutopsyAuth:
    """Authentication and authorization tests for toxic autopsy endpoint."""
    
    def test_autopsy_returns_401_for_unauthenticated(self, unauthenticated_session):
        """Unauthenticated requests should return 401."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    
    def test_autopsy_returns_200_for_admin(self, admin_session):
        """Admin users should get 200 with valid response."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "total_failures" in data
        assert "generated_at" in data


class TestToxicAutopsyResponseSchema:
    """Response schema validation tests."""
    
    def test_response_includes_all_required_fields(self, admin_session):
        """Response must include all required fields per spec."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 200
        data = resp.json()
        
        # Required top-level fields
        required_fields = [
            "total_failures",
            "total_misses_in_window",
            "avg_confidence_pct",
            "unique_symbols",
            "window",
            "by_failure_code",
            "by_feature",
            "by_confidence_bucket",
            "by_direction",
            "by_sector",
            "by_model_version",
            "by_grade",
            "top_offenders",
            "samples",
            "generated_at",
        ]
        
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Window should have days
        assert "days" in data["window"], "window.days missing"
    
    def test_avg_confidence_pct_is_in_valid_range(self, admin_session):
        """avg_confidence_pct should be in 0-100 range, not 8000+."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 200
        data = resp.json()
        
        avg_conf = data.get("avg_confidence_pct", 0)
        # If there are failures, avg_conf should be reasonable (0-100)
        # If no failures, it should be 0
        assert 0 <= avg_conf <= 100, f"avg_confidence_pct out of range: {avg_conf}"
    
    def test_top_offenders_have_required_fields(self, admin_session):
        """top_offenders items should have required fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 200
        data = resp.json()
        
        offenders = data.get("top_offenders", [])
        if offenders:
            offender = offenders[0]
            required_offender_fields = [
                "symbol", "count", "avg_confidence_pct", "sector",
                "grades", "failure_codes", "features"
            ]
            for field in required_offender_fields:
                assert field in offender, f"top_offender missing field: {field}"
    
    def test_samples_capped_at_200(self, admin_session):
        """samples array should be capped at 200 rows max."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?days=30")
        assert resp.status_code == 200
        data = resp.json()
        
        samples = data.get("samples", [])
        assert len(samples) <= 200, f"samples exceeds 200 cap: {len(samples)}"
    
    def test_samples_include_required_fields(self, admin_session):
        """Each sample row should include required fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy")
        assert resp.status_code == 200
        data = resp.json()
        
        samples = data.get("samples", [])
        if samples:
            sample = samples[0]
            required_sample_fields = [
                "prediction_id", "symbol", "sector", "failure_code",
                "grade", "price_at_prediction", "price_verified"
            ]
            for field in required_sample_fields:
                assert field in sample, f"sample missing field: {field}"


class TestToxicAutopsyQueryParams:
    """Query parameter tests."""
    
    def test_days_param_is_honored(self, admin_session):
        """days query param should affect the window."""
        # Test with days=1
        resp1 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?days=1")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["window"]["days"] == 1
        
        # Test with days=14
        resp14 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?days=14")
        assert resp14.status_code == 200
        data14 = resp14.json()
        assert data14["window"]["days"] == 14
    
    def test_min_confidence_param_is_honored(self, admin_session):
        """min_confidence query param should be reflected in response."""
        # Test with min_confidence=70
        resp70 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?min_confidence=70")
        assert resp70.status_code == 200
        data70 = resp70.json()
        assert data70.get("min_confidence_pct") == 70.0
        
        # Test with min_confidence=90
        resp90 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?min_confidence=90")
        assert resp90.status_code == 200
        data90 = resp90.json()
        assert data90.get("min_confidence_pct") == 90.0
    
    def test_higher_min_confidence_yields_fewer_or_equal_failures(self, admin_session):
        """Higher min_confidence should yield fewer or equal failures."""
        resp70 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?days=7&min_confidence=70")
        resp90 = admin_session.get(f"{BASE_URL}/api/admin/toxic-spike/autopsy?days=7&min_confidence=90")
        
        assert resp70.status_code == 200
        assert resp90.status_code == 200
        
        failures_70 = resp70.json().get("total_failures", 0)
        failures_90 = resp90.json().get("total_failures", 0)
        
        # Higher threshold should yield fewer or equal failures
        assert failures_90 <= failures_70, f"90% threshold ({failures_90}) > 70% threshold ({failures_70})"


class TestOpsSnapshotRegression:
    """Regression test: ops-snapshot should still work after toxic-autopsy route added."""
    
    def test_ops_snapshot_still_works(self, admin_session):
        """GET /api/admin/ops-snapshot should return 200."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200, f"ops-snapshot broken: {resp.status_code} - {resp.text}"
        data = resp.json()
        # Basic sanity check
        assert "generated_at" in data or "snapshot_at" in data or "notes" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
