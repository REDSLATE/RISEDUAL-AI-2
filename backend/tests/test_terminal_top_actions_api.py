"""
API Integration Tests for Terminal Top-Actions endpoint (T2).

Tests the live /api/terminal/top-actions endpoint via HTTP requests.
Validates:
- Auth requirements (401 without auth)
- Response shape and contract
- Limit parameter validation (0 → 400, 999 → 400, 5 → max 5 actions)
- Existing endpoints unaffected (/api/terminal/signal/AAPL, /api/terminal/market-state)
"""

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def session():
    """Shared requests session."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_cookies(session):
    """Login and get auth cookies."""
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.status_code} - {resp.text}"
    # Cookies are automatically stored in session
    return session.cookies


class TestTopActionsAuth:
    """Auth requirement tests for /api/terminal/top-actions."""

    def test_top_actions_requires_auth_401(self, session):
        """GET /api/terminal/top-actions without auth returns 401."""
        # Use a fresh session without cookies
        fresh_session = requests.Session()
        resp = fresh_session.get(f"{BASE_URL}/api/terminal/top-actions")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print(f"✓ Unauthenticated request returns 401")


class TestTopActionsContract:
    """Response shape and contract tests."""

    def test_top_actions_returns_valid_shape(self, session, auth_cookies):
        """GET /api/terminal/top-actions with admin auth returns expected shape."""
        resp = session.get(f"{BASE_URL}/api/terminal/top-actions")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Validate envelope keys
        assert "as_of" in data, "Missing 'as_of' in response"
        assert "user_id" in data, "Missing 'user_id' in response"
        assert "limit" in data, "Missing 'limit' in response"
        assert "lookback_hours" in data, "Missing 'lookback_hours' in response"
        assert "enter_cap" in data, "Missing 'enter_cap' in response"
        assert "totals" in data, "Missing 'totals' in response"
        assert "actions" in data, "Missing 'actions' in response"
        
        # Validate totals shape
        totals = data["totals"]
        assert "manage" in totals, "Missing 'manage' in totals"
        assert "enter" in totals, "Missing 'enter' in totals"
        assert "watch" in totals, "Missing 'watch' in totals"
        assert "exit" in totals, "Missing 'exit' in totals"
        
        print(f"✓ Response shape valid: as_of={data['as_of']}, limit={data['limit']}")
        print(f"  Totals: manage={totals['manage']}, enter={totals['enter']}, watch={totals['watch']}, exit={totals['exit']}")
        print(f"  Actions count: {len(data['actions'])}")

    def test_top_actions_action_card_shape(self, session, auth_cookies):
        """Validate action card schema if actions exist."""
        resp = session.get(f"{BASE_URL}/api/terminal/top-actions?limit=10")
        assert resp.status_code == 200
        
        data = resp.json()
        if len(data["actions"]) == 0:
            pytest.skip("No actions in response - cannot validate card shape")
        
        action = data["actions"][0]
        expected_keys = {
            "rank", "kind", "symbol", "asset_type", "action", "priority_score",
            "conviction", "size_multiplier", "vetoes", "vetoes_count",
            "reason", "correlation_note", "decision_id", "decision_age_minutes",
            "shadow", "links",
        }
        missing = expected_keys - set(action.keys())
        assert not missing, f"Missing keys in action card: {missing}"
        
        # Validate conviction sub-object
        assert "tier" in action["conviction"], "Missing 'tier' in conviction"
        assert "score" in action["conviction"], "Missing 'score' in conviction"
        
        # Validate links sub-object
        assert "signal" in action["links"], "Missing 'signal' in links"
        
        # Validate kind is one of expected values
        valid_kinds = {"MANAGE_POSITION", "ENTER", "WATCH", "EXIT"}
        assert action["kind"] in valid_kinds, f"Invalid kind: {action['kind']}"
        
        print(f"✓ Action card shape valid: rank={action['rank']}, kind={action['kind']}, symbol={action['symbol']}")
        print(f"  Action: {action['action']}, conf={action['conviction']['score']}, priority={action['priority_score']}")
        print(f"  Reason: {action['reason'][:80]}...")


class TestTopActionsLimitValidation:
    """Limit parameter validation tests."""

    def test_limit_zero_returns_400(self, session, auth_cookies):
        """?limit=0 should return HTTP 400 (route layer validation)."""
        resp = session.get(f"{BASE_URL}/api/terminal/top-actions?limit=0")
        assert resp.status_code == 400, f"Expected 400 for limit=0, got {resp.status_code}"
        print(f"✓ limit=0 returns 400")

    def test_limit_999_returns_400(self, session, auth_cookies):
        """?limit=999 should return HTTP 400 (>50 cap)."""
        resp = session.get(f"{BASE_URL}/api/terminal/top-actions?limit=999")
        assert resp.status_code == 400, f"Expected 400 for limit=999, got {resp.status_code}"
        print(f"✓ limit=999 returns 400")

    def test_limit_5_returns_at_most_5(self, session, auth_cookies):
        """?limit=5 should return at most 5 actions."""
        resp = session.get(f"{BASE_URL}/api/terminal/top-actions?limit=5")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        data = resp.json()
        assert data["limit"] == 5, f"Expected limit=5 in response, got {data['limit']}"
        assert len(data["actions"]) <= 5, f"Expected at most 5 actions, got {len(data['actions'])}"
        print(f"✓ limit=5 returns {len(data['actions'])} actions (≤5)")


class TestExistingEndpointsUnaffected:
    """Verify existing terminal endpoints still work."""

    def test_signal_endpoint_still_works(self, session, auth_cookies):
        """GET /api/terminal/signal/AAPL still returns 200 with admin auth."""
        resp = session.get(f"{BASE_URL}/api/terminal/signal/AAPL")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} - {resp.text}"
        
        data = resp.json()
        assert data["symbol"] == "AAPL", f"Expected symbol=AAPL, got {data.get('symbol')}"
        print(f"✓ /api/terminal/signal/AAPL returns 200, symbol={data['symbol']}")

    def test_market_state_endpoint_still_works(self, session, auth_cookies):
        """GET /api/terminal/market-state still returns 200 with admin auth."""
        resp = session.get(f"{BASE_URL}/api/terminal/market-state")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} - {resp.text}"
        
        data = resp.json()
        assert "market_state" in data, "Missing 'market_state' in response"
        print(f"✓ /api/terminal/market-state returns 200, state={data['market_state']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
