"""
Iteration 129: Key Vault API Tests
Tests for the secure API key vault feature:
- POST /api/vault/keys — stores a key (admin only)
- GET /api/vault/keys — lists keys with previews (admin only)
- DELETE /api/vault/keys/{name} — deletes a key (admin only)
- Non-admin users get 403 on all vault endpoints
- Key preview format: first 8 chars + ... + last 4 chars
"""
import pytest
import requests
import os

from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, TEST_USER_PASSWORD

# Test key data
TEST_KEY_NAME = "TEST_E2E_KEY_129"
TEST_KEY_VALUE = "test-value-123456789"  # 20 chars for proper preview
TEST_KEY_CATEGORY = "general"
TEST_KEY_DESCRIPTION = "E2E test key for iteration 129"


class TestVaultAuth:
    """Test authentication requirements for vault endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_list_keys_requires_auth(self):
        """GET /api/vault/keys without auth returns 401"""
        response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("PASS: GET /api/vault/keys requires authentication")
    
    def test_store_key_requires_auth(self):
        """POST /api/vault/keys without auth returns 401"""
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": "TEST_UNAUTH_KEY",
            "value": "test-value",
            "category": "general"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("PASS: POST /api/vault/keys requires authentication")
    
    def test_delete_key_requires_auth(self):
        """DELETE /api/vault/keys/{name} without auth returns 401"""
        response = self.session.delete(f"{BASE_URL}/api/vault/keys/TEST_KEY")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("PASS: DELETE /api/vault/keys/{name} requires authentication")


class TestVaultAdminAccess:
    """Test admin-only access for vault endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_response.status_code == 200, f"Admin login failed: {login_response.text}"
        
        # Extract token from response
        login_data = login_response.json()
        if "access_token" in login_data:
            self.session.headers.update({"Authorization": f"Bearer {login_data['access_token']}"})
        
        print(f"Admin login successful, role: {login_data.get('user', {}).get('role', 'unknown')}")
    
    def test_admin_can_list_keys(self):
        """Admin can GET /api/vault/keys"""
        response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "keys" in data, "Response should contain 'keys' field"
        assert "count" in data, "Response should contain 'count' field"
        assert isinstance(data["keys"], list), "'keys' should be a list"
        assert data["count"] == len(data["keys"]), "count should match keys length"
        
        print(f"PASS: Admin can list keys, found {data['count']} keys")
    
    def test_admin_can_store_key(self):
        """Admin can POST /api/vault/keys to store a key"""
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": TEST_KEY_NAME,
            "value": TEST_KEY_VALUE,
            "category": TEST_KEY_CATEGORY,
            "description": TEST_KEY_DESCRIPTION
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("stored") == True, "Response should have stored: true"
        assert data.get("name") == TEST_KEY_NAME, f"Response name should be {TEST_KEY_NAME}"
        assert "message" in data, "Response should contain message"
        
        print(f"PASS: Admin stored key '{TEST_KEY_NAME}' successfully")
    
    def test_stored_key_appears_in_list(self):
        """Stored key appears in GET /api/vault/keys list"""
        # First store the key
        store_response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": TEST_KEY_NAME,
            "value": TEST_KEY_VALUE,
            "category": TEST_KEY_CATEGORY,
            "description": TEST_KEY_DESCRIPTION
        })
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"
        
        # Then list keys
        list_response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert list_response.status_code == 200, f"List failed: {list_response.text}"
        
        data = list_response.json()
        keys = data.get("keys", [])
        
        # Find our test key
        test_key = next((k for k in keys if k.get("name") == TEST_KEY_NAME), None)
        assert test_key is not None, f"Test key '{TEST_KEY_NAME}' not found in list"
        
        # Verify key data
        assert test_key.get("category") == TEST_KEY_CATEGORY, "Category mismatch"
        assert test_key.get("description") == TEST_KEY_DESCRIPTION, "Description mismatch"
        assert "preview" in test_key, "Key should have preview"
        assert "encrypted_value" not in test_key, "Encrypted value should NOT be exposed"
        
        print(f"PASS: Stored key appears in list with correct data")
    
    def test_key_preview_format(self):
        """Key preview shows first 8 chars + ... + last 4 chars"""
        # Store a key with known value
        store_response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": TEST_KEY_NAME,
            "value": TEST_KEY_VALUE,  # "test-value-123456789" (20 chars)
            "category": TEST_KEY_CATEGORY
        })
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"
        
        # List keys and check preview
        list_response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert list_response.status_code == 200, f"List failed: {list_response.text}"
        
        data = list_response.json()
        test_key = next((k for k in data.get("keys", []) if k.get("name") == TEST_KEY_NAME), None)
        assert test_key is not None, f"Test key not found"
        
        preview = test_key.get("preview", "")
        # Expected: "test-val...6789" (first 8 + ... + last 4)
        expected_preview = TEST_KEY_VALUE[:8] + "..." + TEST_KEY_VALUE[-4:]
        assert preview == expected_preview, f"Preview mismatch: got '{preview}', expected '{expected_preview}'"
        
        print(f"PASS: Key preview format correct: '{preview}'")
    
    def test_admin_can_delete_key(self):
        """Admin can DELETE /api/vault/keys/{name}"""
        # First store a key
        store_response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": TEST_KEY_NAME,
            "value": TEST_KEY_VALUE,
            "category": TEST_KEY_CATEGORY
        })
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"
        
        # Delete the key
        delete_response = self.session.delete(f"{BASE_URL}/api/vault/keys/{TEST_KEY_NAME}")
        assert delete_response.status_code == 200, f"Expected 200, got {delete_response.status_code}: {delete_response.text}"
        
        data = delete_response.json()
        assert data.get("deleted") == True, "Response should have deleted: true"
        assert data.get("name") == TEST_KEY_NAME, f"Response name should be {TEST_KEY_NAME}"
        
        print(f"PASS: Admin deleted key '{TEST_KEY_NAME}' successfully")
    
    def test_deleted_key_not_in_list(self):
        """Deleted key no longer appears in list"""
        # Store a key
        store_response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": TEST_KEY_NAME,
            "value": TEST_KEY_VALUE,
            "category": TEST_KEY_CATEGORY
        })
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"
        
        # Delete the key
        delete_response = self.session.delete(f"{BASE_URL}/api/vault/keys/{TEST_KEY_NAME}")
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.text}"
        
        # List keys and verify key is gone
        list_response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert list_response.status_code == 200, f"List failed: {list_response.text}"
        
        data = list_response.json()
        test_key = next((k for k in data.get("keys", []) if k.get("name") == TEST_KEY_NAME), None)
        assert test_key is None, f"Deleted key '{TEST_KEY_NAME}' should not appear in list"
        
        print(f"PASS: Deleted key no longer in list")
    
    def test_delete_nonexistent_key_returns_404(self):
        """DELETE /api/vault/keys/{name} for nonexistent key returns 404"""
        response = self.session.delete(f"{BASE_URL}/api/vault/keys/NONEXISTENT_KEY_12345")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        
        print("PASS: Delete nonexistent key returns 404")
    
    def test_store_key_missing_name_returns_422(self):
        """POST /api/vault/keys without name returns 422"""
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "value": "test-value",
            "category": "general"
        })
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        
        print("PASS: Store key without name returns 422")
    
    def test_store_key_missing_value_returns_422(self):
        """POST /api/vault/keys without value returns 422"""
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": "TEST_KEY",
            "category": "general"
        })
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        
        print("PASS: Store key without value returns 422")


class TestVaultNonAdminAccess:
    """Test that non-admin users get 403 on vault endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session - we'll create a regular user or use unauthenticated requests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Try to register a test user (may already exist)
        test_email = "test_vault_user_129@test.com"
        test_password = TEST_USER_PASSWORD
        
        # Try to register
        register_response = self.session.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": test_password,
            "name": "Test Vault User"
        })
        
        # Login as test user (whether newly registered or existing)
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": test_email,
            "password": test_password
        })
        
        if login_response.status_code == 200:
            login_data = login_response.json()
            if "access_token" in login_data:
                self.session.headers.update({"Authorization": f"Bearer {login_data['access_token']}"})
            self.user_role = login_data.get("user", {}).get("role", "unknown")
            self.logged_in = True
            print(f"Logged in as test user with role: {self.user_role}")
        else:
            self.logged_in = False
            print(f"Could not login as test user: {login_response.text}")
    
    def test_non_admin_cannot_list_keys(self):
        """Non-admin user gets 403 on GET /api/vault/keys"""
        if not self.logged_in:
            pytest.skip("Could not login as test user")
        
        response = self.session.get(f"{BASE_URL}/api/vault/keys")
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        
        print("PASS: Non-admin cannot list vault keys (403)")
    
    def test_non_admin_cannot_store_key(self):
        """Non-admin user gets 403 on POST /api/vault/keys"""
        if not self.logged_in:
            pytest.skip("Could not login as test user")
        
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": "TEST_NONADMIN_KEY",
            "value": "test-value",
            "category": "general"
        })
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        
        print("PASS: Non-admin cannot store vault key (403)")
    
    def test_non_admin_cannot_delete_key(self):
        """Non-admin user gets 403 on DELETE /api/vault/keys/{name}"""
        if not self.logged_in:
            pytest.skip("Could not login as test user")
        
        response = self.session.delete(f"{BASE_URL}/api/vault/keys/TEST_KEY")
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        
        print("PASS: Non-admin cannot delete vault key (403)")


class TestVaultKeyCategories:
    """Test different key categories"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_response.status_code == 200, f"Admin login failed: {login_response.text}"
        
        login_data = login_response.json()
        if "access_token" in login_data:
            self.session.headers.update({"Authorization": f"Bearer {login_data['access_token']}"})
    
    def test_store_ai_category_key(self):
        """Can store key with 'ai' category"""
        key_name = "TEST_AI_KEY_129"
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": key_name,
            "value": "sk-test-ai-key-value",
            "category": "ai",
            "description": "Test AI provider key"
        })
        assert response.status_code == 200, f"Store failed: {response.text}"
        
        # Verify category in list
        list_response = self.session.get(f"{BASE_URL}/api/vault/keys")
        data = list_response.json()
        test_key = next((k for k in data.get("keys", []) if k.get("name") == key_name), None)
        assert test_key is not None, "Key not found"
        assert test_key.get("category") == "ai", "Category should be 'ai'"
        
        # Cleanup
        self.session.delete(f"{BASE_URL}/api/vault/keys/{key_name}")
        
        print("PASS: Can store key with 'ai' category")
    
    def test_store_market_data_category_key(self):
        """Can store key with 'market_data' category"""
        key_name = "TEST_MARKET_KEY_129"
        response = self.session.post(f"{BASE_URL}/api/vault/keys", json={
            "name": key_name,
            "value": "test-market-data-key",
            "category": "market_data",
            "description": "Test market data key"
        })
        assert response.status_code == 200, f"Store failed: {response.text}"
        
        # Verify category in list
        list_response = self.session.get(f"{BASE_URL}/api/vault/keys")
        data = list_response.json()
        test_key = next((k for k in data.get("keys", []) if k.get("name") == key_name), None)
        assert test_key is not None, "Key not found"
        assert test_key.get("category") == "market_data", "Category should be 'market_data'"
        
        # Cleanup
        self.session.delete(f"{BASE_URL}/api/vault/keys/{key_name}")
        
        print("PASS: Can store key with 'market_data' category")


class TestVaultCleanup:
    """Cleanup test data after all tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if login_response.status_code == 200:
            login_data = login_response.json()
            if "access_token" in login_data:
                self.session.headers.update({"Authorization": f"Bearer {login_data['access_token']}"})
    
    def test_cleanup_test_keys(self):
        """Cleanup any test keys created during testing"""
        test_keys = [
            TEST_KEY_NAME,
            "TEST_AI_KEY_129",
            "TEST_MARKET_KEY_129",
            "TEST_UNAUTH_KEY",
            "TEST_NONADMIN_KEY"
        ]
        
        for key_name in test_keys:
            response = self.session.delete(f"{BASE_URL}/api/vault/keys/{key_name}")
            if response.status_code == 200:
                print(f"Cleaned up test key: {key_name}")
        
        print("PASS: Test cleanup completed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
