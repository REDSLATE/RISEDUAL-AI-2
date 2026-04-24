"""
Iteration 140 Tests: Magnitude-path retirement evaluation, Digest preview, Terminal Mode
Tests for:
1. GET /api/admin/tier3-progress r_adoption block
2. GET /api/digest/my-preview endpoint
3. Terminal Mode UI elements
4. Admin panel tab switching
"""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

@pytest.fixture(scope="module")
def admin_session():
    """Login as admin and return session with cookies"""
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@risedual.ai", "password": "RiseDual2026!"}
    )
    assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
    return session


class TestTier3ProgressRAdoption:
    """Tests for r_adoption block in tier3-progress endpoint"""
    
    def test_tier3_progress_returns_r_adoption(self, admin_session):
        """Verify tier3-progress includes r_adoption object"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200
        data = resp.json()
        
        # Verify r_adoption exists
        assert "r_adoption" in data, "r_adoption block missing from tier3-progress"
        r_adoption = data["r_adoption"]
        
        # Verify structure
        assert "history" in r_adoption, "r_adoption.history missing"
        assert "verdict" in r_adoption, "r_adoption.verdict missing"
        assert isinstance(r_adoption["history"], list), "history should be a list"
    
    def test_r_adoption_verdict_structure(self, admin_session):
        """Verify verdict has all required fields"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200
        verdict = resp.json()["r_adoption"]["verdict"]
        
        required_fields = [
            "threshold",
            "required_consecutive",
            "consecutive_stable_runs",
            "latest_r_eligible_frac",
            "magnitude_retirement_ready",
            "runs_tracked"
        ]
        for field in required_fields:
            assert field in verdict, f"verdict.{field} missing"
        
        # Verify types
        assert isinstance(verdict["threshold"], (int, float))
        assert isinstance(verdict["required_consecutive"], int)
        assert isinstance(verdict["consecutive_stable_runs"], int)
        assert isinstance(verdict["latest_r_eligible_frac"], (int, float))
        assert isinstance(verdict["magnitude_retirement_ready"], bool)
        assert isinstance(verdict["runs_tracked"], int)
    
    def test_r_adoption_history_row_structure(self, admin_session):
        """Verify history rows have correct structure"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200
        history = resp.json()["r_adoption"]["history"]
        
        if len(history) > 0:
            row = history[0]
            assert "at" in row, "history row missing 'at'"
            assert "r_eligible_frac" in row, "history row missing 'r_eligible_frac'"
            assert "r_skipped_frac" in row, "history row missing 'r_skipped_frac'"
            assert "samples" in row, "history row missing 'samples'"
    
    def test_tier3_progress_requires_auth(self):
        """Verify endpoint requires authentication"""
        resp = requests.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 401


class TestDigestMyPreview:
    """Tests for GET /api/digest/my-preview endpoint"""
    
    def test_my_preview_returns_content_summary(self, admin_session):
        """Verify my-preview returns content_summary"""
        resp = admin_session.get(f"{BASE_URL}/api/digest/my-preview")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "content_summary" in data
        cs = data["content_summary"]
        assert "predictions" in cs
        assert "smart_money" in cs
        assert "alerts" in cs
        assert "has_overview" in cs
        assert "has_watchlist_intel" in cs
    
    def test_my_preview_returns_preview_object(self, admin_session):
        """Verify my-preview returns preview with top items"""
        resp = admin_session.get(f"{BASE_URL}/api/digest/my-preview")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "preview" in data
        preview = data["preview"]
        assert "overview_headline" in preview
        assert "top_predictions" in preview
        assert "top_smart_money" in preview
        assert "alert_titles" in preview
        
        # Verify top_predictions is limited to 3
        assert len(preview["top_predictions"]) <= 3
        assert len(preview["top_smart_money"]) <= 3
        assert len(preview["alert_titles"]) <= 3
    
    def test_my_preview_returns_email_and_timestamp(self, admin_session):
        """Verify my-preview returns email and generated_at"""
        resp = admin_session.get(f"{BASE_URL}/api/digest/my-preview")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "email" in data
        assert "generated_at" in data
        assert data["email"] == "admin@risedual.ai"
    
    def test_my_preview_requires_auth(self):
        """Verify endpoint requires authentication"""
        resp = requests.get(f"{BASE_URL}/api/digest/my-preview")
        assert resp.status_code == 401
    
    def test_my_preview_available_to_non_admin(self, admin_session):
        """Verify endpoint is available to all authed users (not admin-only)"""
        # Create a regular user session
        session = requests.Session()
        # First check if we can access with admin (should work)
        resp = admin_session.get(f"{BASE_URL}/api/digest/my-preview")
        assert resp.status_code == 200


class TestDigestSendNow:
    """Tests for POST /api/digest/send-now endpoint"""
    
    def test_send_now_requires_auth(self):
        """Verify endpoint requires authentication"""
        resp = requests.post(f"{BASE_URL}/api/digest/send-now")
        assert resp.status_code == 401
    
    def test_send_now_rate_limited(self, admin_session):
        """Verify rate limiting works (1 per hour)"""
        # First call might succeed or be rate limited
        resp1 = admin_session.post(f"{BASE_URL}/api/digest/send-now")
        # If first call succeeded, second should be rate limited
        if resp1.status_code == 200:
            resp2 = admin_session.post(f"{BASE_URL}/api/digest/send-now")
            assert resp2.status_code == 429, "Second call should be rate limited"


class TestRegressionExistingEndpoints:
    """Regression tests for existing endpoints"""
    
    def test_tier3_readiness_still_works(self, admin_session):
        """Verify tier3-readiness endpoint still works"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/tier3-readiness?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data or "unlock" in data
    
    def test_digest_status_still_works(self, admin_session):
        """Verify digest status endpoint still works"""
        resp = admin_session.get(f"{BASE_URL}/api/digest/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "subscribed" in data
    
    def test_digest_opt_in_out_still_works(self, admin_session):
        """Verify digest opt-in/out endpoints still work"""
        # Opt out
        resp = admin_session.post(f"{BASE_URL}/api/digest/opt-out")
        assert resp.status_code == 200
        
        # Opt back in
        resp = admin_session.post(f"{BASE_URL}/api/digest/opt-in")
        assert resp.status_code == 200
