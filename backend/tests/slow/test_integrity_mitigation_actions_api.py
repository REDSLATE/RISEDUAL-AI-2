"""
API Integration Tests for Integrity Mitigation Actions (Iteration 163)

Tests the two new mitigation actions:
- BLOCK_NEW_BOTS: blocks POST /api/bots and PATCH /api/bots/{id}/toggle (enabled=true)
- FREEZE_SIZING_OVERRIDES: blocks POST /api/admin/guard-shadow/policy/promote and /clear

Also tests:
- Summary endpoint returns block_new_bots and freeze_sizing_overrides flags
- Invalid action validation (BOGUS_ACTION returns 400)
- No regression: /api/bots works when no mitigation active
"""

import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    BASE_URL = "https://risedual-trading.preview.emergentagent.com"

# Test credentials
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestIntegrityMitigationActionsAPI:
    """End-to-end API tests for BLOCK_NEW_BOTS and FREEZE_SIZING_OVERRIDES actions."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies."""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login to get auth cookies
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.user = login_resp.json()
        assert self.user.get("role") == "owner", "Expected owner role"
        yield
        # Cleanup: ensure test rules are deleted
        self._cleanup_test_rules()

    def _cleanup_test_rules(self):
        """Delete any test rules created during tests."""
        for rule_id in ["TEST_block_new_bots_rule", "TEST_freeze_sizing_rule", "TEST_bogus_action_rule"]:
            try:
                self.session.delete(f"{BASE_URL}/api/admin/data-integrity/alert-rules/{rule_id}")
            except Exception:
                pass
        # Also clean up any integrity_mitigations rows from test rules
        # (they auto-expire via TTL, but we can't directly delete via API)

    # ── Test 1: Login baseline ──────────────────────────────────────

    def test_login_baseline(self):
        """POST /api/auth/login with admin credentials works."""
        resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("email") == ADMIN_EMAIL
        assert data.get("role") == "owner"

    # ── Test 2: Summary includes new flags ──────────────────────────

    def test_summary_includes_block_new_bots_and_freeze_sizing_flags(self):
        """GET /api/admin/data-integrity/summary returns block_new_bots and freeze_sizing_overrides."""
        resp = self.session.get(f"{BASE_URL}/api/admin/data-integrity/summary")
        assert resp.status_code == 200
        data = resp.json()
        
        # Check mitigation block exists
        assert "mitigation" in data, "Missing 'mitigation' key in summary"
        mitigation = data["mitigation"]
        
        # Check new flags exist (default to false when nothing active)
        assert "block_new_bots" in mitigation, "Missing 'block_new_bots' in mitigation"
        assert "freeze_sizing_overrides" in mitigation, "Missing 'freeze_sizing_overrides' in mitigation"
        assert isinstance(mitigation["block_new_bots"], bool)
        assert isinstance(mitigation["freeze_sizing_overrides"], bool)

    # ── Test 3: BLOCK_NEW_BOTS action flow ──────────────────────────

    def test_block_new_bots_action_flow(self):
        """
        Full BLOCK_NEW_BOTS flow:
        1. Create alert rule with action=BLOCK_NEW_BOTS
        2. Evaluate-now fires the rule
        3. Verify summary.mitigation.block_new_bots = true
        4. POST /api/bots returns 423 with error_code=integrity_block_new_bots
        5. PATCH /api/bots/{id}/toggle with enabled=true returns 423
        6. Cleanup
        """
        rule_id = "TEST_block_new_bots_rule"
        
        # Step 1: Create alert rule with BLOCK_NEW_BOTS action
        rule_payload = {
            "rule_id": rule_id,
            "metric": "unknown_direction_tokens",
            "comparator": "gte",
            "threshold": 0,  # Auto-breaches
            "window_hours": 24,
            "channels": [],
            "enabled": True,
            "throttle_hours": 0,  # No throttle for testing
            "notes": "Test rule for BLOCK_NEW_BOTS",
            "mitigation": {
                "action": "BLOCK_NEW_BOTS",
                "params": {"reason": "test block new bots"},
            },
            "mitigation_ttl_minutes": 1,  # Short TTL for fast cleanup
        }
        create_resp = self.session.post(
            f"{BASE_URL}/api/admin/data-integrity/alert-rules",
            json=rule_payload,
        )
        assert create_resp.status_code == 200, f"Failed to create rule: {create_resp.text}"
        
        # Step 2: Evaluate-now to fire the rule
        eval_resp = self.session.post(f"{BASE_URL}/api/admin/data-integrity/alert-rules/evaluate-now")
        assert eval_resp.status_code == 200, f"Evaluate-now failed: {eval_resp.text}"
        
        # Step 3: Verify summary shows block_new_bots = true
        summary_resp = self.session.get(f"{BASE_URL}/api/admin/data-integrity/summary")
        assert summary_resp.status_code == 200
        mitigation = summary_resp.json().get("mitigation", {})
        assert mitigation.get("block_new_bots") is True, f"Expected block_new_bots=true, got: {mitigation}"
        
        # Step 4: POST /api/bots should return 423
        bot_payload = {
            "type": "signal",
            "name": "test_blocked_bot",
            "mode": "paper",
            "config": {},
        }
        bot_resp = self.session.post(f"{BASE_URL}/api/bots", json=bot_payload)
        assert bot_resp.status_code == 423, f"Expected 423, got {bot_resp.status_code}: {bot_resp.text}"
        bot_error = bot_resp.json()
        assert bot_error.get("detail", {}).get("error_code") == "integrity_block_new_bots", f"Wrong error_code: {bot_error}"
        
        # Step 5: PATCH /api/bots/{id}/toggle with enabled=true should return 423
        # Use a fake bot_id - the 423 should fire before 404
        toggle_resp = self.session.patch(
            f"{BASE_URL}/api/bots/000000000000000000000000/toggle",
            json={"enabled": True},
        )
        assert toggle_resp.status_code == 423, f"Expected 423 on toggle, got {toggle_resp.status_code}: {toggle_resp.text}"
        toggle_error = toggle_resp.json()
        assert toggle_error.get("detail", {}).get("error_code") == "integrity_block_new_bots", f"Wrong error_code on toggle: {toggle_error}"
        
        # Step 6: Cleanup - delete the rule
        del_resp = self.session.delete(f"{BASE_URL}/api/admin/data-integrity/alert-rules/{rule_id}")
        assert del_resp.status_code == 200

    # ── Test 4: FREEZE_SIZING_OVERRIDES action flow ─────────────────

    def test_freeze_sizing_overrides_action_flow(self):
        """
        Full FREEZE_SIZING_OVERRIDES flow:
        1. Create alert rule with action=FREEZE_SIZING_OVERRIDES
        2. Evaluate-now fires the rule
        3. Verify summary.mitigation.freeze_sizing_overrides = true
        4. POST /api/admin/guard-shadow/policy/promote returns 423
        5. POST /api/admin/guard-shadow/policy/clear returns 423
        6. Cleanup
        """
        rule_id = "TEST_freeze_sizing_rule"
        
        # Step 1: Create alert rule with FREEZE_SIZING_OVERRIDES action
        rule_payload = {
            "rule_id": rule_id,
            "metric": "unknown_direction_tokens",
            "comparator": "gte",
            "threshold": 0,  # Auto-breaches
            "window_hours": 24,
            "channels": [],
            "enabled": True,
            "throttle_hours": 0,
            "notes": "Test rule for FREEZE_SIZING_OVERRIDES",
            "mitigation": {
                "action": "FREEZE_SIZING_OVERRIDES",
                "params": {"reason": "test freeze sizing overrides"},
            },
            "mitigation_ttl_minutes": 1,
        }
        create_resp = self.session.post(
            f"{BASE_URL}/api/admin/data-integrity/alert-rules",
            json=rule_payload,
        )
        assert create_resp.status_code == 200, f"Failed to create rule: {create_resp.text}"
        
        # Step 2: Evaluate-now to fire the rule
        eval_resp = self.session.post(f"{BASE_URL}/api/admin/data-integrity/alert-rules/evaluate-now")
        assert eval_resp.status_code == 200, f"Evaluate-now failed: {eval_resp.text}"
        
        # Step 3: Verify summary shows freeze_sizing_overrides = true
        summary_resp = self.session.get(f"{BASE_URL}/api/admin/data-integrity/summary")
        assert summary_resp.status_code == 200
        mitigation = summary_resp.json().get("mitigation", {})
        assert mitigation.get("freeze_sizing_overrides") is True, f"Expected freeze_sizing_overrides=true, got: {mitigation}"
        
        # Step 4: POST /api/admin/guard-shadow/policy/promote should return 423
        promote_payload = {
            "flag": "enforce_adversarial",
            "value": False,
            "note": "test promote during freeze",
        }
        promote_resp = self.session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/promote",
            json=promote_payload,
        )
        assert promote_resp.status_code == 423, f"Expected 423 on promote, got {promote_resp.status_code}: {promote_resp.text}"
        promote_error = promote_resp.json()
        assert promote_error.get("detail", {}).get("error_code") == "integrity_freeze_sizing_overrides", f"Wrong error_code on promote: {promote_error}"
        
        # Step 5: POST /api/admin/guard-shadow/policy/clear should return 423
        clear_payload = {"flag": "enforce_adversarial"}
        clear_resp = self.session.post(
            f"{BASE_URL}/api/admin/guard-shadow/policy/clear",
            json=clear_payload,
        )
        assert clear_resp.status_code == 423, f"Expected 423 on clear, got {clear_resp.status_code}: {clear_resp.text}"
        clear_error = clear_resp.json()
        assert clear_error.get("detail", {}).get("error_code") == "integrity_freeze_sizing_overrides", f"Wrong error_code on clear: {clear_error}"
        
        # Step 6: Cleanup - delete the rule
        del_resp = self.session.delete(f"{BASE_URL}/api/admin/data-integrity/alert-rules/{rule_id}")
        assert del_resp.status_code == 200

    # ── Test 5: Invalid action validation ───────────────────────────

    def test_bogus_action_returns_400(self):
        """POST /api/admin/data-integrity/alert-rules with action=BOGUS_ACTION returns 400."""
        rule_payload = {
            "rule_id": "TEST_bogus_action_rule",
            "metric": "unknown_direction_tokens",
            "comparator": "gte",
            "threshold": 1,
            "window_hours": 24,
            "channels": [],
            "enabled": True,
            "throttle_hours": 12,
            "notes": "Test rule with invalid action",
            "mitigation": {
                "action": "BOGUS_ACTION",
                "params": {},
            },
            "mitigation_ttl_minutes": 60,
        }
        resp = self.session.post(
            f"{BASE_URL}/api/admin/data-integrity/alert-rules",
            json=rule_payload,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        error = resp.json()
        assert error.get("detail", {}).get("error_code") == "unsupported_mitigation_action", f"Wrong error_code: {error}"
        assert "BOGUS_ACTION" in str(error.get("detail", {}).get("received", "")), f"Error should mention received action: {error}"

    # ── Test 6: No regression - bots work when no mitigation ────────

    def test_bots_work_when_no_mitigation_active(self):
        """POST /api/bots succeeds when no BLOCK_NEW_BOTS mitigation is active."""
        # First verify no block_new_bots mitigation is active
        summary_resp = self.session.get(f"{BASE_URL}/api/admin/data-integrity/summary")
        assert summary_resp.status_code == 200
        mitigation = summary_resp.json().get("mitigation", {})
        
        # If block_new_bots is active from a previous test, wait for TTL
        if mitigation.get("block_new_bots"):
            time.sleep(65)  # Wait for 1-minute TTL to expire
            summary_resp = self.session.get(f"{BASE_URL}/api/admin/data-integrity/summary")
            mitigation = summary_resp.json().get("mitigation", {})
        
        # Now test bot creation
        if not mitigation.get("block_new_bots"):
            bot_payload = {
                "type": "signal",
                "name": "test_allowed_bot",
                "mode": "paper",
                "config": {},
            }
            bot_resp = self.session.post(f"{BASE_URL}/api/bots", json=bot_payload)
            # Should succeed (200 or 201) or fail for non-mitigation reasons (400, etc.)
            # but NOT 423
            assert bot_resp.status_code != 423, f"Got 423 when no mitigation should be active: {bot_resp.text}"
            
            # If bot was created, clean it up
            if bot_resp.status_code in (200, 201):
                bot_data = bot_resp.json()
                bot_id = bot_data.get("id") or bot_data.get("_id")
                if bot_id:
                    self.session.delete(f"{BASE_URL}/api/bots/{bot_id}")

    # ── Test 7: Toggle with enabled=false always allowed ────────────

    def test_toggle_disabled_always_allowed(self):
        """PATCH /api/bots/{id}/toggle with enabled=false is always allowed regardless of mitigation."""
        # This test verifies the code path that allows disabling bots even during mitigation
        # We use a fake bot_id - the point is to verify the 423 doesn't fire for enabled=false
        toggle_resp = self.session.patch(
            f"{BASE_URL}/api/bots/000000000000000000000000/toggle",
            json={"enabled": False},
        )
        # Should NOT be 423 - it should be 400 (bad bot_id) or 404 (not found)
        # The key assertion is that it's not blocked by mitigation
        assert toggle_resp.status_code != 423, f"Got 423 for enabled=false which should always be allowed: {toggle_resp.text}"


# ── Standalone test runner ──────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
