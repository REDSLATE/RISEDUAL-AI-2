"""
API Integration Tests for Adversarial Cores 24h + Top Actions Stream endpoints.

Tests the new endpoints added in iteration 165:
- GET /api/admin/adversarial-cores/24h (owner auth required)
- GET /api/terminal/top-actions/stream (owner auth required, SSE)

Validates:
- Auth requirements (401 without auth)
- Response shape and contract
- Limit parameter validation for stream endpoint
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
def auth_token(session):
    """Login and get auth token."""
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.status_code} - {resp.text}"
    data = resp.json()
    return data.get("access_token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Auth headers with Bearer token."""
    return {"Authorization": f"Bearer {auth_token}"}


# ============================================================
# ADVERSARIAL CORES 24H TESTS
# ============================================================

class TestAdversarialCores24hAuth:
    """Auth requirement tests for /api/admin/adversarial-cores/24h."""

    def test_adversarial_cores_requires_auth_401(self, session):
        """GET /api/admin/adversarial-cores/24h without auth returns 401."""
        fresh_session = requests.Session()
        resp = fresh_session.get(f"{BASE_URL}/api/admin/adversarial-cores/24h")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("✓ Unauthenticated request returns 401")


class TestAdversarialCores24hContract:
    """Response shape and contract tests for adversarial-cores/24h."""

    def test_adversarial_cores_returns_valid_shape(self, session, auth_headers):
        """GET /api/admin/adversarial-cores/24h with owner auth returns expected shape."""
        resp = session.get(
            f"{BASE_URL}/api/admin/adversarial-cores/24h",
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Validate all required keys
        required_keys = {
            "enabled", "phase", "decisions_24h", "closed_24h", "decision_counts",
            "wins", "avg_edge_gap", "avg_bull_confidence", "avg_bear_confidence",
            "bull_win_rate", "bear_win_rate", "last_decision_at", "collection",
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Missing keys: {missing}"
        
        # Validate types
        assert isinstance(data["enabled"], bool), "enabled should be bool"
        assert data["phase"] in ("shadow", "risk_only", "veto", "full"), f"Invalid phase: {data['phase']}"
        assert isinstance(data["decisions_24h"], int), "decisions_24h should be int"
        assert isinstance(data["closed_24h"], int), "closed_24h should be int"
        assert isinstance(data["avg_edge_gap"], (int, float)), "avg_edge_gap should be numeric"
        assert isinstance(data["avg_bull_confidence"], (int, float)), "avg_bull_confidence should be numeric"
        assert isinstance(data["avg_bear_confidence"], (int, float)), "avg_bear_confidence should be numeric"
        
        # Validate decision_counts shape
        dc = data["decision_counts"]
        assert "LONG" in dc, "Missing LONG in decision_counts"
        assert "SHORT_OR_AVOID" in dc, "Missing SHORT_OR_AVOID in decision_counts"
        assert "NO_TRADE" in dc, "Missing NO_TRADE in decision_counts"
        
        # Validate wins shape
        wins = data["wins"]
        assert "bull" in wins, "Missing bull in wins"
        assert "bear" in wins, "Missing bear in wins"
        assert "neutral" in wins, "Missing neutral in wins"
        
        # Validate collection name
        assert data["collection"] == "crypto_adversarial_decision_log"
        
        print(f"✓ Response shape valid: enabled={data['enabled']}, phase={data['phase']}")
        print(f"  decisions_24h={data['decisions_24h']}, closed_24h={data['closed_24h']}")
        print(f"  avg_edge_gap={data['avg_edge_gap']}")

    def test_adversarial_cores_enabled_is_true(self, session, auth_headers):
        """Verify enabled=True when CRYPTO_ADVERSARIAL_ENABLED=1 is set."""
        resp = session.get(
            f"{BASE_URL}/api/admin/adversarial-cores/24h",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # Per the test request, enabled should be True
        assert data["enabled"] is True, f"Expected enabled=True, got {data['enabled']}"
        print("✓ enabled=True (CRYPTO_ADVERSARIAL_ENABLED=1 is set)")


# ============================================================
# TOP ACTIONS STREAM TESTS
# ============================================================

class TestTopActionsStreamAuth:
    """Auth requirement tests for /api/terminal/top-actions/stream."""

    def test_stream_requires_auth_401(self, session):
        """GET /api/terminal/top-actions/stream without auth returns 401."""
        fresh_session = requests.Session()
        resp = fresh_session.get(f"{BASE_URL}/api/terminal/top-actions/stream?limit=5")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("✓ Unauthenticated stream request returns 401")


class TestTopActionsStreamContract:
    """Response shape and contract tests for top-actions/stream."""

    def test_stream_returns_event_stream_content_type(self, session, auth_headers):
        """GET /api/terminal/top-actions/stream returns text/event-stream content-type."""
        resp = session.get(
            f"{BASE_URL}/api/terminal/top-actions/stream?limit=5",
            headers=auth_headers,
            stream=True,
            timeout=5,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type, f"Expected text/event-stream, got {content_type}"
        
        # Read first chunk to verify snapshot event
        first_chunk = ""
        for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
            first_chunk += chunk
            if "event: snapshot" in first_chunk:
                break
            if len(first_chunk) > 10000:
                break
        
        resp.close()
        
        assert "event: snapshot" in first_chunk, "Expected 'event: snapshot' in first frame"
        print("✓ Stream returns text/event-stream with snapshot event")

    def test_stream_snapshot_matches_top_actions_contract(self, session, auth_headers):
        """Verify the snapshot payload matches /api/terminal/top-actions contract.
        
        Uses iter_lines with a short timeout to avoid hanging on SSE keep-alive.
        """
        import json
        
        resp = session.get(
            f"{BASE_URL}/api/terminal/top-actions/stream?limit=5",
            headers=auth_headers,
            stream=True,
            timeout=3,
        )
        assert resp.status_code == 200
        
        # Read lines until we get the data line (SSE format: event: X\ndata: Y\n\n)
        data_line = None
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data_line = line[5:].strip()
                    break
        except Exception:
            pass
        finally:
            resp.close()
        
        assert data_line, "No data line found in SSE response"
        
        payload = json.loads(data_line)
        
        # Validate envelope keys (same as /api/terminal/top-actions)
        assert "as_of" in payload, "Missing 'as_of' in snapshot"
        assert "user_id" in payload, "Missing 'user_id' in snapshot"
        assert "limit" in payload, "Missing 'limit' in snapshot"
        assert "lookback_hours" in payload, "Missing 'lookback_hours' in snapshot"
        assert "enter_cap" in payload, "Missing 'enter_cap' in snapshot"
        assert "totals" in payload, "Missing 'totals' in snapshot"
        assert "actions" in payload, "Missing 'actions' in snapshot"
        
        # Validate totals shape
        totals = payload["totals"]
        assert "manage" in totals, "Missing 'manage' in totals"
        assert "enter" in totals, "Missing 'enter' in totals"
        assert "watch" in totals, "Missing 'watch' in totals"
        assert "exit" in totals, "Missing 'exit' in totals"
        
        print(f"✓ Snapshot matches top-actions contract: limit={payload['limit']}, actions={len(payload['actions'])}")


class TestTopActionsStreamLimitValidation:
    """Limit parameter validation tests for stream endpoint."""

    def test_stream_limit_999_returns_400(self, session, auth_headers):
        """?limit=999 should return HTTP 400 (>50 cap)."""
        resp = session.get(
            f"{BASE_URL}/api/terminal/top-actions/stream?limit=999",
            headers=auth_headers,
        )
        assert resp.status_code == 400, f"Expected 400 for limit=999, got {resp.status_code}"
        print("✓ limit=999 returns 400")

    def test_stream_limit_0_returns_400(self, session, auth_headers):
        """?limit=0 should return HTTP 400 (<1)."""
        resp = session.get(
            f"{BASE_URL}/api/terminal/top-actions/stream?limit=0",
            headers=auth_headers,
        )
        assert resp.status_code == 400, f"Expected 400 for limit=0, got {resp.status_code}"
        print("✓ limit=0 returns 400")

    def test_stream_limit_5_returns_200(self, session, auth_headers):
        """?limit=5 should return HTTP 200."""
        resp = session.get(
            f"{BASE_URL}/api/terminal/top-actions/stream?limit=5",
            headers=auth_headers,
            stream=True,
            timeout=3,
        )
        assert resp.status_code == 200, f"Expected 200 for limit=5, got {resp.status_code}"
        resp.close()
        print("✓ limit=5 returns 200")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
