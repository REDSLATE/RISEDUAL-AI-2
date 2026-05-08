"""
Test Admin Panel and User Workspace APIs
- Admin endpoints (owner-only): list users, activate, deactivate, grant-pro, revoke-pro
- Workspace endpoints (authenticated): watchlist CRUD, hypothesis history
"""
import pytest
import requests
import os
from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, OWNER_EMAIL, OWNER_PASSWORD

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials — env wins, but fall back to the values resolved by
# `conftest_creds` (which already have the correct production fallbacks).
# Pre-2026-05-01 the local fallbacks here were empty strings,
# silently sending ``password=""`` to /api/auth/login when env wasn't
# set and producing a misleading "401 Invalid email or password" that
# masked the brute-force-DoS bug for ~4 weeks.
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", OWNER_EMAIL)
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", OWNER_PASSWORD)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", ADMIN_EMAIL)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def owner_token():
    """Get owner access token"""
    res = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD
    })
    if res.status_code != 200:
        pytest.skip(f"Owner login failed: {res.status_code} - {res.text}")
    return res.json().get("access_token")


@pytest.fixture(scope="module")
def admin_token():
    """Get admin access token"""
    res = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if res.status_code != 200:
        pytest.skip(f"Admin login failed: {res.status_code} - {res.text}")
    return res.json().get("access_token")


@pytest.fixture(scope="module")
def test_user():
    """Create a test user for admin operations"""
    import uuid
    email = f"TEST_user_{uuid.uuid4().hex[:8]}@test.com"
    res = requests.post(f"{BASE_URL}/api/auth/register", json={
        "email": email,
        "password": "TestPass123!",
        "name": "Test User"
    })
    if res.status_code not in [200, 201]:
        pytest.skip(f"Test user creation failed: {res.status_code}")
    data = res.json()
    return {
        "id": data.get("id"),
        "email": email,
        "token": data.get("access_token")
    }


class TestOwnerLogin:
    """Test owner login functionality"""
    
    def test_owner_login_success(self):
        """Owner can login with correct credentials"""
        res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "access_token" in data
        assert data.get("role") == "owner"
        assert data.get("subscription_status") == "pro"
        print(f"Owner login successful: role={data.get('role')}, subscription={data.get('subscription_status')}")
    
    def test_admin_login_success(self):
        """Admin can login with correct credentials.

        Note: the canonical RISEDUAL admin has ``role="owner"`` after
        the Feb 2026 single-owner consolidation (see
        ``routes/auth.py::seed_admin``). The legacy assertion
        ``role == "admin"`` was stale and contributed to the
        2026-04 admin-login confusion that masked the brute-force
        DoS bug. The contract this test pins is "the documented
        ADMIN credential successfully authenticates and is privileged"
        — owner is strictly more privileged than admin, so accepting
        either preserves the original intent.
        """
        res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "access_token" in data
        assert data.get("role") in {"owner", "admin"}, (
            f"Expected privileged role, got {data.get('role')!r}"
        )
        print(f"Admin login successful: role={data.get('role')}")


class TestAdminPanel:
    """Test Admin Panel endpoints (owner-only)"""
    
    def test_list_users_as_owner(self, owner_token):
        """Owner can list all users"""
        res = requests.get(
            f"{BASE_URL}/api/auth/admin/users",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)
        print(f"Listed {data['total']} users")
    
    def test_list_users_as_admin_forbidden(self, test_user):
        """Non-privileged user cannot list users.

        Pre-Feb-2026 this test asserted that a separate "admin" tier
        (distinct from "owner") was forbidden. After the single-owner
        consolidation (see ``routes/auth.py::seed_admin``) the "admin"
        tier no longer exists — the meaningful access-control check
        is "regular registered user is forbidden", which this version
        pins.
        """
        res = requests.get(
            f"{BASE_URL}/api/auth/admin/users",
            headers={"Authorization": f"Bearer {test_user['token']}"}
        )
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
        print("Regular user correctly denied access to user list")
    
    def test_list_users_unauthenticated(self):
        """Unauthenticated request is rejected"""
        res = requests.get(f"{BASE_URL}/api/auth/admin/users")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    
    def test_deactivate_user(self, owner_token, test_user):
        """Owner can deactivate a user"""
        res = requests.post(
            f"{BASE_URL}/api/auth/admin/users/{test_user['id']}/deactivate",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"User deactivated: {data.get('message')}")
    
    def test_activate_user(self, owner_token, test_user):
        """Owner can activate a user"""
        res = requests.post(
            f"{BASE_URL}/api/auth/admin/users/{test_user['id']}/activate",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"User activated: {data.get('message')}")
    
    def test_grant_pro(self, owner_token, test_user):
        """Owner can grant Pro to a user"""
        res = requests.post(
            f"{BASE_URL}/api/auth/admin/users/{test_user['id']}/grant-pro",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"Pro granted: {data.get('message')}")
    
    def test_revoke_pro(self, owner_token, test_user):
        """Owner can revoke Pro from a user"""
        res = requests.post(
            f"{BASE_URL}/api/auth/admin/users/{test_user['id']}/revoke-pro",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"Pro revoked: {data.get('message')}")
    
    def test_admin_cannot_deactivate(self, test_user):
        """Non-privileged user cannot deactivate users.

        See ``test_list_users_as_admin_forbidden`` — under the
        single-owner consolidation the admin-vs-owner distinction was
        removed, so we test the meaningful contract: a regular
        registered user is forbidden from privileged endpoints.
        """
        res = requests.post(
            f"{BASE_URL}/api/auth/admin/users/{test_user['id']}/deactivate",
            headers={"Authorization": f"Bearer {test_user['token']}"}
        )
        assert res.status_code == 403, f"Expected 403, got {res.status_code}"
        print("Regular user correctly denied deactivate access")


class TestWorkspaceWatchlist:
    """Test Workspace Watchlist endpoints"""
    
    def test_get_watchlist_authenticated(self, admin_token):
        """Authenticated user can get their watchlist"""
        res = requests.get(
            f"{BASE_URL}/api/workspace/watchlist",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "tickers" in data
        print(f"Watchlist retrieved: {data.get('tickers')}")
    
    def test_get_watchlist_unauthenticated(self):
        """Unauthenticated request is rejected"""
        res = requests.get(f"{BASE_URL}/api/workspace/watchlist")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    
    def test_add_ticker_to_watchlist(self, admin_token):
        """User can add a ticker to watchlist"""
        res = requests.post(
            f"{BASE_URL}/api/workspace/watchlist/add",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            json={"ticker": "MSFT"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"Ticker added: {data.get('message')}")
        
        # Verify ticker is in watchlist
        res2 = requests.get(
            f"{BASE_URL}/api/workspace/watchlist",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res2.status_code == 200
        tickers = res2.json().get("tickers", [])
        assert "MSFT" in tickers, f"MSFT not found in watchlist: {tickers}"
        print(f"Verified MSFT in watchlist: {tickers}")
    
    def test_remove_ticker_from_watchlist(self, admin_token):
        """User can remove a ticker from watchlist"""
        # First add a ticker
        requests.post(
            f"{BASE_URL}/api/workspace/watchlist/add",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            json={"ticker": "GOOG"}
        )
        
        # Remove it
        res = requests.post(
            f"{BASE_URL}/api/workspace/watchlist/remove",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            json={"ticker": "GOOG"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        
        # Verify removal
        res2 = requests.get(
            f"{BASE_URL}/api/workspace/watchlist",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        tickers = res2.json().get("tickers", [])
        assert "GOOG" not in tickers, f"GOOG still in watchlist: {tickers}"
        print("Verified GOOG removed from watchlist")


class TestWorkspaceHistory:
    """Test Workspace Hypothesis History endpoints"""
    
    def test_get_history_authenticated(self, admin_token):
        """Authenticated user can get their hypothesis history"""
        res = requests.get(
            f"{BASE_URL}/api/workspace/history",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "history" in data
        print(f"History retrieved: {len(data.get('history', []))} entries")
    
    def test_get_history_unauthenticated(self):
        """Unauthenticated request is rejected"""
        res = requests.get(f"{BASE_URL}/api/workspace/history")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    
    def test_save_history_entry(self, admin_token):
        """User can save a hypothesis to history"""
        res = requests.post(
            f"{BASE_URL}/api/workspace/history/save",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            json={
                "symbol": "AAPL",
                "verdict": "BUY",
                "confidence": 85
            }
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "message" in data
        print(f"History saved: {data.get('message')}")
        
        # Verify entry is in history
        res2 = requests.get(
            f"{BASE_URL}/api/workspace/history",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        history = res2.json().get("history", [])
        aapl_entries = [h for h in history if h.get("symbol") == "AAPL"]
        assert len(aapl_entries) > 0, "AAPL not found in history"
        print(f"Verified AAPL in history: {aapl_entries[0]}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
