"""
Iteration 116: Success Fee System Backend Tests
Tests the new success fee feature: 1.5% fee on broker-connected users' monthly gains above $1,000.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
REGULAR_USER_EMAIL = "testuser@example.com"
REGULAR_USER_PASSWORD = "TestUser2026!"


class TestSuccessFeeEndpoints:
    """Test Success Fee API endpoints"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code} - {response.text}")
        return session
    
    @pytest.fixture(scope="class")
    def owner_session(self):
        """Get authenticated owner session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code} - {response.text}")
        return session
    
    @pytest.fixture(scope="class")
    def unauthenticated_session(self):
        """Get unauthenticated session"""
        return requests.Session()
    
    # ============ GET /api/success-fee/current ============
    def test_current_fee_requires_auth(self, unauthenticated_session):
        """GET /api/success-fee/current should require authentication"""
        response = unauthenticated_session.get(f"{BASE_URL}/api/success-fee/current")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_current_fee_returns_fee_summary(self, admin_session):
        """GET /api/success-fee/current should return fee summary for authenticated user"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/current")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Admin likely has no broker connections, so eligible should be False
        assert "eligible" in data, "Response should contain 'eligible' field"
        assert "fee_rate" in data, "Response should contain 'fee_rate' field"
        assert "threshold" in data, "Response should contain 'threshold' field"
        
        # Verify fee rate and threshold values
        assert data["fee_rate"] == 0.015, f"Fee rate should be 0.015 (1.5%), got {data['fee_rate']}"
        assert data["threshold"] == 1000.0, f"Threshold should be 1000, got {data['threshold']}"
        
        # If not eligible, should have reason
        if not data.get("eligible"):
            assert "reason" in data, "Non-eligible response should contain 'reason'"
            print(f"User not eligible: {data.get('reason')}")
    
    # ============ GET /api/success-fee/history ============
    def test_fee_history_requires_auth(self, unauthenticated_session):
        """GET /api/success-fee/history should require authentication"""
        response = unauthenticated_session.get(f"{BASE_URL}/api/success-fee/history")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_fee_history_returns_records(self, admin_session):
        """GET /api/success-fee/history should return fee records list"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/history")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "records" in data, "Response should contain 'records' field"
        assert isinstance(data["records"], list), "Records should be a list"
        print(f"Fee history contains {len(data['records'])} records")
    
    def test_fee_history_with_limit(self, admin_session):
        """GET /api/success-fee/history should respect limit parameter"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/history?limit=5")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert len(data.get("records", [])) <= 5, "Should respect limit parameter"
    
    # ============ GET /api/success-fee/admin/stats ============
    def test_admin_stats_requires_auth(self, unauthenticated_session):
        """GET /api/success-fee/admin/stats should require authentication"""
        response = unauthenticated_session.get(f"{BASE_URL}/api/success-fee/admin/stats")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_admin_stats_returns_aggregate_data(self, admin_session):
        """GET /api/success-fee/admin/stats should return aggregate statistics"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/admin/stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Verify expected fields in stats
        expected_fields = ["total_records", "unpaid", "paid", "waived", "current_month_records", 
                          "total_collected", "total_outstanding", "fee_rate", "threshold"]
        for field in expected_fields:
            assert field in data, f"Stats should contain '{field}' field"
        
        # Verify fee rate and threshold
        assert data["fee_rate"] == 0.015, f"Fee rate should be 0.015, got {data['fee_rate']}"
        assert data["threshold"] == 1000.0, f"Threshold should be 1000, got {data['threshold']}"
        
        print(f"Admin stats: total_records={data['total_records']}, unpaid={data['unpaid']}, "
              f"total_outstanding=${data['total_outstanding']}, total_collected=${data['total_collected']}")
    
    def test_admin_stats_owner_access(self, owner_session):
        """GET /api/success-fee/admin/stats should be accessible by owner"""
        response = owner_session.get(f"{BASE_URL}/api/success-fee/admin/stats")
        assert response.status_code == 200, f"Owner should have access, got {response.status_code}"
    
    # ============ GET /api/success-fee/admin/list ============
    def test_admin_list_requires_auth(self, unauthenticated_session):
        """GET /api/success-fee/admin/list should require authentication"""
        response = unauthenticated_session.get(f"{BASE_URL}/api/success-fee/admin/list")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_admin_list_returns_records(self, admin_session):
        """GET /api/success-fee/admin/list should return fee records"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/admin/list")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "records" in data, "Response should contain 'records' field"
        assert isinstance(data["records"], list), "Records should be a list"
        print(f"Admin list contains {len(data['records'])} fee records")
    
    def test_admin_list_with_status_filter(self, admin_session):
        """GET /api/success-fee/admin/list should support status filter"""
        for status in ["all", "unpaid", "paid", "waived", "pending"]:
            response = admin_session.get(f"{BASE_URL}/api/success-fee/admin/list?status={status}")
            assert response.status_code == 200, f"Filter '{status}' failed: {response.status_code}"
            data = response.json()
            assert "records" in data, f"Filter '{status}' should return records"
    
    # ============ POST /api/success-fee/admin/mark-paid ============
    def test_mark_paid_requires_auth(self, unauthenticated_session):
        """POST /api/success-fee/admin/mark-paid should require authentication"""
        response = unauthenticated_session.post(
            f"{BASE_URL}/api/success-fee/admin/mark-paid",
            json={"user_id": "test", "period": "2026-01"}
        )
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_mark_paid_requires_user_id_and_period(self, admin_session):
        """POST /api/success-fee/admin/mark-paid should require user_id and period"""
        # Missing user_id
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/mark-paid",
            json={"period": "2026-01"}
        )
        assert response.status_code == 400, f"Expected 400 for missing user_id, got {response.status_code}"
        
        # Missing period
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/mark-paid",
            json={"user_id": "test"}
        )
        assert response.status_code == 400, f"Expected 400 for missing period, got {response.status_code}"
    
    def test_mark_paid_nonexistent_record(self, admin_session):
        """POST /api/success-fee/admin/mark-paid should return 404 for nonexistent record"""
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/mark-paid",
            json={"user_id": "nonexistent_user_id_12345", "period": "1999-01", "note": "Test"}
        )
        assert response.status_code == 404, f"Expected 404 for nonexistent record, got {response.status_code}"
    
    # ============ POST /api/success-fee/admin/waive ============
    def test_waive_requires_auth(self, unauthenticated_session):
        """POST /api/success-fee/admin/waive should require authentication"""
        response = unauthenticated_session.post(
            f"{BASE_URL}/api/success-fee/admin/waive",
            json={"user_id": "test", "period": "2026-01"}
        )
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_waive_requires_user_id_and_period(self, admin_session):
        """POST /api/success-fee/admin/waive should require user_id and period"""
        # Missing user_id
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/waive",
            json={"period": "2026-01"}
        )
        assert response.status_code == 400, f"Expected 400 for missing user_id, got {response.status_code}"
        
        # Missing period
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/waive",
            json={"user_id": "test"}
        )
        assert response.status_code == 400, f"Expected 400 for missing period, got {response.status_code}"
    
    def test_waive_nonexistent_record(self, admin_session):
        """POST /api/success-fee/admin/waive should return 404 for nonexistent record"""
        response = admin_session.post(
            f"{BASE_URL}/api/success-fee/admin/waive",
            json={"user_id": "nonexistent_user_id_12345", "period": "1999-01", "note": "Test waive"}
        )
        assert response.status_code == 404, f"Expected 404 for nonexistent record, got {response.status_code}"


class TestSuccessFeeServiceLogic:
    """Test the fee calculation logic via API responses"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code}")
        return session
    
    def test_fee_rate_is_1_5_percent(self, admin_session):
        """Verify fee rate is 1.5% (0.015)"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/current")
        assert response.status_code == 200
        data = response.json()
        assert data["fee_rate"] == 0.015, "Fee rate should be 1.5%"
    
    def test_threshold_is_1000(self, admin_session):
        """Verify profit threshold is $1,000"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/current")
        assert response.status_code == 200
        data = response.json()
        assert data["threshold"] == 1000.0, "Threshold should be $1,000"
    
    def test_non_broker_user_not_eligible(self, admin_session):
        """Users without broker connections should not be eligible"""
        response = admin_session.get(f"{BASE_URL}/api/success-fee/current")
        assert response.status_code == 200
        data = response.json()
        
        # Admin likely has no broker connections
        if not data.get("eligible"):
            assert "reason" in data, "Should provide reason for ineligibility"
            assert "broker" in data["reason"].lower(), "Reason should mention broker connections"
            print(f"Correctly identified non-eligible user: {data['reason']}")


class TestHealthAndAuth:
    """Basic health and auth tests to ensure system is working"""
    
    def test_api_health(self):
        """Verify API is responding"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
    
    def test_admin_login(self):
        """Verify admin can login"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.status_code} - {response.text}"
        
        # Verify cookies are set
        assert "access_token" in session.cookies or response.cookies, "Should set access_token cookie"
    
    def test_owner_login(self):
        """Verify owner can login"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Owner login failed: {response.status_code} - {response.text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
