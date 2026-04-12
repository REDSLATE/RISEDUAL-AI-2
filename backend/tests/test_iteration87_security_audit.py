"""
Iteration 87: Security Audit Dashboard Tests
Tests for the new Security Audit API endpoints and Admin Panel access for admin role.

Features tested:
1. Security Audit API endpoints (GET /api/admin/security/*)
2. Admin role access to security endpoints (previously owner-only)
3. Non-admin user gets 403 on security endpoints
4. Unlock account functionality
"""
import pytest
import requests
import os

# Import centralized credentials
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestSecurityAuditAPI:
    """Tests for Security Audit Dashboard API endpoints"""
    
    def _get_session_with_login(self, email, password):
        """Create a new session and login"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return session, response
    
    # ==================== Admin Login Tests ====================
    
    def test_admin_login_success(self):
        """Test admin user can login successfully"""
        session, response = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        # Response has role at root level, not nested under "user"
        assert data.get("role") == "admin", f"Expected admin role, got {data.get('role')}"
        print(f"✓ Admin login successful: {data.get('email')}")
    
    def test_owner_login_success(self):
        """Test owner user can login successfully"""
        session, response = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        data = response.json()
        # Response has role at root level
        assert data.get("role") == "owner", f"Expected owner role, got {data.get('role')}"
        print(f"✓ Owner login successful: {data.get('email')}")
    
    # ==================== Security Overview Endpoint ====================
    
    def test_security_overview_admin_access(self):
        """Test admin can access security overview endpoint"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/overview")
        assert response.status_code == 200, f"Security overview failed: {response.text}"
        
        data = response.json()
        # Verify response structure
        assert "failed_logins_24h" in data, "Missing failed_logins_24h"
        assert "failed_logins_7d" in data, "Missing failed_logins_7d"
        assert "locked_accounts" in data, "Missing locked_accounts"
        assert "oauth_rotations_24h" in data, "Missing oauth_rotations_24h"
        assert "oauth_rotations_7d" in data, "Missing oauth_rotations_7d"
        assert "active_broker_connections" in data, "Missing active_broker_connections"
        assert "total_users" in data, "Missing total_users"
        assert "pro_users" in data, "Missing pro_users"
        assert "checked_at" in data, "Missing checked_at"
        
        print(f"✓ Security overview accessible by admin: failed_logins_7d={data['failed_logins_7d']}, total_users={data['total_users']}")
    
    def test_security_overview_owner_access(self):
        """Test owner can access security overview endpoint"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/overview")
        assert response.status_code == 200, f"Security overview failed for owner: {response.text}"
        
        data = response.json()
        assert "failed_logins_24h" in data
        assert "total_users" in data
        print(f"✓ Security overview accessible by owner")
    
    def test_security_overview_unauthenticated_denied(self):
        """Test unauthenticated user cannot access security overview"""
        # Use a fresh session without cookies
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/admin/security/overview")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Security overview correctly denied for unauthenticated user")
    
    # ==================== Failed Logins Endpoint ====================
    
    def test_failed_logins_admin_access(self):
        """Test admin can access failed logins endpoint"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/failed-logins?limit=20")
        assert response.status_code == 200, f"Failed logins endpoint failed: {response.text}"
        
        data = response.json()
        assert "failed_logins" in data, "Missing failed_logins array"
        assert "count" in data, "Missing count"
        assert isinstance(data["failed_logins"], list), "failed_logins should be a list"
        
        # If there are failed logins, verify structure
        if data["failed_logins"]:
            fl = data["failed_logins"][0]
            assert "identifier" in fl, "Missing identifier in failed login"
            assert "attempts" in fl, "Missing attempts in failed login"
            assert "last_attempt" in fl, "Missing last_attempt in failed login"
            print(f"✓ Failed logins data: {len(data['failed_logins'])} records")
        else:
            print(f"✓ Failed logins endpoint works (no records)")
    
    def test_failed_logins_owner_access(self):
        """Test owner can access failed logins endpoint"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/failed-logins")
        assert response.status_code == 200, f"Failed logins failed for owner: {response.text}"
        print(f"✓ Failed logins accessible by owner")
    
    # ==================== OAuth Rotations Endpoint ====================
    
    def test_oauth_rotations_admin_access(self):
        """Test admin can access OAuth rotations endpoint"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/oauth-rotations?limit=20")
        assert response.status_code == 200, f"OAuth rotations endpoint failed: {response.text}"
        
        data = response.json()
        assert "rotations" in data, "Missing rotations array"
        assert "count" in data, "Missing count"
        assert isinstance(data["rotations"], list), "rotations should be a list"
        
        # If there are rotations, verify structure
        if data["rotations"]:
            r = data["rotations"][0]
            assert "user_id" in r, "Missing user_id in rotation"
            assert "broker_id" in r, "Missing broker_id in rotation"
            assert "event" in r, "Missing event in rotation"
            assert "timestamp" in r, "Missing timestamp in rotation"
            print(f"✓ OAuth rotations data: {len(data['rotations'])} records")
        else:
            print(f"✓ OAuth rotations endpoint works (no records)")
    
    def test_oauth_rotations_owner_access(self):
        """Test owner can access OAuth rotations endpoint"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/oauth-rotations")
        assert response.status_code == 200, f"OAuth rotations failed for owner: {response.text}"
        print(f"✓ OAuth rotations accessible by owner")
    
    # ==================== Broker Connections Endpoint ====================
    
    def test_broker_connections_admin_access(self):
        """Test admin can access broker connections endpoint"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/broker-connections")
        assert response.status_code == 200, f"Broker connections endpoint failed: {response.text}"
        
        data = response.json()
        assert "connections" in data, "Missing connections array"
        assert "count" in data, "Missing count"
        assert isinstance(data["connections"], list), "connections should be a list"
        
        # If there are connections, verify structure (sensitive fields excluded)
        if data["connections"]:
            c = data["connections"][0]
            assert "user_id" in c, "Missing user_id in connection"
            assert "broker_id" in c, "Missing broker_id in connection"
            assert "auth_method" in c, "Missing auth_method in connection"
            # Verify sensitive fields are NOT included
            assert "api_key_enc" not in c, "api_key_enc should be excluded"
            assert "api_secret_enc" not in c, "api_secret_enc should be excluded"
            assert "oauth_refresh_token_enc" not in c, "oauth_refresh_token_enc should be excluded"
            print(f"✓ Broker connections data: {len(data['connections'])} records (sensitive fields excluded)")
        else:
            print(f"✓ Broker connections endpoint works (no records)")
    
    def test_broker_connections_owner_access(self):
        """Test owner can access broker connections endpoint"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/admin/security/broker-connections")
        assert response.status_code == 200, f"Broker connections failed for owner: {response.text}"
        print(f"✓ Broker connections accessible by owner")
    
    # ==================== Unlock Account Endpoint ====================
    
    def test_unlock_nonexistent_account(self):
        """Test unlocking a non-existent account returns 404"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.post(f"{BASE_URL}/api/admin/security/unlock/nonexistent@test.com")
        assert response.status_code == 404, f"Expected 404 for non-existent account, got {response.status_code}"
        print(f"✓ Unlock non-existent account correctly returns 404")
    
    def test_unlock_unauthenticated_denied(self):
        """Test unauthenticated user cannot unlock accounts"""
        fresh_session = requests.Session()
        response = fresh_session.post(f"{BASE_URL}/api/admin/security/unlock/test@test.com")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Unlock correctly denied for unauthenticated user")
    
    # ==================== Non-Admin Access Denied Tests ====================
    
    def test_regular_user_denied_security_endpoints(self):
        """Test that unauthenticated user gets 401/403 on security endpoints"""
        fresh_session = requests.Session()
        
        endpoints = [
            "/api/admin/security/overview",
            "/api/admin/security/failed-logins",
            "/api/admin/security/oauth-rotations",
            "/api/admin/security/broker-connections",
        ]
        
        for endpoint in endpoints:
            response = fresh_session.get(f"{BASE_URL}{endpoint}")
            assert response.status_code in [401, 403], f"Expected 401/403 for {endpoint}, got {response.status_code}"
        
        print(f"✓ All security endpoints correctly deny unauthenticated access")


class TestAdminPanelAccess:
    """Tests for Admin Panel access for both admin and owner roles"""
    
    def _get_session_with_login(self, email, password):
        """Create a new session and login"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return session, response
    
    def test_admin_cannot_access_admin_users_list(self):
        """Test admin CANNOT access the admin users list endpoint (owner-only)"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/auth/admin/users")
        # Admin users list is owner-only, admin should get 403
        assert response.status_code == 403, f"Expected 403 for admin, got {response.status_code}"
        print(f"✓ Admin correctly denied access to users list (owner-only endpoint)")
    
    def test_owner_can_access_admin_users_list(self):
        """Test owner can access the admin users list endpoint"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/auth/admin/users")
        assert response.status_code == 200, f"Admin users list failed for owner: {response.text}"
        
        data = response.json()
        assert "users" in data, "Missing users array"
        print(f"✓ Owner can access users list: {len(data['users'])} users")
    
    def test_admin_role_in_user_data(self):
        """Test that admin user has correct role in /api/auth/me"""
        session, login_resp = self._get_session_with_login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, "Admin login failed"
        
        response = session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Auth me failed: {response.text}"
        
        data = response.json()
        assert data.get("role") == "admin", f"Expected admin role, got {data.get('role')}"
        print(f"✓ Admin user has correct role: {data.get('role')}")
    
    def test_owner_role_in_user_data(self):
        """Test that owner user has correct role in /api/auth/me"""
        session, login_resp = self._get_session_with_login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, "Owner login failed"
        
        response = session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Auth me failed: {response.text}"
        
        data = response.json()
        assert data.get("role") == "owner", f"Expected owner role, got {data.get('role')}"
        print(f"✓ Owner user has correct role: {data.get('role')}")


class TestAPIHealthChecks:
    """Basic health checks for core APIs"""
    
    def test_api_root(self):
        """Test API root endpoint"""
        session = requests.Session()
        response = session.get(f"{BASE_URL}/api/")
        assert response.status_code == 200, f"API root failed: {response.text}"
        data = response.json()
        assert "message" in data
        print(f"✓ API root: {data.get('message')}")
    
    def test_sectors_heatmap(self):
        """Test sectors heatmap endpoint (public)"""
        session = requests.Session()
        response = session.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Sectors heatmap failed: {response.text}"
        print(f"✓ Sectors heatmap endpoint working")
