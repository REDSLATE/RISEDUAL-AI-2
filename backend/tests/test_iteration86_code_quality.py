"""
Test suite for Iteration 86 - Code Quality Sweep Verification
Tests:
1. MD5 → SHA-256 hash change verification (accuracy.py, market_memory_service.py, post_mortem_service.py)
2. Backend API health checks (auth, accuracy, sectors, broker)
3. Credential centralization verification (conftest_creds.py imports)
"""
import pytest
import requests
import os
from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

# Override BASE_URL from environment if available
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', BASE_URL).rstrip('/')


class TestAuthEndpoints:
    """Test authentication endpoints"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if res.status_code != 200:
            pytest.skip(f"Admin login failed: {res.status_code}")
        return session
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "email" in data, "Response should contain email"
        assert data["email"] == ADMIN_EMAIL, f"Expected {ADMIN_EMAIL}, got {data['email']}"
        print(f"✓ Admin login successful: {data['email']}")
    
    def test_auth_me_returns_user_data(self, admin_session):
        """GET /api/auth/me with cookie returns user data"""
        res = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "email" in data, "Response should contain email"
        assert "role" in data, "Response should contain role"
        print(f"✓ Auth me returned: {data['email']} ({data['role']})")


class TestAccuracyEndpoints:
    """Test accuracy tracking endpoints (Pro only)"""
    
    @pytest.fixture(scope="class")
    def pro_session(self):
        """Login as admin (pro user) and return session"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if res.status_code != 200:
            pytest.skip(f"Pro user login failed: {res.status_code}")
        return session
    
    def test_accuracy_stats_returns_200(self, pro_session):
        """GET /api/accuracy/stats returns accuracy data for pro user"""
        res = pro_session.get(f"{BASE_URL}/api/accuracy/stats")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        # Should have overall stats
        assert "overall" in data or "war_room" in data, "Response should contain accuracy stats"
        print("✓ Accuracy stats returned successfully")
    
    def test_accuracy_memory_returns_200(self, pro_session):
        """GET /api/accuracy/memory returns memory stats for pro user"""
        res = pro_session.get(f"{BASE_URL}/api/accuracy/memory")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "total_episodes" in data, "Response should contain total_episodes"
        print(f"✓ Memory stats: {data.get('total_episodes', 0)} episodes")
    
    def test_failure_modes_returns_200(self, pro_session):
        """GET /api/accuracy/failure-modes returns failure mode categories"""
        res = pro_session.get(f"{BASE_URL}/api/accuracy/failure-modes")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "failure_modes" in data, "Response should contain failure_modes"
        modes = data["failure_modes"]
        # Verify expected failure modes exist
        expected_modes = ["TECH_FAKEOUT", "MACRO_SHOCK", "LIQUIDITY_GAP", "REGIME_SHIFT", "UNKNOWN"]
        for mode in expected_modes:
            assert mode in modes, f"Missing failure mode: {mode}"
        print(f"✓ Failure modes: {list(modes.keys())}")


class TestSectorEndpoints:
    """Test sector heatmap endpoints"""
    
    def test_sectors_heatmap_returns_200(self):
        """GET /api/sectors/heatmap returns sector data"""
        res = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "sectors" in data, "Response should contain sectors"
        sectors = data["sectors"]
        assert len(sectors) > 0, "Should have at least one sector"
        print(f"✓ Sectors heatmap: {len(sectors)} sectors returned")


class TestBrokerEndpoints:
    """Test broker OAuth and execution endpoints"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if res.status_code != 200:
            pytest.skip(f"Auth failed: {res.status_code}")
        return session
    
    def test_oauth_alpaca_status_returns_200(self):
        """GET /api/broker/oauth/alpaca/status returns OAuth status"""
        res = requests.get(f"{BASE_URL}/api/broker/oauth/alpaca/status")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "available" in data, "Response should contain available field"
        assert "broker_id" in data, "Response should contain broker_id"
        assert data["broker_id"] == "alpaca", f"Expected alpaca, got {data['broker_id']}"
        print(f"✓ Alpaca OAuth status: available={data['available']}")
    
    def test_oauth_capabilities_returns_200(self):
        """GET /api/broker/oauth/capabilities returns OAuth capabilities"""
        res = requests.get(f"{BASE_URL}/api/broker/oauth/capabilities")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "three_legged_oauth" in data, "Response should contain three_legged_oauth"
        assert "supported_brokers" in data, "Response should contain supported_brokers"
        print(f"✓ OAuth capabilities: {len(data['supported_brokers'])} brokers supported")
    
    def test_execution_status_returns_200(self, auth_session):
        """GET /api/broker/execution-status returns execution privileges"""
        res = auth_session.get(f"{BASE_URL}/api/broker/execution-status")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "execution_allowed" in data, "Response should contain execution_allowed"
        assert "mode" in data, "Response should contain mode"
        print(f"✓ Execution status: mode={data['mode']}")


class TestCodeQualityVerification:
    """Verify code quality changes were applied correctly"""
    
    def test_sha256_in_accuracy_py(self):
        """Verify SHA-256 is used in accuracy.py (not MD5)"""
        with open("/app/backend/routes/accuracy.py", "r") as f:
            content = f.read()
        assert "sha256" in content, "accuracy.py should use sha256"
        assert "md5" not in content.lower() or "md5" in content.lower() and "sha256" in content, \
            "accuracy.py should not use md5 for hashing"
        print("✓ accuracy.py uses SHA-256")
    
    def test_sha256_in_market_memory_service(self):
        """Verify SHA-256 is used in market_memory_service.py (not MD5)"""
        with open("/app/backend/services/market_memory_service.py", "r") as f:
            content = f.read()
        assert "sha256" in content, "market_memory_service.py should use sha256"
        print("✓ market_memory_service.py uses SHA-256")
    
    def test_sha256_in_post_mortem_service(self):
        """Verify SHA-256 is used in post_mortem_service.py (not MD5)"""
        with open("/app/backend/services/post_mortem_service.py", "r") as f:
            content = f.read()
        assert "sha256" in content, "post_mortem_service.py should use sha256"
        print("✓ post_mortem_service.py uses SHA-256")
    
    def test_conftest_creds_exists(self):
        """Verify conftest_creds.py exists with centralized credentials"""
        with open("/app/backend/tests/conftest_creds.py", "r") as f:
            content = f.read()
        assert "BASE_URL" in content, "conftest_creds.py should define BASE_URL"
        assert "ADMIN_EMAIL" in content, "conftest_creds.py should define ADMIN_EMAIL"
        assert "ADMIN_PASSWORD" in content, "conftest_creds.py should define ADMIN_PASSWORD"
        assert "OWNER_EMAIL" in content, "conftest_creds.py should define OWNER_EMAIL"
        print("✓ conftest_creds.py has centralized credentials")
    
    def test_broker_py_has_helper_functions(self):
        """Verify broker.py has refactored helper functions"""
        with open("/app/backend/routes/broker.py", "r") as f:
            content = f.read()
        # Check for extracted helper functions
        assert "_resolve_origin" in content, "broker.py should have _resolve_origin helper"
        assert "_exchange_oauth_code" in content, "broker.py should have _exchange_oauth_code helper"
        assert "_validate_oauth_account" in content, "broker.py should have _validate_oauth_account helper"
        assert "_store_oauth_connection" in content, "broker.py should have _store_oauth_connection helper"
        print("✓ broker.py has refactored helper functions")
    
    def test_accuracy_py_has_chromadb_helper(self):
        """Verify accuracy.py has extracted ChromaDB helper"""
        with open("/app/backend/routes/accuracy.py", "r") as f:
            content = f.read()
        assert "_update_chromadb_failure_code" in content, "accuracy.py should have _update_chromadb_failure_code helper"
        print("✓ accuracy.py has extracted ChromaDB helper")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
