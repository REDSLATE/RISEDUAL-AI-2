"""
Test Cache Admin Endpoints - Iteration 40
Tests for the new Cache Stats Dashboard feature in Admin Panel:
- GET /api/admin/cache-stats (requires owner/admin auth)
- POST /api/admin/cache-invalidate/{key} (removes a key from cache)
- POST /api/admin/cache-clear (clears all cache entries)
- Auth checks: 403 for non-admin, 401 for unauthenticated
"""
import pytest
import requests
import os
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD
ADMIN_EMAIL = ADMIN_EMAIL
ADMIN_PASSWORD = ADMIN_PASSWORD


class TestCacheAdminEndpoints:
    """Cache admin endpoint tests - requires owner/admin auth via httpOnly cookies"""

    @pytest.fixture(scope="class")
    def owner_session(self):
        """Login as owner and return session with cookies"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert res.status_code == 200, f"Owner login failed: {res.text}"
        return session

    @pytest.fixture(scope="class")
    def admin_session(self):
        """Login as admin and return session with cookies"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert res.status_code == 200, f"Admin login failed: {res.text}"
        return session

    @pytest.fixture(scope="class")
    def unauthenticated_session(self):
        """Return session without auth"""
        return requests.Session()

    # ==================== AUTH TESTS ====================

    def test_cache_stats_unauthenticated_returns_401(self, unauthenticated_session):
        """GET /api/admin/cache-stats should return 401 for unauthenticated users"""
        res = unauthenticated_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"

    def test_cache_invalidate_unauthenticated_returns_401(self, unauthenticated_session):
        """POST /api/admin/cache-invalidate/{key} should return 401 for unauthenticated users"""
        res = unauthenticated_session.post(f"{BASE_URL}/api/admin/cache-invalidate/test_key")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"

    def test_cache_clear_unauthenticated_returns_401(self, unauthenticated_session):
        """POST /api/admin/cache-clear should return 401 for unauthenticated users"""
        res = unauthenticated_session.post(f"{BASE_URL}/api/admin/cache-clear")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"

    # ==================== OWNER ACCESS TESTS ====================

    def test_cache_stats_owner_access(self, owner_session):
        """GET /api/admin/cache-stats should return 200 for owner"""
        res = owner_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        
        data = res.json()
        # Verify response structure
        assert "entries" in data, "Response should have 'entries' field"
        assert "total_keys" in data, "Response should have 'total_keys' field"
        assert "hits" in data, "Response should have 'hits' field"
        assert "misses" in data, "Response should have 'misses' field"
        assert "hit_rate" in data, "Response should have 'hit_rate' field"
        assert "total_requests" in data, "Response should have 'total_requests' field"
        assert "uptime_seconds" in data, "Response should have 'uptime_seconds' field"
        
        # Verify data types
        assert isinstance(data["entries"], list), "entries should be a list"
        assert isinstance(data["total_keys"], int), "total_keys should be an int"
        assert isinstance(data["hits"], int), "hits should be an int"
        assert isinstance(data["misses"], int), "misses should be an int"
        assert isinstance(data["hit_rate"], (int, float)), "hit_rate should be numeric"
        assert isinstance(data["total_requests"], int), "total_requests should be an int"
        assert isinstance(data["uptime_seconds"], (int, float)), "uptime_seconds should be numeric"

    def test_cache_clear_owner_access(self, owner_session):
        """POST /api/admin/cache-clear should return 200 for owner"""
        res = owner_session.post(f"{BASE_URL}/api/admin/cache-clear")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        
        data = res.json()
        assert data.get("ok") == True, "Response should have ok=True"
        assert "message" in data, "Response should have message field"

    # ==================== ADMIN ACCESS TESTS ====================

    def test_cache_stats_admin_access(self, admin_session):
        """GET /api/admin/cache-stats should return 200 for admin"""
        res = admin_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        
        data = res.json()
        assert "entries" in data
        assert "total_keys" in data
        assert "hit_rate" in data

    def test_cache_clear_admin_access(self, admin_session):
        """POST /api/admin/cache-clear should return 200 for admin"""
        res = admin_session.post(f"{BASE_URL}/api/admin/cache-clear")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    # ==================== CACHE POPULATION & INVALIDATION TESTS ====================

    def test_populate_cache_then_verify_stats(self, owner_session):
        """Hit a cached endpoint, then verify cache stats show the entry"""
        # First clear cache
        owner_session.post(f"{BASE_URL}/api/admin/cache-clear")
        
        # Hit an endpoint that uses cache (sectors/heatmap uses TTLCache)
        # This may take a few seconds
        heatmap_res = owner_session.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        assert heatmap_res.status_code == 200, f"Heatmap request failed: {heatmap_res.text}"
        
        # Now check cache stats
        stats_res = owner_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert stats_res.status_code == 200
        
        data = stats_res.json()
        # After hitting heatmap, we should have at least 1 cache entry
        assert data["total_keys"] >= 1, f"Expected at least 1 cached key, got {data['total_keys']}"
        assert data["misses"] >= 1, f"Expected at least 1 miss (first request), got {data['misses']}"

    def test_cache_invalidate_specific_key(self, owner_session):
        """Test invalidating a specific cache key"""
        # First get current cache stats
        stats_res = owner_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert stats_res.status_code == 200
        
        data = stats_res.json()
        if data["total_keys"] > 0 and len(data["entries"]) > 0:
            # Get first key and invalidate it
            key_to_invalidate = data["entries"][0]["key"]
            initial_count = data["total_keys"]
            
            # Invalidate the key
            invalidate_res = owner_session.post(f"{BASE_URL}/api/admin/cache-invalidate/{key_to_invalidate}")
            assert invalidate_res.status_code == 200, f"Invalidate failed: {invalidate_res.text}"
            
            inv_data = invalidate_res.json()
            assert inv_data.get("ok") == True
            assert inv_data.get("invalidated") == key_to_invalidate
            
            # Verify key was removed
            stats_after = owner_session.get(f"{BASE_URL}/api/admin/cache-stats").json()
            assert stats_after["total_keys"] == initial_count - 1, "Key count should decrease by 1"
        else:
            # No keys to invalidate, just verify endpoint works
            invalidate_res = owner_session.post(f"{BASE_URL}/api/admin/cache-invalidate/nonexistent_key")
            assert invalidate_res.status_code == 200

    def test_cache_entry_structure(self, owner_session):
        """Verify cache entry structure has required fields"""
        # Populate cache first
        owner_session.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        
        stats_res = owner_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert stats_res.status_code == 200
        
        data = stats_res.json()
        if len(data["entries"]) > 0:
            entry = data["entries"][0]
            assert "key" in entry, "Entry should have 'key' field"
            assert "age_seconds" in entry, "Entry should have 'age_seconds' field"
            assert "refreshing" in entry, "Entry should have 'refreshing' field"
            assert "size_bytes" in entry, "Entry should have 'size_bytes' field"
            
            # Verify types
            assert isinstance(entry["key"], str)
            assert isinstance(entry["age_seconds"], (int, float))
            assert isinstance(entry["refreshing"], bool)
            assert isinstance(entry["size_bytes"], int)

    def test_cache_clear_removes_all_entries(self, owner_session):
        """Test that cache-clear removes all entries"""
        # Populate cache
        owner_session.get(f"{BASE_URL}/api/sectors/heatmap", timeout=30)
        
        # Clear cache
        clear_res = owner_session.post(f"{BASE_URL}/api/admin/cache-clear")
        assert clear_res.status_code == 200
        
        # Verify cache is empty
        stats_res = owner_session.get(f"{BASE_URL}/api/admin/cache-stats")
        data = stats_res.json()
        assert data["total_keys"] == 0, f"Expected 0 keys after clear, got {data['total_keys']}"
        assert len(data["entries"]) == 0, "Entries list should be empty after clear"


class TestCacheAdminNonAdminAccess:
    """Test that non-admin users get 403 Forbidden"""

    @pytest.fixture(scope="class")
    def regular_user_session(self):
        """Create a regular user and login"""
        session = requests.Session()
        # First register a new user
        import uuid
        test_email = f"test_cache_{uuid.uuid4().hex[:8]}@test.com"
        
        reg_res = session.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Cache User"
        })
        
        if reg_res.status_code == 201:
            # Login with the new user
            login_res = session.post(f"{BASE_URL}/api/auth/login", json={
                "email": test_email,
                "password": "TestPass123!"
            })
            if login_res.status_code == 200:
                return session
        
        pytest.skip("Could not create regular user for testing")

    def test_cache_stats_regular_user_returns_403(self, regular_user_session):
        """GET /api/admin/cache-stats should return 403 for regular users"""
        res = regular_user_session.get(f"{BASE_URL}/api/admin/cache-stats")
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

    def test_cache_invalidate_regular_user_returns_403(self, regular_user_session):
        """POST /api/admin/cache-invalidate/{key} should return 403 for regular users"""
        res = regular_user_session.post(f"{BASE_URL}/api/admin/cache-invalidate/test_key")
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

    def test_cache_clear_regular_user_returns_403(self, regular_user_session):
        """POST /api/admin/cache-clear should return 403 for regular users"""
        res = regular_user_session.post(f"{BASE_URL}/api/admin/cache-clear")
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
