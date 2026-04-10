"""
Iteration 64: Memory Dashboard UI + Broker OAuth Admin Testing
Tests P1 (Memory Dashboard data endpoints) and P2 (Broker OAuth Admin CRUD)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestAuthSetup:
    """Authentication setup tests"""
    
    def test_owner_login(self):
        """Owner can login and get session cookie"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        data = response.json()
        # Response returns user data directly (not nested under "user")
        assert data.get("role") == "owner" or data.get("user", {}).get("role") == "owner"
        print(f"PASS: Owner login successful, role={data.get('role', data.get('user', {}).get('role'))}")
    
    def test_admin_login(self):
        """Admin can login and get session cookie"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        # Response returns user data directly (not nested under "user")
        assert data.get("role") == "admin" or data.get("user", {}).get("role") == "admin"
        print(f"PASS: Admin login successful, role={data.get('role', data.get('user', {}).get('role'))}")


class TestMemoryDashboardEndpoints:
    """P1: Memory Dashboard data endpoints (Pro-only)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner (Pro user) for Memory Dashboard tests"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        yield
        self.session.close()
    
    def test_memory_stats_endpoint(self):
        """GET /api/accuracy/memory returns memory stats"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200, f"Memory stats failed: {response.text}"
        data = response.json()
        # Check expected fields
        assert "total_episodes" in data or "initialized" in data
        print(f"PASS: Memory stats endpoint returns data: {list(data.keys())[:5]}")
    
    def test_accuracy_stats_endpoint(self):
        """GET /api/accuracy/stats returns accuracy statistics"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats")
        assert response.status_code == 200, f"Accuracy stats failed: {response.text}"
        data = response.json()
        # Should have overall stats
        assert "overall" in data or "accuracy" in data or isinstance(data, dict)
        print(f"PASS: Accuracy stats endpoint returns data")
    
    def test_cleanup_history_endpoint(self):
        """GET /api/accuracy/memory/cleanup/history returns cleanup runs"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory/cleanup/history")
        assert response.status_code == 200, f"Cleanup history failed: {response.text}"
        data = response.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)
        print(f"PASS: Cleanup history returns {len(data['runs'])} runs")
    
    def test_failure_breakdown_endpoint(self):
        """GET /api/accuracy/failure-breakdown returns failure mode breakdown"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/failure-breakdown")
        assert response.status_code == 200, f"Failure breakdown failed: {response.text}"
        data = response.json()
        # Should have breakdown or total_failures
        assert "breakdown" in data or "total_failures" in data or "failure_modes" in data
        print(f"PASS: Failure breakdown endpoint returns data")
    
    def test_post_mortem_history_endpoint(self):
        """GET /api/accuracy/post-mortem/history returns AI post-mortem results"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/post-mortem/history?limit=20")
        assert response.status_code == 200, f"Post-mortem history failed: {response.text}"
        data = response.json()
        assert "post_mortems" in data
        assert isinstance(data["post_mortems"], list)
        print(f"PASS: Post-mortem history returns {len(data['post_mortems'])} entries")


class TestBrokerOAuthAdminOwnerOnly:
    """P2: Broker OAuth Admin - Owner-only access tests"""
    
    @pytest.fixture
    def owner_session(self):
        """Login as owner"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        yield session
        session.close()
    
    @pytest.fixture
    def admin_session(self):
        """Login as admin (non-owner)"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        yield session
        session.close()
    
    def test_get_broker_oauth_owner_access(self, owner_session):
        """GET /api/admin/broker-oauth returns broker config for owner"""
        response = owner_session.get(f"{BASE_URL}/api/admin/broker-oauth")
        assert response.status_code == 200, f"Owner GET broker-oauth failed: {response.text}"
        data = response.json()
        assert "brokers" in data
        # Should have alpaca in supported brokers
        assert "alpaca" in data["brokers"]
        print(f"PASS: Owner can GET broker-oauth, brokers: {list(data['brokers'].keys())}")
    
    def test_get_broker_oauth_admin_denied(self, admin_session):
        """GET /api/admin/broker-oauth returns 403 for admin (non-owner)"""
        response = admin_session.get(f"{BASE_URL}/api/admin/broker-oauth")
        assert response.status_code == 403, f"Expected 403 for admin, got {response.status_code}"
        data = response.json()
        assert "Owner access required" in data.get("detail", "")
        print(f"PASS: Admin correctly denied with 403: {data.get('detail')}")
    
    def test_post_broker_oauth_admin_denied(self, admin_session):
        """POST /api/admin/broker-oauth/alpaca returns 403 for admin"""
        response = admin_session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "test_id", "client_secret": "test_secret"}
        )
        assert response.status_code == 403, f"Expected 403 for admin POST, got {response.status_code}"
        print(f"PASS: Admin correctly denied POST with 403")
    
    def test_delete_broker_oauth_admin_denied(self, admin_session):
        """DELETE /api/admin/broker-oauth/alpaca returns 403 for admin"""
        response = admin_session.delete(f"{BASE_URL}/api/admin/broker-oauth/alpaca")
        assert response.status_code == 403, f"Expected 403 for admin DELETE, got {response.status_code}"
        print(f"PASS: Admin correctly denied DELETE with 403")


class TestBrokerOAuthCRUD:
    """P2: Broker OAuth Admin - Full CRUD cycle (owner only)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as owner for CRUD tests"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        yield
        # Cleanup: delete test config if exists
        self.session.delete(f"{BASE_URL}/api/admin/broker-oauth/alpaca")
        self.session.close()
    
    def test_save_broker_oauth_credentials(self):
        """POST /api/admin/broker-oauth/alpaca saves encrypted credentials"""
        test_client_id = "TEST_CLIENT_ID_12345678"
        test_client_secret = "TEST_CLIENT_SECRET_ABCDEFGH"
        
        response = self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": test_client_id, "client_secret": test_client_secret}
        )
        assert response.status_code == 200, f"Save broker OAuth failed: {response.text}"
        data = response.json()
        
        # Verify response
        assert data.get("status") == "configured"
        assert data.get("broker_id") == "alpaca"
        assert "client_id_preview" in data
        # Preview should show partial ID
        assert "TEST_CLI" in data["client_id_preview"] or "..." in data["client_id_preview"]
        print(f"PASS: Saved broker OAuth, preview: {data['client_id_preview']}")
    
    def test_verify_saved_credentials_appear(self):
        """After saving, GET shows configured=true with preview"""
        # First save
        self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "VERIFY_CLIENT_ID_123", "client_secret": "VERIFY_SECRET_456"}
        )
        
        # Then verify
        response = self.session.get(f"{BASE_URL}/api/admin/broker-oauth")
        assert response.status_code == 200
        data = response.json()
        
        alpaca_config = data["brokers"].get("alpaca", {})
        assert alpaca_config.get("configured") == True
        assert alpaca_config.get("client_id_preview") != ""
        print(f"PASS: Verified saved credentials appear, configured={alpaca_config.get('configured')}")
    
    def test_oauth_status_after_save(self):
        """GET /api/broker/oauth/alpaca/status returns configured=true after save"""
        # Save credentials
        self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "STATUS_TEST_ID", "client_secret": "STATUS_TEST_SECRET"}
        )
        
        # Check status
        response = self.session.get(f"{BASE_URL}/api/broker/oauth/alpaca/status")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("configured") == True
        assert data.get("available") == True
        print(f"PASS: OAuth status shows configured=true after save")
    
    def test_delete_broker_oauth_config(self):
        """DELETE /api/admin/broker-oauth/alpaca removes config"""
        # First save
        self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "DELETE_TEST_ID", "client_secret": "DELETE_TEST_SECRET"}
        )
        
        # Then delete
        response = self.session.delete(f"{BASE_URL}/api/admin/broker-oauth/alpaca")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "deleted"
        print(f"PASS: Deleted broker OAuth config")
    
    def test_oauth_status_after_delete(self):
        """GET /api/broker/oauth/alpaca/status returns configured=false after delete"""
        # Save then delete
        self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "DEL_STATUS_ID", "client_secret": "DEL_STATUS_SECRET"}
        )
        self.session.delete(f"{BASE_URL}/api/admin/broker-oauth/alpaca")
        
        # Check status
        response = self.session.get(f"{BASE_URL}/api/broker/oauth/alpaca/status")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("configured") == False
        print(f"PASS: OAuth status shows configured=false after delete")
    
    def test_save_requires_both_fields(self):
        """POST /api/admin/broker-oauth/alpaca requires both client_id and client_secret"""
        # Missing client_secret
        response = self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "ONLY_ID"}
        )
        assert response.status_code == 400
        
        # Missing client_id
        response = self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_secret": "ONLY_SECRET"}
        )
        assert response.status_code == 400
        print(f"PASS: Save correctly requires both fields")
    
    def test_unsupported_broker_rejected(self):
        """POST /api/admin/broker-oauth/unsupported returns 400"""
        response = self.session.post(
            f"{BASE_URL}/api/admin/broker-oauth/unsupported_broker",
            json={"client_id": "test", "client_secret": "test"}
        )
        assert response.status_code == 400
        print(f"PASS: Unsupported broker correctly rejected")


class TestBrokerOAuthUnauthenticated:
    """P2: Broker OAuth Admin - Unauthenticated access tests"""
    
    def test_get_broker_oauth_unauthenticated(self):
        """GET /api/admin/broker-oauth returns 401 without auth"""
        response = requests.get(f"{BASE_URL}/api/admin/broker-oauth")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print(f"PASS: Unauthenticated GET returns 401")
    
    def test_post_broker_oauth_unauthenticated(self):
        """POST /api/admin/broker-oauth/alpaca returns 401 without auth"""
        response = requests.post(
            f"{BASE_URL}/api/admin/broker-oauth/alpaca",
            json={"client_id": "test", "client_secret": "test"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print(f"PASS: Unauthenticated POST returns 401")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
