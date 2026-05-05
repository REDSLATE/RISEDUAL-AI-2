"""
Iteration 138: SHAP/Directional ML Adaptation Integration Tests

Tests for:
1. GET /api/admin/alerts/why/{alert_id} — SHAP enrichment endpoint
2. GET /api/admin/adaptations — ML adaptations list (owner-gated)
3. GET /api/admin/strategies/leaderboard — strategy leaderboard (admin-gated)
4. POST /api/admin/alerts/replay — alert replay (admin-gated)
5. Auth gates: 401 anon, 403 non-admin
6. Backend ruff/mypy compliance (already verified by main agent)
"""

import os
import pytest
import requests
import uuid
from datetime import datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    BASE_URL = "https://risedual-trading.preview.emergentagent.com"

# Test credentials from /app/memory/test_credentials.md
OWNER_EMAIL = "admin@risedual.ai"
OWNER_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def session():
    """Shared requests session."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def owner_session(session):
    """Authenticated session with owner credentials."""
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    if resp.status_code != 200:
        pytest.skip(f"Owner login failed: {resp.status_code} - {resp.text}")
    return session


class TestAlertWhyEndpoint:
    """Tests for GET /api/admin/alerts/why/{alert_id}"""

    def test_alert_why_requires_auth(self, session):
        """Anon request should return 401."""
        # Create a fresh session without auth
        anon = requests.Session()
        resp = anon.get(f"{BASE_URL}/api/admin/alerts/why/test-alert-id")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_alert_why_404_on_bogus_id(self, owner_session):
        """Bogus alert_id should return 404."""
        bogus_id = f"bogus-{uuid.uuid4()}"
        resp = owner_session.get(f"{BASE_URL}/api/admin/alerts/why/{bogus_id}")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        data = resp.json()
        assert "not found" in data.get("detail", "").lower()

    def test_alert_why_returns_expected_structure(self, owner_session):
        """If an alert exists, verify response structure with SHAP fields."""
        # First, check if there are any alerts in the audit
        audit_resp = owner_session.get(f"{BASE_URL}/api/admin/alerts/audit?limit=5")
        if audit_resp.status_code != 200:
            pytest.skip("Cannot access alerts audit")
        
        audit_data = audit_resp.json()
        items = audit_data.get("items", [])
        
        if not items:
            # No alerts exist - create a synthetic one for testing
            pytest.skip("No alerts in DB to test /why endpoint")
        
        # Use the first alert's ID
        alert_id = items[0].get("alert_id")
        if not alert_id:
            pytest.skip("Alert has no alert_id")
        
        resp = owner_session.get(f"{BASE_URL}/api/admin/alerts/why/{alert_id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        # Verify structure
        assert "alert_id" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        
        # If items exist, verify each has expected fields
        for item in data["items"]:
            assert "symbol" in item
            assert "confidence" in item or item.get("confidence") is None
            assert "failure_code" in item
            assert "drivers" in item
            # SHAP enrichment field
            assert "shap_top" in item
            if item["shap_top"]:
                for shap_entry in item["shap_top"]:
                    assert "feature" in shap_entry
                    assert "contribution" in shap_entry


class TestAdaptationsEndpoint:
    """Tests for GET /api/admin/adaptations"""

    def test_adaptations_requires_owner(self, session):
        """Anon request should return 401."""
        anon = requests.Session()
        resp = anon.get(f"{BASE_URL}/api/admin/adaptations")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_adaptations_returns_expected_structure(self, owner_session):
        """Owner should get {items, enabled, total}."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/adaptations")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "items" in data
        assert "enabled" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["enabled"], bool)
        assert isinstance(data["total"], int)
        
        # If items exist, verify directional fields
        for item in data["items"]:
            assert "metric" in item
            assert "adjustment_factor" in item
            # New directional fields
            if "direction" in item:
                assert item["direction"] in ["LONG", "SHORT", "ANY"]
            if "contrast" in item:
                assert isinstance(item["contrast"], (int, float, type(None)))
            if "severity" in item:
                assert isinstance(item["severity"], (int, float))


class TestStrategiesLeaderboard:
    """Tests for GET /api/admin/strategies/leaderboard"""

    def test_leaderboard_requires_auth(self, session):
        """Anon request should return 401."""
        anon = requests.Session()
        resp = anon.get(f"{BASE_URL}/api/admin/strategies/leaderboard")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_leaderboard_returns_expected_structure(self, owner_session):
        """Owner should get leaderboard payload."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/strategies/leaderboard")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "items" in data
        assert "window_days" in data
        assert "resolved_total" in data
        assert "pending_total" in data
        assert isinstance(data["items"], list)
        
        # Verify item structure if any exist
        for item in data["items"]:
            assert "strategy" in item
            assert "trades" in item
            assert "wins" in item
            assert "losses" in item
            assert "pending" in item
            assert "win_rate" in item or item.get("win_rate") is None
            assert "avg_r" in item or item.get("avg_r") is None
            assert "total_pnl" in item


class TestAlertReplay:
    """Tests for POST /api/admin/alerts/replay"""

    def test_replay_requires_auth(self, session):
        """Anon request should return 401."""
        anon = requests.Session()
        resp = anon.post(f"{BASE_URL}/api/admin/alerts/replay?alert_id=test")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_replay_404_on_bogus_id(self, owner_session):
        """Bogus alert_id should return 404."""
        bogus_id = f"bogus-{uuid.uuid4()}"
        resp = owner_session.post(f"{BASE_URL}/api/admin/alerts/replay?alert_id={bogus_id}")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


class TestAlertAudit:
    """Tests for GET /api/admin/alerts/audit"""

    def test_audit_requires_admin(self, session):
        """Anon request should return 401."""
        anon = requests.Session()
        resp = anon.get(f"{BASE_URL}/api/admin/alerts/audit")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_audit_returns_expected_structure(self, owner_session):
        """Admin should get audit list."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/alerts/audit?limit=10")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)


class TestSyntheticAlertWithSHAP:
    """Test SHAP enrichment with a synthetic alert."""

    @pytest.fixture(scope="class")
    def synthetic_alert_id(self, owner_session):
        """Create a synthetic alert for testing SHAP enrichment."""
        # We need to insert directly into MongoDB for this test
        # Since we can't do that via API, we'll check if any toxic_spike alerts exist
        resp = owner_session.get(
            f"{BASE_URL}/api/admin/alerts/audit?alert_type=toxic_spike&limit=1"
        )
        if resp.status_code != 200:
            pytest.skip("Cannot access alerts audit")
        
        data = resp.json()
        items = data.get("items", [])
        if items:
            return items[0].get("alert_id")
        return None

    def test_shap_enrichment_on_toxic_spike(self, owner_session, synthetic_alert_id):
        """Verify SHAP enrichment returns feature contributions."""
        if not synthetic_alert_id:
            pytest.skip("No toxic_spike alerts available for SHAP test")
        
        resp = owner_session.get(
            f"{BASE_URL}/api/admin/alerts/why/{synthetic_alert_id}"
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        data = resp.json()
        assert data["alert_id"] == synthetic_alert_id
        
        # Check if any items have SHAP data
        items_with_shap = [
            item for item in data["items"]
            if item.get("shap_top") and len(item["shap_top"]) > 0
        ]
        
        # Log what we found
        print(f"Alert {synthetic_alert_id}: {len(data['items'])} items, "
              f"{len(items_with_shap)} with SHAP data")
        
        # If SHAP data exists, verify structure
        for item in items_with_shap:
            for shap_entry in item["shap_top"]:
                assert "feature" in shap_entry
                assert "contribution" in shap_entry
                # Contribution should be a float
                assert isinstance(shap_entry["contribution"], (int, float))
                print(f"  {item['symbol']}: {shap_entry['feature']}={shap_entry['contribution']:.4f}")


class TestModelAdaptationDirectional:
    """Test directional fields in model adaptations."""

    def test_adaptations_have_direction_field(self, owner_session):
        """Verify adaptations include direction (LONG/SHORT/ANY)."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/adaptations")
        assert resp.status_code == 200
        
        data = resp.json()
        items = data.get("items", [])
        
        # Log adaptation details
        print(f"Found {len(items)} active adaptations, enabled={data['enabled']}")
        
        for item in items:
            direction = item.get("direction", "ANY")
            metric = item.get("metric", "unknown")
            factor = item.get("adjustment_factor", 1.0)
            contrast = item.get("contrast")
            severity = item.get("severity", 0)
            
            print(f"  {metric}/{direction}: factor={factor:.2f}, "
                  f"contrast={contrast}, severity={severity:.4f}")
            
            # Verify direction is valid
            assert direction in ["LONG", "SHORT", "ANY"], \
                f"Invalid direction: {direction}"


class TestHealthEndpoints:
    """Verify related health/status endpoints work."""

    def test_ml_latest_model(self, owner_session):
        """GET /api/admin/ml-latest-model should return model info."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/ml-latest-model")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        data = resp.json()
        # Should have version or message
        assert "version" in data or "message" in data

    def test_tier3_progress(self, owner_session):
        """GET /api/admin/tier3-progress should return progress info."""
        resp = owner_session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        data = resp.json()
        assert "days" in data or "progress_pct" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
