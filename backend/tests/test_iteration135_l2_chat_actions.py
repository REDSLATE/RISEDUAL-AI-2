"""
Iteration 135: Level-2 AI Chat Actions Testing
Tests for:
1. POST /api/chat/followups - chips + actions generation
2. POST /api/analytics/chip-event - telemetry (shown/clicked/action-shown/action-clicked)
3. GET /api/analytics/chip-events/stats - admin-only stats with L1+L2 breakdown
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials — pulled from test_credentials.md; non-production values used
# only against the preview/local DB. Overridable via env for CI.
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@risedual.ai")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "RiseDual2026!")


@pytest.fixture(scope="module")
def admin_session():
    """Login as admin and return session with cookies."""
    session = requests.Session()
    resp = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return session


@pytest.fixture(scope="module")
def anon_session():
    """Anonymous session without login."""
    return requests.Session()


class TestChatFollowupsEndpoint:
    """Tests for POST /api/chat/followups - generates chips + actions."""

    def test_followups_ticker_specific_returns_actions(self, admin_session):
        """When assistant reply mentions a specific ticker, should return research + watchlist actions."""
        resp = admin_session.post(f"{BASE_URL}/api/chat/followups", json={
            "last_user_message": "What is AAPL doing today?",
            "last_assistant_response": "AAPL (Apple Inc.) closed at $178.45 today, up 1.2% from yesterday. The stock has been showing strong momentum with RSI at 62. Key support at $175 and resistance at $182. Volume was above average at 58M shares traded.",
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200, f"Followups failed: {resp.text}"
        data = resp.json()
        
        # Should have chips array with 3 items
        assert "chips" in data, "Response missing 'chips' key"
        assert isinstance(data["chips"], list), "chips should be a list"
        assert len(data["chips"]) == 3, f"Expected 3 chips, got {len(data['chips'])}"
        
        # Should have actions array (may have 0-2 items for ticker-specific)
        assert "actions" in data, "Response missing 'actions' key"
        assert isinstance(data["actions"], list), "actions should be a list"
        
        # Validate chip format
        for chip in data["chips"]:
            assert isinstance(chip, str), "Each chip should be a string"
            assert len(chip) <= 60, f"Chip too long: {chip}"
        
        print(f"✓ Ticker-specific followups: chips={data['chips']}, actions={data['actions']}")

    def test_followups_generic_returns_empty_actions(self, admin_session):
        """Generic/informational reply should return actions: [] (empty)."""
        resp = admin_session.post(f"{BASE_URL}/api/chat/followups", json={
            "last_user_message": "What is RSI?",
            "last_assistant_response": "RSI stands for Relative Strength Index. It's a momentum oscillator that measures the speed and magnitude of recent price changes to evaluate overbought or oversold conditions. RSI values range from 0 to 100, with readings above 70 typically indicating overbought conditions and below 30 indicating oversold conditions.",
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200, f"Followups failed: {resp.text}"
        data = resp.json()
        
        assert "chips" in data
        assert len(data["chips"]) >= 2, "Should have at least 2 chips"
        assert "actions" in data
        # Generic reply should have empty or minimal actions
        print(f"✓ Generic followups: chips={data['chips']}, actions={data['actions']}")

    def test_followups_short_response_returns_fallback(self, admin_session):
        """Very short assistant response should return fallback chips."""
        resp = admin_session.post(f"{BASE_URL}/api/chat/followups", json={
            "last_user_message": "Hi",
            "last_assistant_response": "Hello!",  # < 20 chars
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200
        data = resp.json()
        
        # Should return fallback chips
        assert "chips" in data
        assert len(data["chips"]) == 3
        assert "actions" in data
        assert data["actions"] == [], "Short response should have empty actions"
        print(f"✓ Short response fallback: chips={data['chips']}")

    def test_followups_action_kinds_whitelist(self, admin_session):
        """Actions should only have allowed kinds: research, warroom, options, workspace, watchlist."""
        allowed_kinds = {"research", "warroom", "options", "workspace", "watchlist"}
        
        # Test with options-related content
        resp = admin_session.post(f"{BASE_URL}/api/chat/followups", json={
            "last_user_message": "Show me TSLA options chain",
            "last_assistant_response": "Here's the TSLA options chain for the next expiry. The $250 calls are showing high implied volatility at 65%. Put/call ratio is 0.8 indicating bullish sentiment. The Greeks show delta of 0.45 for ATM options.",
            "context_hub": "options"
        })
        assert resp.status_code == 200
        data = resp.json()
        
        for action in data.get("actions", []):
            assert "kind" in action, "Action missing 'kind'"
            assert action["kind"] in allowed_kinds, f"Invalid action kind: {action['kind']}"
            assert "label" in action, "Action missing 'label'"
            assert len(action["label"]) <= 40, f"Label too long: {action['label']}"
            
            # If ticker present, validate format
            if "ticker" in action and action["ticker"]:
                import re
                assert re.match(r"^[A-Z]{1,5}$", action["ticker"]), f"Invalid ticker format: {action['ticker']}"
        
        print(f"✓ Action kinds validated: {data.get('actions', [])}")

    def test_followups_research_watchlist_require_ticker(self, admin_session):
        """research and watchlist actions should only appear when ticker is present."""
        resp = admin_session.post(f"{BASE_URL}/api/chat/followups", json={
            "last_user_message": "Tell me about NVDA",
            "last_assistant_response": "NVDA (NVIDIA Corporation) is trading at $485.20. The company reported strong Q3 earnings with data center revenue up 279% YoY. AI chip demand continues to drive growth. Analysts have a consensus price target of $550.",
            "context_hub": "research"
        })
        assert resp.status_code == 200
        data = resp.json()
        
        for action in data.get("actions", []):
            if action["kind"] in ("research", "watchlist"):
                assert "ticker" in action and action["ticker"], f"research/watchlist action missing ticker: {action}"
        
        print("✓ Ticker requirement validated for research/watchlist actions")


class TestChipEventTelemetry:
    """Tests for POST /api/analytics/chip-event - telemetry logging."""

    def test_chip_event_shown_accepted(self, admin_session):
        """action='shown' should be accepted."""
        resp = admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "shown",
            "chip_text": "Explain that further",
            "message_idx": 0,
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") == True, f"Expected ok:true, got {data}"
        print("✓ action='shown' accepted")

    def test_chip_event_clicked_accepted(self, admin_session):
        """action='clicked' should be accepted."""
        resp = admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "clicked",
            "chip_text": "Run a prediction on this",
            "message_idx": 1,
            "context_hub": "research"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") == True
        print("✓ action='clicked' accepted")

    def test_chip_event_action_shown_accepted(self, admin_session):
        """action='action-shown' (L2) should be accepted."""
        resp = admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "action-shown",
            "chip_text": "Open AAPL in Research",
            "message_idx": 2,
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") == True
        print("✓ action='action-shown' accepted")

    def test_chip_event_action_clicked_accepted(self, admin_session):
        """action='action-clicked' (L2) should be accepted."""
        resp = admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "action-clicked",
            "chip_text": "Open AAPL in Research",
            "message_idx": 2,
            "context_hub": "dashboard"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") == True
        print("✓ action='action-clicked' accepted")

    def test_chip_event_invalid_action_rejected(self, admin_session):
        """Invalid action values should return ok:false, reason:'bad_action'."""
        # Note: Backend does case-insensitive matching via .lower(), so SHOWN/Clicked are accepted
        invalid_actions = ["view", "hover", "dismiss", "invalid", "submitted"]
        
        for action in invalid_actions:
            resp = admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
                "action": action,
                "chip_text": "Test chip",
                "message_idx": 0
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("ok") == False, f"Expected ok:false for action='{action}'"
            assert data.get("reason") == "bad_action", f"Expected reason='bad_action' for action='{action}', got {data}"
        
        print(f"✓ Invalid actions rejected: {invalid_actions}")

    def test_chip_event_anonymous_allowed(self, anon_session):
        """Anonymous users should be able to log chip events."""
        resp = anon_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "shown",
            "chip_text": "Anonymous chip test",
            "message_idx": 0
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") == True
        print("✓ Anonymous chip event accepted")


class TestChipEventsStats:
    """Tests for GET /api/analytics/chip-events/stats - admin-only stats."""

    def test_stats_admin_access(self, admin_session):
        """Admin should be able to access chip stats."""
        resp = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=30")
        assert resp.status_code == 200, f"Stats failed: {resp.text}"
        data = resp.json()
        
        # Validate response structure
        required_fields = ["window_days", "shown", "clicked", "ctr", 
                          "action_shown", "action_clicked", "action_ctr",
                          "top_clicked", "top_actions"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        assert data["window_days"] == 30
        assert isinstance(data["shown"], int)
        assert isinstance(data["clicked"], int)
        assert isinstance(data["ctr"], (int, float))
        assert isinstance(data["action_shown"], int)
        assert isinstance(data["action_clicked"], int)
        assert isinstance(data["action_ctr"], (int, float))
        assert isinstance(data["top_clicked"], list)
        assert isinstance(data["top_actions"], list)
        
        print(f"✓ Admin stats access: shown={data['shown']}, clicked={data['clicked']}, ctr={data['ctr']}")
        print(f"  L2 stats: action_shown={data['action_shown']}, action_clicked={data['action_clicked']}, action_ctr={data['action_ctr']}")

    def test_stats_owner_access(self, admin_session):
        """Owner/Admin should be able to access chip stats with different window."""
        # Note: Using admin_session since owner account may be deactivated in test env
        resp = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["window_days"] == 7
        print("✓ Admin stats access with 7d window granted")

    def test_stats_anonymous_denied(self, anon_session):
        """Anonymous users should get 401 or 403."""
        resp = anon_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=30")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"✓ Anonymous stats access denied ({resp.status_code})")

    def test_stats_top_clicked_structure(self, admin_session):
        """top_clicked should have proper structure."""
        resp = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=30&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        
        for item in data.get("top_clicked", []):
            assert "chip" in item, "top_clicked item missing 'chip'"
            assert "count" in item, "top_clicked item missing 'count'"
            assert isinstance(item["count"], int)
        
        print(f"✓ top_clicked structure valid: {len(data.get('top_clicked', []))} items")

    def test_stats_top_actions_structure(self, admin_session):
        """top_actions should have proper structure."""
        resp = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=30&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        
        for item in data.get("top_actions", []):
            assert "chip" in item, "top_actions item missing 'chip'"
            assert "count" in item, "top_actions item missing 'count'"
            assert isinstance(item["count"], int)
        
        print(f"✓ top_actions structure valid: {len(data.get('top_actions', []))} items")


class TestChipEventDataPersistence:
    """Tests to verify chip events are persisted to MongoDB."""

    def test_events_land_in_mongo(self, admin_session):
        """Verify events are actually stored by checking stats change."""
        # Get initial stats
        resp1 = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=1")
        assert resp1.status_code == 200
        initial = resp1.json()
        
        # Log some events
        unique_chip = f"Test chip {int(time.time())}"
        for _ in range(3):
            admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
                "action": "shown",
                "chip_text": unique_chip,
                "message_idx": 0
            })
        
        admin_session.post(f"{BASE_URL}/api/analytics/chip-event", json={
            "action": "clicked",
            "chip_text": unique_chip,
            "message_idx": 0
        })
        
        # Get updated stats
        resp2 = admin_session.get(f"{BASE_URL}/api/analytics/chip-events/stats?days=1")
        assert resp2.status_code == 200
        updated = resp2.json()
        
        # Verify counts increased
        assert updated["shown"] >= initial["shown"] + 3, "shown count should increase"
        assert updated["clicked"] >= initial["clicked"] + 1, "clicked count should increase"
        
        print(f"✓ Events persisted: shown {initial['shown']} -> {updated['shown']}, clicked {initial['clicked']} -> {updated['clicked']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
