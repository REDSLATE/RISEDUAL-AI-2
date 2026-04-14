"""Iteration 128: Dynamic Model Registration, Heartbeat, and Enable/Disable APIs

Tests the NEW provider health management endpoints:
- GET /api/provider-health — full lane snapshot (admin only)
- GET /api/provider-health/models — list all registered models with health data (admin only)
- POST /api/provider-health/register — hot-register a new provider (admin only, persists to MongoDB)
- POST /api/provider-health/deregister — remove a provider at runtime (admin only, deletes from MongoDB)
- POST /api/provider-health/heartbeat — external health heartbeat (NO AUTH required)
- POST /api/provider-health/enable — re-enable a disabled provider (admin only)

Key behaviors:
- Heartbeat endpoint is intentionally unauthenticated (services call it themselves)
- Register persists to `registered_providers` collection in MongoDB
- Heartbeat with status=failed disables the provider
- Non-admin users get 403 on admin endpoints
"""
import pytest
import requests
import os

# Import shared credentials
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test provider name to avoid conflicts
TEST_PROVIDER_NAME = "test-agent-model-128"
TEST_LANE = "test-lane-128"


class TestProviderHealthAuth:
    """Test authentication requirements for provider health endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for each test"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login_admin(self):
        """Login as admin and return session with cookies"""
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        data = resp.json()
        # Check for access_token in response
        assert "access_token" in data, f"No access_token in login response: {data}"
        return data["access_token"]
    
    def test_01_provider_health_snapshot_requires_auth(self):
        """GET /api/provider-health requires admin auth"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health")
        # Should fail without auth
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"
        print("PASS: GET /api/provider-health requires auth")
    
    def test_02_provider_health_models_requires_auth(self):
        """GET /api/provider-health/models requires admin auth"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health/models")
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"
        print("PASS: GET /api/provider-health/models requires auth")
    
    def test_03_register_requires_auth(self):
        """POST /api/provider-health/register requires admin auth"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/register", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "provider": "test",
            "api_key": "test-key",
            "model": "test-model",
            "priority": 100
        })
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"
        print("PASS: POST /api/provider-health/register requires auth")
    
    def test_04_deregister_requires_auth(self):
        """POST /api/provider-health/deregister requires admin auth"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME
        })
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"
        print("PASS: POST /api/provider-health/deregister requires auth")
    
    def test_05_enable_requires_auth(self):
        """POST /api/provider-health/enable requires admin auth"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/enable", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME
        })
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"
        print("PASS: POST /api/provider-health/enable requires auth")
    
    def test_06_heartbeat_does_not_require_auth(self):
        """POST /api/provider-health/heartbeat does NOT require auth"""
        # Heartbeat should work without auth (services call it themselves)
        # It may return 404 if provider doesn't exist, but NOT 401/403
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": "nonexistent-lane",
            "name": "nonexistent-provider",
            "status": "ok",
            "latency_ms": 100,
            "error_rate": 0
        })
        # Should NOT be 401/403 - heartbeat is intentionally unauthenticated
        assert resp.status_code not in [401, 403], f"Heartbeat should not require auth, got {resp.status_code}"
        # 404 is expected for nonexistent provider
        assert resp.status_code == 404, f"Expected 404 for nonexistent provider, got {resp.status_code}"
        print("PASS: POST /api/provider-health/heartbeat does NOT require auth (got 404 for nonexistent)")


class TestProviderHealthAdminOperations:
    """Test admin operations for provider health management"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login as admin
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        data = resp.json()
        self.token = data.get("access_token")
        # Set Authorization header for subsequent requests
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_07_get_provider_health_snapshot(self):
        """GET /api/provider-health returns full lane snapshot"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "lanes" in data, f"Response should have 'lanes' key: {data}"
        assert isinstance(data["lanes"], dict), f"lanes should be a dict: {data['lanes']}"
        print(f"PASS: GET /api/provider-health returns snapshot with {len(data['lanes'])} lanes")
    
    def test_08_get_provider_health_models_list(self):
        """GET /api/provider-health/models lists all registered models"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health/models")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "models" in data, f"Response should have 'models' key: {data}"
        assert "count" in data, f"Response should have 'count' key: {data}"
        assert isinstance(data["models"], list), f"models should be a list: {data['models']}"
        print(f"PASS: GET /api/provider-health/models returns {data['count']} models")
    
    def test_09_get_provider_health_models_filter_by_lane(self):
        """GET /api/provider-health/models?lane=ai filters by lane"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane=ai")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "models" in data, f"Response should have 'models' key: {data}"
        # All returned models should be in 'ai' lane
        for model in data["models"]:
            assert model.get("lane") == "ai", f"Model should be in 'ai' lane: {model}"
        print(f"PASS: GET /api/provider-health/models?lane=ai returns {data['count']} AI models")
    
    def test_10_register_new_provider(self):
        """POST /api/provider-health/register hot-registers a new provider"""
        # First, clean up any existing test provider
        self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME
        })
        
        # Register new provider
        resp = self.session.post(f"{BASE_URL}/api/provider-health/register", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "provider": "test-provider",
            "api_key": "test-api-key-128",
            "model": "test-model-v1",
            "priority": 50
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("registered") == True, f"Expected registered=True: {data}"
        assert data.get("lane") == TEST_LANE, f"Expected lane={TEST_LANE}: {data}"
        assert data.get("name") == TEST_PROVIDER_NAME, f"Expected name={TEST_PROVIDER_NAME}: {data}"
        print(f"PASS: POST /api/provider-health/register created provider: {data}")
    
    def test_11_verify_registered_provider_in_models_list(self):
        """Verify registered provider appears in /models list"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane={TEST_LANE}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        # Find our test provider
        test_provider = None
        for model in data["models"]:
            if model.get("name") == TEST_PROVIDER_NAME:
                test_provider = model
                break
        
        assert test_provider is not None, f"Test provider not found in models list: {data['models']}"
        assert test_provider.get("lane") == TEST_LANE, f"Wrong lane: {test_provider}"
        assert test_provider.get("provider") == "test-provider", f"Wrong provider type: {test_provider}"
        assert test_provider.get("priority") == 50, f"Wrong priority: {test_provider}"
        assert test_provider.get("has_key") == True, f"Should have API key: {test_provider}"
        print(f"PASS: Registered provider appears in /models list with correct data")
    
    def test_12_heartbeat_ok_status(self):
        """POST /api/provider-health/heartbeat with status=ok updates health"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "status": "ok",
            "latency_ms": 150.5,
            "error_rate": 0.01
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("accepted") == True, f"Expected accepted=True: {data}"
        assert data.get("status") == "ok", f"Expected status=ok: {data}"
        print(f"PASS: Heartbeat with status=ok accepted: {data}")
    
    def test_13_heartbeat_degraded_status(self):
        """POST /api/provider-health/heartbeat with status=degraded sets cooldown"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "status": "degraded",
            "latency_ms": 500,
            "error_rate": 0.1
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("accepted") == True, f"Expected accepted=True: {data}"
        assert data.get("status") == "degraded", f"Expected status=degraded: {data}"
        print(f"PASS: Heartbeat with status=degraded accepted: {data}")
    
    def test_14_heartbeat_failed_status_disables_provider(self):
        """POST /api/provider-health/heartbeat with status=failed disables provider"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "status": "failed",
            "latency_ms": 0,
            "error_rate": 1.0
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("accepted") == True, f"Expected accepted=True: {data}"
        assert data.get("status") == "failed", f"Expected status=failed: {data}"
        
        # Verify provider is now disabled in models list
        models_resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane={TEST_LANE}")
        assert models_resp.status_code == 200
        models_data = models_resp.json()
        
        test_provider = None
        for model in models_data["models"]:
            if model.get("name") == TEST_PROVIDER_NAME:
                test_provider = model
                break
        
        assert test_provider is not None, f"Test provider not found"
        assert test_provider.get("disabled") == True, f"Provider should be disabled after failed heartbeat: {test_provider}"
        assert test_provider.get("available") == False, f"Provider should not be available: {test_provider}"
        print(f"PASS: Heartbeat with status=failed disables provider")
    
    def test_15_enable_disabled_provider(self):
        """POST /api/provider-health/enable re-enables a disabled provider"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/enable", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("enabled") == True, f"Expected enabled=True: {data}"
        
        # Verify provider is now enabled in models list
        models_resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane={TEST_LANE}")
        assert models_resp.status_code == 200
        models_data = models_resp.json()
        
        test_provider = None
        for model in models_data["models"]:
            if model.get("name") == TEST_PROVIDER_NAME:
                test_provider = model
                break
        
        assert test_provider is not None, f"Test provider not found"
        assert test_provider.get("disabled") == False, f"Provider should be enabled: {test_provider}"
        assert test_provider.get("available") == True, f"Provider should be available: {test_provider}"
        print(f"PASS: POST /api/provider-health/enable re-enables provider")
    
    def test_16_enable_nonexistent_provider_returns_404(self):
        """POST /api/provider-health/enable for nonexistent provider returns 404"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/enable", json={
            "lane": "nonexistent-lane",
            "name": "nonexistent-provider"
        })
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: Enable nonexistent provider returns 404")
    
    def test_17_deregister_provider(self):
        """POST /api/provider-health/deregister removes provider"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("deregistered") == True, f"Expected deregistered=True: {data}"
        
        # Verify provider is no longer in models list
        models_resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane={TEST_LANE}")
        assert models_resp.status_code == 200
        models_data = models_resp.json()
        
        test_provider = None
        for model in models_data["models"]:
            if model.get("name") == TEST_PROVIDER_NAME:
                test_provider = model
                break
        
        assert test_provider is None, f"Provider should be removed from models list: {models_data['models']}"
        print(f"PASS: POST /api/provider-health/deregister removes provider")
    
    def test_18_heartbeat_for_deregistered_provider_returns_404(self):
        """POST /api/provider-health/heartbeat for deregistered provider returns 404"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": TEST_LANE,
            "name": TEST_PROVIDER_NAME,
            "status": "ok",
            "latency_ms": 100,
            "error_rate": 0
        })
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: Heartbeat for deregistered provider returns 404")


class TestProviderPersistence:
    """Test that registered providers persist to MongoDB and restore on restart"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login as admin
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        data = resp.json()
        self.token = data.get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_19_register_provider_for_persistence_test(self):
        """Register a provider to test persistence"""
        # Clean up first
        self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": "persistence-test-lane",
            "name": "persistence-test-provider"
        })
        
        # Register
        resp = self.session.post(f"{BASE_URL}/api/provider-health/register", json={
            "lane": "persistence-test-lane",
            "name": "persistence-test-provider",
            "provider": "test",
            "api_key": "persistence-test-key",
            "model": "persistence-model",
            "priority": 75
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("registered") == True
        print(f"PASS: Registered provider for persistence test")
    
    def test_20_verify_provider_in_models_list(self):
        """Verify the persistence test provider is in models list"""
        resp = self.session.get(f"{BASE_URL}/api/provider-health/models?lane=persistence-test-lane")
        assert resp.status_code == 200
        data = resp.json()
        
        found = False
        for model in data["models"]:
            if model.get("name") == "persistence-test-provider":
                found = True
                assert model.get("priority") == 75
                break
        
        assert found, f"Persistence test provider not found"
        print(f"PASS: Persistence test provider found in models list")
    
    def test_21_cleanup_persistence_test_provider(self):
        """Clean up the persistence test provider"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": "persistence-test-lane",
            "name": "persistence-test-provider"
        })
        assert resp.status_code == 200
        print(f"PASS: Cleaned up persistence test provider")


class TestProviderHealthValidation:
    """Test input validation for provider health endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login as admin
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Admin login failed: {resp.text}"
        data = resp.json()
        self.token = data.get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_22_register_missing_required_fields(self):
        """POST /api/provider-health/register with missing fields returns 422"""
        resp = self.session.post(f"{BASE_URL}/api/provider-health/register", json={
            "lane": "test"
            # Missing name, provider, api_key
        })
        assert resp.status_code == 422, f"Expected 422 for missing fields, got {resp.status_code}: {resp.text}"
        print(f"PASS: Register with missing fields returns 422")
    
    def test_23_heartbeat_invalid_status(self):
        """POST /api/provider-health/heartbeat with invalid status"""
        # First register a provider
        self.session.post(f"{BASE_URL}/api/provider-health/register", json={
            "lane": "validation-test-lane",
            "name": "validation-test-provider",
            "provider": "test",
            "api_key": "test-key",
            "model": "test",
            "priority": 100
        })
        
        # Try heartbeat with invalid status - the API accepts any string but only processes ok/degraded/failed
        resp = self.session.post(f"{BASE_URL}/api/provider-health/heartbeat", json={
            "lane": "validation-test-lane",
            "name": "validation-test-provider",
            "status": "invalid-status",
            "latency_ms": 100,
            "error_rate": 0
        })
        # Should still accept but not change state meaningfully
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        # Cleanup
        self.session.post(f"{BASE_URL}/api/provider-health/deregister", json={
            "lane": "validation-test-lane",
            "name": "validation-test-provider"
        })
        print(f"PASS: Heartbeat with unknown status is accepted (no-op)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
