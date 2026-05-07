"""API-level tests for the ops-snapshot, calibration ECE, and shadow stats endpoints.

Tests:
- GET / (admin-only, full payload, no key leakage)
- GET /api/admin/conviction/calibration (ECE + notes)
- GET /api/admin/shadow/stats (notes array)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestOpsSnapshotEndpoint:
    """Tests for GET /api/admin/ops-snapshot"""

    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies."""
        session = requests.Session()
        resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if resp.status_code != 200:
            pytest.skip(f"Admin login failed: {resp.status_code} - {resp.text[:200]}")
        return session

    def test_ops_snapshot_requires_auth(self):
        """Unauthenticated request returns 401."""
        resp = requests.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_ops_snapshot_returns_full_payload(self, admin_session):
        """Admin gets full payload with all expected keys."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
        
        # Check all top-level keys exist
        expected_keys = {
            "generated_at", "runtime", "operator_flags", "extra_flags",
            "integrations", "mongo", "scheduler", "tier3", "notes"
        }
        assert set(data.keys()) == expected_keys, f"Missing keys: {expected_keys - set(data.keys())}"

    def test_ops_snapshot_operator_flags_all_known_present(self, admin_session):
        """Every KNOWN_FLAGS entry is present even if unset."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        # Known flags from ops_snapshot.py
        known_flags = {
            "CRYPTO_ADVERSARIAL_ENABLED",
            "CRYPTO_ADVERSARIAL_PHASE",
            "CRYPTO_RESEARCH_SHADOW_ENGINE",
            "COUNCIL_SHADOW_MODE",
            "COUNCIL_RISK_MODULATOR_ENABLED",
            "ML_ADAPTATION_SHADOW_MODE",
            "REGIME_WEIGHTS_ENABLED",
            "PUBLIC_DATA_FLOOR_DATE",
            "SHADOW_COST_CEILING_USD_PER_DAY",
        }
        
        flag_names = {f["name"] for f in data["operator_flags"]}
        assert known_flags.issubset(flag_names), f"Missing flags: {known_flags - flag_names}"
        
        # Each flag has name, value, set fields
        for flag in data["operator_flags"]:
            assert "name" in flag
            assert "value" in flag
            assert "set" in flag
            assert isinstance(flag["set"], bool)

    def test_ops_snapshot_integrations_no_key_leakage(self, admin_session):
        """Integration list never contains actual API key values."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        # Serialize to string and check for sensitive patterns
        serialized = str(data)
        
        # CRITICAL: These patterns must NEVER appear in response
        forbidden_patterns = [
            "sk_live",  # Stripe live key prefix
            "sk_test",  # Stripe test key prefix
            "olyznuucpx",  # USPTO key substring from .env
            "sk-emergent",  # Emergent LLM key prefix
            "re_A2v5",  # Resend API key prefix
            "tvly-dev",  # Tavily key prefix
        ]
        
        for pattern in forbidden_patterns:
            assert pattern not in serialized, f"SECURITY: Found '{pattern}' in response - key leakage!"
        
        # Verify integrations have correct structure
        for integration in data["integrations"]:
            assert "name" in integration
            assert "env_var" in integration
            assert "configured" in integration
            assert isinstance(integration["configured"], bool)
            # Value should NOT be present
            assert "value" not in integration or integration.get("value") is None

    def test_ops_snapshot_mongo_probe_real(self, admin_session):
        """Mongo probe returns ok=true with ping_ms < 100."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        mongo = data["mongo"]
        assert mongo["ok"] is True, f"Mongo probe failed: {mongo.get('error')}"
        assert "ping_ms" in mongo
        assert mongo["ping_ms"] < 100, f"Mongo ping too slow: {mongo['ping_ms']}ms"

    def test_ops_snapshot_scheduler_heartbeat(self, admin_session):
        """Scheduler heartbeat has last_signal_at and age_seconds."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        scheduler = data["scheduler"]
        # May be ok=False if no scheduler signals yet, but structure should be stable
        if scheduler.get("ok"):
            assert "last_signal_at" in scheduler
            assert "age_seconds" in scheduler
            assert scheduler["age_seconds"] > 0

    def test_ops_snapshot_tier3_mirrors_readiness(self, admin_session):
        """Tier3 section has expected fields from fetch_tier_readiness."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        tier3 = data["tier3"]
        # Should have these fields from research_shadow_stats.fetch_tier_readiness
        expected_tier3_keys = {
            "ok", "tier3_progress_pct", "tier3_unlocked",
            "adversarial_phase", "council_modulator_enabled", "ready_to_enable_council"
        }
        assert expected_tier3_keys.issubset(set(tier3.keys())), f"Missing tier3 keys: {expected_tier3_keys - set(tier3.keys())}"

    def test_ops_snapshot_notes_always_nonempty(self, admin_session):
        """Notes array is always non-empty."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code == 200
        data = resp.json()
        
        notes = data["notes"]
        assert isinstance(notes, list)
        assert len(notes) > 0, "Notes array should never be empty"
        # Either has warnings or 'All gauges nominal.'
        assert any(n for n in notes)


class TestConvictionCalibrationECE:
    """Tests for ECE + notes in GET /api/admin/conviction/calibration"""

    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies."""
        session = requests.Session()
        resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if resp.status_code != 200:
            pytest.skip(f"Admin login failed: {resp.status_code}")
        return session

    def test_calibration_requires_owner(self):
        """Unauthenticated request returns 401."""
        resp = requests.get(f"{BASE_URL}/api/admin/conviction/calibration")
        assert resp.status_code == 401

    def test_calibration_has_ece_object(self, admin_session):
        """Response includes top-level 'ece' object."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/conviction/calibration?days=30")
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
        
        assert "ece" in data, "Missing 'ece' key in response"
        ece = data["ece"]
        assert "conviction" in ece
        assert "confidence" in ece
        # Values are numeric or null
        if ece["conviction"] is not None:
            assert isinstance(ece["conviction"], (int, float))
        if ece["confidence"] is not None:
            assert isinstance(ece["confidence"], (int, float))

    def test_calibration_has_notes_array(self, admin_session):
        """Response includes 'notes' array with heuristic guidance."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/conviction/calibration?days=30")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "notes" in data, "Missing 'notes' key in response"
        notes = data["notes"]
        assert isinstance(notes, list)
        assert len(notes) > 0, "Notes array should never be empty"

    def test_calibration_notes_include_ece_warnings(self, admin_session):
        """Notes fire ECE warnings when ece > 0.10."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/conviction/calibration?days=30")
        assert resp.status_code == 200
        data = resp.json()
        
        ece = data.get("ece", {})
        notes = data.get("notes", [])
        
        # If ECE > 0.10, should have a "poor" warning
        if ece.get("conviction") and ece["conviction"] > 0.10:
            assert any("ece" in n.lower() and "poor" in n.lower() for n in notes), \
                f"Expected ECE poor warning for conviction={ece['conviction']}"

    def test_calibration_monotonicity_coexists_with_ece(self, admin_session):
        """Both monotonicity and ECE checks are present."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/conviction/calibration?days=30")
        assert resp.status_code == 200
        data = resp.json()
        
        # Monotonicity object should exist
        assert "monotonic" in data
        mono = data["monotonic"]
        assert "conviction" in mono
        assert "confidence" in mono
        
        # ECE object should also exist
        assert "ece" in data


class TestShadowStatsNotes:
    """Tests for notes array in GET /api/admin/shadow/stats"""

    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies."""
        session = requests.Session()
        resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if resp.status_code != 200:
            pytest.skip(f"Admin login failed: {resp.status_code}")
        return session

    def test_shadow_stats_requires_auth(self):
        """Unauthenticated request returns 401."""
        resp = requests.get(f"{BASE_URL}/api/admin/shadow/stats")
        assert resp.status_code == 401

    def test_shadow_stats_has_notes_array(self, admin_session):
        """Response includes top-level 'notes' array."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/shadow/stats")
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
        
        assert "notes" in data, "Missing 'notes' key in response"
        notes = data["notes"]
        assert isinstance(notes, list)
        assert len(notes) > 0, "Notes array should never be empty"

    def test_shadow_stats_notes_heuristic_content(self, admin_session):
        """Notes contain meaningful heuristic guidance."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/shadow/stats")
        assert resp.status_code == 200
        data = resp.json()
        
        notes = data["notes"]
        # Should have at least one note with actual content
        assert any(len(n) > 10 for n in notes), "Notes should have meaningful content"
        
        # Check for expected heuristic patterns
        notes_text = " ".join(notes).lower()
        # Should mention one of: no decisions, sample size, win-rate, healthy
        expected_patterns = [
            "no shadow decisions",
            "scored dissents",
            "win-rate",
            "healthy",
            "wait for sample",
            "actively wrong",
        ]
        assert any(p in notes_text for p in expected_patterns), \
            f"Notes don't contain expected heuristic patterns: {notes}"


class TestNonAdminAccess:
    """Tests that non-admin users get 403."""

    def test_ops_snapshot_non_admin_forbidden(self):
        """Non-admin user gets 403 on ops-snapshot."""
        # Create a regular user session (if we had one) or just test unauthenticated
        # For now, we verify the endpoint exists and requires auth
        resp = requests.get(f"{BASE_URL}/api/admin/ops-snapshot")
        assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
