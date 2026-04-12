"""
Iteration 43: Code Quality Sweep Regression Tests
Tests for refactored code paths:
- server.py startup_event split into 4 helper functions
- AuthModal split into AuthForm + ForgotPasswordForm
- AlertsPanel uses NotificationItem
- StrategyBuilder uses StrategyPreview
- PromoBanner uses sessionStorage instead of localStorage
"""
import pytest
import requests
import os
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD

# Test credentials from environment
@pytest.fixture(scope="module")
def session():
    """Shared requests session with cookies"""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_session(session):
    """Authenticated admin session"""
    resp = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return session


class TestServerStartup:
    """Tests for server.py startup_event split into 4 helper functions"""
    
    def test_api_root_responds(self, session):
        """Verify server started successfully - _wire_db_to_routes worked"""
        resp = session.get(f"{BASE_URL}/api/")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("message") == "RISEDUAL AI API - Ready"
        print("PASS: API root responds - server startup successful")
    
    def test_cache_warmup_sectors(self, session):
        """Verify _start_cache_warmup pre-populated sector heatmap"""
        resp = session.get(f"{BASE_URL}/api/sectors/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert "sectors" in data
        assert len(data["sectors"]) >= 10
        print(f"PASS: Sector heatmap cached - {len(data['sectors'])} sectors")
    
    def test_cache_warmup_world_events(self, session):
        """Verify _start_cache_warmup pre-populated world events"""
        resp = session.get(f"{BASE_URL}/api/world-events")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_events" in data
        assert data["total_events"] > 0
        print(f"PASS: World events cached - {data['total_events']} events")
    
    def test_cache_warmup_foreign_markets(self, session):
        """Verify _start_cache_warmup pre-populated foreign markets"""
        resp = session.get(f"{BASE_URL}/api/foreign-markets")
        assert resp.status_code == 200
        data = resp.json()
        assert "asia" in data
        assert "europe" in data
        assert "americas" in data
        print(f"PASS: Foreign markets cached - {len(data['asia'])} Asia, {len(data['europe'])} Europe")
    
    def test_test_credentials_written(self):
        """Verify _write_test_credentials created the file"""
        creds_path = "/app/memory/test_credentials.md"
        assert os.path.exists(creds_path), "test_credentials.md not found"
        with open(creds_path) as f:
            content = f.read()
        assert "Owner" in content
        assert "Admin" in content
        assert "httpOnly" in content
        print("PASS: test_credentials.md written correctly")


class TestAuthWithCookies:
    """Tests for auth login flow with httpOnly cookies"""
    
    def test_login_sets_cookies(self, session):
        """Verify POST /api/auth/login returns tokens and sets cookies"""
        resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"PASS: Login successful - role={data['role']}, subscription={data['subscription_status']}")
    
    def test_auth_me_with_cookies(self, admin_session):
        """Verify GET /api/auth/me works with cookie auth"""
        resp = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"PASS: /api/auth/me returns user - {data['email']}")
    
    def test_login_invalid_credentials(self, session):
        """Verify login fails with wrong password"""
        resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "wrongpassword"
        })
        assert resp.status_code == 401
        print("PASS: Invalid credentials rejected with 401")


class TestAdminCacheEndpoints:
    """Tests for admin cache management endpoints"""
    
    def test_cache_stats_returns_data(self, admin_session):
        """Verify GET /api/admin/cache-stats returns hit_rate/misses/entries"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "hit_rate" in data
        assert "total_requests" in data
        assert "hits" in data
        assert "misses" in data
        print(f"PASS: Cache stats - hit_rate={data['hit_rate']}%, entries={len(data['entries'])}")
    
    def test_cache_stats_has_warmup_entries(self, admin_session):
        """Verify cache has pre-warmed entries from startup"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert resp.status_code == 200
        data = resp.json()
        entry_keys = [e["key"] for e in data["entries"]]
        # At least sector_heatmap should be cached from warmup
        assert "sector_heatmap" in entry_keys or len(entry_keys) > 0
        print(f"PASS: Cache has {len(entry_keys)} entries: {entry_keys}")


class TestCoreAPIEndpoints:
    """Tests for core API endpoints still working after refactoring"""
    
    def test_sectors_heatmap_200(self, session):
        """Verify /api/sectors/heatmap returns 200"""
        resp = session.get(f"{BASE_URL}/api/sectors/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert "sectors" in data
        assert "market_summary" in data
        print(f"PASS: /api/sectors/heatmap - {len(data['sectors'])} sectors")
    
    def test_world_events_200(self, session):
        """Verify /api/world-events returns 200"""
        resp = session.get(f"{BASE_URL}/api/world-events")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_events" in data
        assert "high_impact_events" in data
        print(f"PASS: /api/world-events - {data['total_events']} events")
    
    def test_foreign_markets_200(self, session):
        """Verify /api/foreign-markets returns 200"""
        resp = session.get(f"{BASE_URL}/api/foreign-markets")
        assert resp.status_code == 200
        data = resp.json()
        assert "asia" in data
        assert "europe" in data
        assert "americas" in data
        assert "commodities" in data
        print(f"PASS: /api/foreign-markets - all regions present")
    
    def test_status_endpoint(self, session):
        """Verify /api/status returns 200"""
        resp = session.get(f"{BASE_URL}/api/status")
        assert resp.status_code == 200
        print("PASS: /api/status endpoint working")


class TestStrategyEndpoints:
    """Tests for strategy builder endpoints (StrategyPreview component uses these)"""
    
    def test_strategy_list_authenticated(self, admin_session):
        """Verify /api/strategy/list works for authenticated users"""
        resp = admin_session.get(f"{BASE_URL}/api/strategy/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        print(f"PASS: /api/strategy/list - {len(data['strategies'])} saved strategies")


class TestNotificationEndpoints:
    """Tests for notification endpoints (NotificationItem component uses these)"""
    
    def test_notifications_authenticated(self, admin_session):
        """Verify /api/notifications works for authenticated users"""
        resp = admin_session.get(f"{BASE_URL}/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert "unread_count" in data
        print(f"PASS: /api/notifications - {len(data['notifications'])} notifications, {data['unread_count']} unread")


class TestForgotPasswordEndpoint:
    """Tests for forgot password endpoint (ForgotPasswordForm component uses this)"""
    
    def test_forgot_password_accepts_email(self, session):
        """Verify /api/auth/forgot-password accepts email"""
        resp = session.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": "test@example.com"
        })
        # Should return 200 even for non-existent email (security best practice)
        assert resp.status_code == 200
        print("PASS: /api/auth/forgot-password accepts email")


class TestPromoEndpoints:
    """Tests for promo endpoints (PromoBanner component uses these)"""
    
    def test_promo_active(self, session):
        """Verify /api/promo/active returns promo data"""
        resp = session.get(f"{BASE_URL}/api/promo/active")
        # May return 200 with null promo if no active promo
        assert resp.status_code == 200
        data = resp.json()
        # promo can be null if no active campaign
        print(f"PASS: /api/promo/active - promo={'active' if data.get('promo') else 'none'}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
