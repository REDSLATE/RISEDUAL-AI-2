"""
Iteration 84: Broker Execution Privileges Tests
Tests that only owner (managingdirector@redslateholdings.com) can execute live trades.
All other users (admin, regular) have read-only access.
"""
import pytest
import requests
import os
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD

# Test credentials from /app/memory/test_credentials.md
class TestBrokerExecutionPrivileges:
    """Test broker execution privileges - owner vs non-owner access"""
    
    @pytest.fixture(scope="class")
    def owner_session(self):
        """Login as owner and return session with cookies"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code} - {response.text}")
        return session
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code} - {response.text}")
        return session
    
    # ============================================================
    # EXECUTION STATUS ENDPOINT TESTS
    # ============================================================
    
    def test_execution_status_owner_allowed(self, owner_session):
        """Owner should have execution_allowed=true"""
        response = owner_session.get(f"{BASE_URL}/api/broker/execution-status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data.get("execution_allowed") is True, f"Owner should have execution_allowed=true, got {data}"
        assert data.get("mode") == "live", f"Owner should have mode='live', got {data}"
        print(f"✓ Owner execution status: {data}")
    
    def test_execution_status_admin_not_allowed(self, admin_session):
        """Admin should have execution_allowed=false"""
        response = admin_session.get(f"{BASE_URL}/api/broker/execution-status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data.get("execution_allowed") is False, f"Admin should have execution_allowed=false, got {data}"
        assert data.get("mode") == "read_only", f"Admin should have mode='read_only', got {data}"
        print(f"✓ Admin execution status: {data}")
    
    def test_execution_status_unauthenticated(self):
        """Unauthenticated request should fail"""
        session = requests.Session()
        response = session.get(f"{BASE_URL}/api/broker/execution-status")
        assert response.status_code in [401, 403], f"Expected 401/403 for unauthenticated, got {response.status_code}"
        print(f"✓ Unauthenticated execution-status returns {response.status_code}")
    
    # ============================================================
    # ORDER PLACEMENT TESTS (POST /api/broker/order/{broker_id})
    # ============================================================
    
    def test_place_order_admin_forbidden(self, admin_session):
        """Admin should get 403 when trying to place an order"""
        response = admin_session.post(f"{BASE_URL}/api/broker/order/alpaca", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "buy",
            "order_type": "market",
            "time_in_force": "day"
        })
        assert response.status_code == 403, f"Expected 403 for admin order placement, got {response.status_code}: {response.text}"
        data = response.json()
        assert "restricted" in data.get("detail", "").lower() or "read-only" in data.get("detail", "").lower(), \
            f"Expected restriction message, got: {data}"
        print(f"✓ Admin order placement correctly returns 403: {data.get('detail')}")
    
    def test_place_order_owner_no_broker_connection(self, owner_session):
        """Owner can attempt order but will fail if no broker connected (404 not 403)"""
        response = owner_session.post(f"{BASE_URL}/api/broker/order/alpaca", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "buy",
            "order_type": "market",
            "time_in_force": "day"
        })
        # Owner should NOT get 403 - they should get 404 (no broker) or 400 (broker error)
        assert response.status_code != 403, f"Owner should not get 403, got {response.status_code}: {response.text}"
        # Expected: 404 (no broker connection) or 400 (broker API error)
        assert response.status_code in [400, 404], f"Expected 400/404 for owner without broker, got {response.status_code}"
        print(f"✓ Owner order attempt returns {response.status_code} (not 403 - execution allowed)")
    
    # ============================================================
    # ORDER CANCELLATION TESTS (DELETE /api/broker/order/{broker_id}/{order_id})
    # ============================================================
    
    def test_cancel_order_admin_forbidden(self, admin_session):
        """Admin should get 403 when trying to cancel an order"""
        response = admin_session.delete(f"{BASE_URL}/api/broker/order/alpaca/fake-order-123")
        assert response.status_code == 403, f"Expected 403 for admin order cancellation, got {response.status_code}: {response.text}"
        data = response.json()
        assert "restricted" in data.get("detail", "").lower() or "read-only" in data.get("detail", "").lower() or "authorized" in data.get("detail", "").lower(), \
            f"Expected restriction message, got: {data}"
        print(f"✓ Admin order cancellation correctly returns 403: {data.get('detail')}")
    
    def test_cancel_order_owner_no_broker_connection(self, owner_session):
        """Owner can attempt cancel but will fail if no broker connected (404 not 403)"""
        response = owner_session.delete(f"{BASE_URL}/api/broker/order/alpaca/fake-order-123")
        # Owner should NOT get 403 - they should get 404 (no broker) or 400 (broker error)
        assert response.status_code != 403, f"Owner should not get 403, got {response.status_code}: {response.text}"
        # Expected: 404 (no broker connection) or 400 (broker API error)
        assert response.status_code in [400, 404], f"Expected 400/404 for owner without broker, got {response.status_code}"
        print(f"✓ Owner cancel attempt returns {response.status_code} (not 403 - execution allowed)")
    
    # ============================================================
    # READ-ONLY ENDPOINTS (should work for all authenticated users)
    # ============================================================
    
    def test_broker_connect_accessible_to_admin(self, admin_session):
        """Admin should be able to attempt broker connection (will fail without valid keys)"""
        response = admin_session.post(f"{BASE_URL}/api/broker/connect", json={
            "broker_id": "alpaca",
            "api_key": "test-invalid-key",
            "api_secret": "test-invalid-secret",
            "paper": True
        })
        # Should NOT be 403 - admin can connect brokers (read-only sync)
        assert response.status_code != 403, f"Admin should be able to connect broker, got 403"
        # Expected: 400 (invalid credentials) - not 403 (forbidden)
        assert response.status_code == 400, f"Expected 400 for invalid credentials, got {response.status_code}"
        print(f"✓ Admin broker connect returns {response.status_code} (not 403 - connection allowed)")
    
    def test_broker_connect_accessible_to_owner(self, owner_session):
        """Owner should be able to attempt broker connection"""
        response = owner_session.post(f"{BASE_URL}/api/broker/connect", json={
            "broker_id": "alpaca",
            "api_key": "test-invalid-key",
            "api_secret": "test-invalid-secret",
            "paper": True
        })
        # Should NOT be 403
        assert response.status_code != 403, f"Owner should be able to connect broker, got 403"
        # Expected: 400 (invalid credentials)
        assert response.status_code == 400, f"Expected 400 for invalid credentials, got {response.status_code}"
        print(f"✓ Owner broker connect returns {response.status_code} (connection allowed)")
    
    def test_broker_connections_list_admin(self, admin_session):
        """Admin should be able to list their broker connections"""
        response = admin_session.get(f"{BASE_URL}/api/broker/connections")
        assert response.status_code == 200, f"Expected 200 for connections list, got {response.status_code}"
        data = response.json()
        assert "connections" in data, f"Expected 'connections' key in response, got {data}"
        print(f"✓ Admin can list broker connections: {len(data.get('connections', []))} connections")
    
    def test_broker_connections_list_owner(self, owner_session):
        """Owner should be able to list their broker connections"""
        response = owner_session.get(f"{BASE_URL}/api/broker/connections")
        assert response.status_code == 200, f"Expected 200 for connections list, got {response.status_code}"
        data = response.json()
        assert "connections" in data, f"Expected 'connections' key in response, got {data}"
        print(f"✓ Owner can list broker connections: {len(data.get('connections', []))} connections")
    
    def test_broker_account_admin_no_connection(self, admin_session):
        """Admin can access account endpoint (will fail with 404 if no connection)"""
        response = admin_session.get(f"{BASE_URL}/api/broker/account/alpaca")
        # Should NOT be 403 - read-only endpoint
        assert response.status_code != 403, f"Admin should access account endpoint, got 403"
        # Expected: 404 (no connection)
        assert response.status_code == 404, f"Expected 404 for no connection, got {response.status_code}"
        print(f"✓ Admin account endpoint returns {response.status_code} (read-only access allowed)")
    
    def test_broker_positions_admin_no_connection(self, admin_session):
        """Admin can access positions endpoint (will fail with 404 if no connection)"""
        response = admin_session.get(f"{BASE_URL}/api/broker/positions/alpaca")
        # Should NOT be 403 - read-only endpoint
        assert response.status_code != 403, f"Admin should access positions endpoint, got 403"
        # Expected: 404 (no connection)
        assert response.status_code == 404, f"Expected 404 for no connection, got {response.status_code}"
        print(f"✓ Admin positions endpoint returns {response.status_code} (read-only access allowed)")
    
    def test_broker_orders_admin_no_connection(self, admin_session):
        """Admin can access orders endpoint (will fail with 404 if no connection)"""
        response = admin_session.get(f"{BASE_URL}/api/broker/orders/alpaca")
        # Should NOT be 403 - read-only endpoint
        assert response.status_code != 403, f"Admin should access orders endpoint, got 403"
        # Expected: 404 (no connection)
        assert response.status_code == 404, f"Expected 404 for no connection, got {response.status_code}"
        print(f"✓ Admin orders endpoint returns {response.status_code} (read-only access allowed)")
    
    def test_broker_portfolio_sync_admin_no_connection(self, admin_session):
        """Admin can access portfolio-sync endpoint (will fail with 404 if no connection)"""
        response = admin_session.get(f"{BASE_URL}/api/broker/portfolio-sync/alpaca")
        # Should NOT be 403 - read-only endpoint
        assert response.status_code != 403, f"Admin should access portfolio-sync endpoint, got 403"
        # Expected: 404 (no connection)
        assert response.status_code == 404, f"Expected 404 for no connection, got {response.status_code}"
        print(f"✓ Admin portfolio-sync endpoint returns {response.status_code} (read-only access allowed)")


class TestBrokerExecutionPrivilegesSummary:
    """Summary test to verify the execution privilege logic"""
    
    def test_execution_privilege_summary(self):
        """Summary: Verify execution privilege logic is correctly implemented"""
        # Login as owner
        owner_session = requests.Session()
        owner_session.headers.update({"Content-Type": "application/json"})
        owner_login = owner_session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        
        # Login as admin
        admin_session = requests.Session()
        admin_session.headers.update({"Content-Type": "application/json"})
        admin_login = admin_session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        
        if owner_login.status_code != 200 or admin_login.status_code != 200:
            pytest.skip("Login failed for one or both users")
        
        # Check execution status for both
        owner_exec = owner_session.get(f"{BASE_URL}/api/broker/execution-status").json()
        admin_exec = admin_session.get(f"{BASE_URL}/api/broker/execution-status").json()
        
        print("\n" + "="*60)
        print("BROKER EXECUTION PRIVILEGES SUMMARY")
        print("="*60)
        print(f"Owner ({OWNER_EMAIL}):")
        print(f"  - execution_allowed: {owner_exec.get('execution_allowed')}")
        print(f"  - mode: {owner_exec.get('mode')}")
        print(f"\nAdmin ({ADMIN_EMAIL}):")
        print(f"  - execution_allowed: {admin_exec.get('execution_allowed')}")
        print(f"  - mode: {admin_exec.get('mode')}")
        print("="*60)
        
        # Verify
        assert owner_exec.get("execution_allowed") is True, "Owner should have execution privileges"
        assert admin_exec.get("execution_allowed") is False, "Admin should NOT have execution privileges"
        
        # Test order placement
        admin_order = admin_session.post(f"{BASE_URL}/api/broker/order/alpaca", json={
            "symbol": "AAPL", "quantity": 1, "side": "buy", "order_type": "market", "time_in_force": "day"
        })
        assert admin_order.status_code == 403, f"Admin order should be 403, got {admin_order.status_code}"
        
        print("\n✓ All execution privilege checks passed!")
        print("  - Owner: LIVE TRADING enabled")
        print("  - Admin: READ ONLY mode (403 on order placement)")
