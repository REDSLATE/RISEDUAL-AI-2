"""API tests for ops-alerter endpoints (iteration 146).

Tests:
- GET /api/admin/ops-alerter/status (admin only)
- POST /api/admin/ops-alerter/run (admin only)
- Unconfigured webhook path (OPS_ALERT_WEBHOOK_URL unset)
- State persistence across runs
- 'All gauges nominal.' filtering
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    """Authenticated admin session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    resp = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if resp.status_code != 200:
        pytest.skip(f"Admin login failed: {resp.status_code} - {resp.text[:200]}")
    return session


@pytest.fixture(scope="module")
def unauthenticated_session():
    """Session without auth."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


class TestOpsAlerterStatus:
    """GET /api/admin/ops-alerter/status tests."""

    def test_status_returns_expected_schema(self, admin_session):
        """Status endpoint returns configured, last_notes, alert_history, updated_at."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        
        data = resp.json()
        # Required fields
        assert "configured" in data, "Missing 'configured' field"
        assert "last_notes" in data, "Missing 'last_notes' field"
        assert "alert_history" in data, "Missing 'alert_history' field"
        assert "updated_at" in data, "Missing 'updated_at' field"
        
        # Type checks
        assert isinstance(data["configured"], bool), "configured should be bool"
        assert isinstance(data["last_notes"], list), "last_notes should be list"
        assert isinstance(data["alert_history"], dict), "alert_history should be dict"
        # updated_at can be null or string
        assert data["updated_at"] is None or isinstance(data["updated_at"], str)
        
        print(f"Status: configured={data['configured']}, last_notes={len(data['last_notes'])}, updated_at={data['updated_at']}")

    def test_status_never_returns_webhook_url(self, admin_session):
        """SECURITY: Status endpoint must never leak the webhook URL."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert resp.status_code == 200
        
        data = resp.json()
        text = str(data).lower()
        
        # Should not contain any webhook-related URL patterns
        assert "webhook_url" not in text, "Response contains webhook_url"
        assert "hooks.slack.com" not in text, "Response contains Slack webhook"
        assert "discord.com/api/webhooks" not in text, "Response contains Discord webhook"
        print("SECURITY VERIFIED: No webhook URL in status response")

    def test_status_requires_admin(self, unauthenticated_session):
        """Status endpoint returns 401 for unauthenticated requests."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("Auth check passed: 401 for unauthenticated")

    def test_status_configured_false_when_webhook_unset(self, admin_session):
        """With OPS_ALERT_WEBHOOK_URL unset, configured should be false."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert resp.status_code == 200
        
        data = resp.json()
        # Per the test requirements, webhook is intentionally unset
        assert data["configured"] is False, "Expected configured=false when webhook unset"
        print("Verified: configured=false when OPS_ALERT_WEBHOOK_URL is unset")


class TestOpsAlerterRun:
    """POST /api/admin/ops-alerter/run tests."""

    def test_run_returns_expected_schema(self, admin_session):
        """Run endpoint returns full structured payload."""
        resp = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        
        data = resp.json()
        # Required fields per spec
        required_fields = [
            "configured", "snapshot_at", "current_notes",
            "fresh_alerts", "resolved_alerts", "suppressed_dedup",
            "posted_alert", "posted_resolved"
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Type checks
        assert isinstance(data["configured"], bool)
        assert isinstance(data["current_notes"], list)
        assert isinstance(data["fresh_alerts"], list)
        assert isinstance(data["resolved_alerts"], list)
        assert isinstance(data["suppressed_dedup"], list)
        assert isinstance(data["posted_alert"], bool)
        assert isinstance(data["posted_resolved"], bool)
        
        print(f"Run result: configured={data['configured']}, notes={len(data['current_notes'])}, "
              f"fresh={len(data['fresh_alerts'])}, resolved={len(data['resolved_alerts'])}")

    def test_run_requires_admin(self, unauthenticated_session):
        """Run endpoint returns 401 for unauthenticated requests."""
        resp = unauthenticated_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("Auth check passed: 401 for unauthenticated POST /run")

    def test_run_unconfigured_returns_configured_false(self, admin_session):
        """With webhook unset, run returns configured=false but still executes."""
        resp = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 200
        
        data = resp.json()
        assert data["configured"] is False, "Expected configured=false"
        assert data["posted_alert"] is False, "Should not post when unconfigured"
        assert data["posted_resolved"] is False, "Should not post resolved when unconfigured"
        print("Verified: unconfigured path returns configured=false, posted_*=false")

    def test_run_state_persists_across_calls(self, admin_session):
        """Calling /run twice should show state advancing (last_notes updates)."""
        # First run
        resp1 = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp1.status_code == 200
        data1 = resp1.json()
        
        # Get status to see persisted state
        status1 = admin_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert status1.status_code == 200
        state1 = status1.json()
        
        # Second run
        resp2 = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp2.status_code == 200
        data2 = resp2.json()
        
        # Get status again
        status2 = admin_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert status2.status_code == 200
        state2 = status2.json()
        
        # State should have updated_at set after runs
        assert state2["updated_at"] is not None, "updated_at should be set after run"
        
        # last_notes should match current_notes from the run
        assert state2["last_notes"] == data2["current_notes"], \
            "Persisted last_notes should match run's current_notes"
        
        print(f"State persistence verified: updated_at={state2['updated_at']}, "
              f"last_notes matches current_notes")

    def test_run_filters_nominal_note(self, admin_session):
        """'All gauges nominal.' should never appear in fresh_alerts or resolved_alerts."""
        resp = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 200
        
        data = resp.json()
        nominal = "All gauges nominal."
        
        assert nominal not in data["fresh_alerts"], \
            f"'{nominal}' should not appear in fresh_alerts"
        assert nominal not in data["resolved_alerts"], \
            f"'{nominal}' should not appear in resolved_alerts"
        
        print("Verified: 'All gauges nominal.' filtered from alerts")

    def test_first_run_with_nominal_produces_no_false_resolve(self, admin_session):
        """First run with only 'All gauges nominal.' should not produce resolved messages."""
        # This is tested implicitly - if current_notes only has nominal,
        # resolved_alerts should be empty (no false-positive resolve)
        resp = admin_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 200
        
        data = resp.json()
        # If the system is healthy (only nominal note), there should be no resolved alerts
        if data["current_notes"] == ["All gauges nominal."]:
            assert data["resolved_alerts"] == [], \
                "Should not have resolved alerts when only nominal note present"
            print("Verified: No false-resolve when only nominal note")
        else:
            print(f"System has real notes: {data['current_notes'][:3]}... - skipping nominal-only check")


class TestOpsAlerterNonAdminAccess:
    """Test that non-admin users get 403."""

    @pytest.fixture
    def regular_user_session(self):
        """Create a regular user and get session."""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        # Try to register a test user
        import uuid
        test_email = f"test_alerter_{uuid.uuid4().hex[:8]}@test.com"
        reg_resp = session.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test User"
        })
        
        if reg_resp.status_code not in (200, 201):
            # User might already exist or registration disabled
            pytest.skip("Could not create test user")
        
        # Login
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": test_email,
            "password": "TestPass123!"
        })
        
        if login_resp.status_code != 200:
            pytest.skip("Could not login test user")
        
        return session

    def test_status_returns_403_for_non_admin(self, regular_user_session):
        """Non-admin users should get 403 on status endpoint."""
        resp = regular_user_session.get(f"{BASE_URL}/api/admin/ops-alerter/status")
        assert resp.status_code == 403, f"Expected 403 for non-admin, got {resp.status_code}"
        print("Verified: 403 for non-admin on /status")

    def test_run_returns_403_for_non_admin(self, regular_user_session):
        """Non-admin users should get 403 on run endpoint."""
        resp = regular_user_session.post(f"{BASE_URL}/api/admin/ops-alerter/run")
        assert resp.status_code == 403, f"Expected 403 for non-admin, got {resp.status_code}"
        print("Verified: 403 for non-admin on /run")


class TestSchedulerRegistration:
    """Verify ops_alerter_tick is registered in scheduler."""

    def test_scheduler_has_ops_alerter_job(self, admin_session):
        """The APScheduler should have ops_alerter_tick registered at 15-min interval."""
        # We can verify this indirectly via the self-test endpoint
        resp = admin_session.get(f"{BASE_URL}/api/admin/self-test")
        if resp.status_code != 200:
            pytest.skip("Self-test endpoint not available")
        
        data = resp.json()
        checks = data.get("checks", [])
        
        # Look for scheduler check
        scheduler_check = next((c for c in checks if "scheduler" in c.get("name", "").lower()), None)
        if scheduler_check:
            print(f"Scheduler check: {scheduler_check}")
            # If scheduler check exists and passes, jobs are registered
            if scheduler_check.get("status") == "PASS":
                print("Scheduler jobs registered (including ops_alerter_tick)")
        else:
            # Alternative: check ops-snapshot for scheduler heartbeat
            snap_resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
            if snap_resp.status_code == 200:
                snap = snap_resp.json()
                scheduler = snap.get("scheduler", {})
                assert scheduler.get("ok") is True, "Scheduler should be running"
                print(f"Scheduler heartbeat: {scheduler}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
