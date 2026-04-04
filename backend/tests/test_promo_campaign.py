"""
Test suite for Promo Campaign feature.
Tests: POST /api/promo/create, GET /api/promo/active, GET /api/promo/progress,
       GET /api/promo/all, PUT /api/promo/{id}/toggle, DELETE /api/promo/{id}
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
FREE_USER_EMAIL = "freeuser_test@test.com"
FREE_USER_PASSWORD = "Test1234!"

# Known active promo ID from context
ACTIVE_PROMO_ID = "69d0d11e8617ff93a0953ad9"


class TestPromoActiveEndpoint:
    """Test GET /api/promo/active - public endpoint"""
    
    def test_active_promo_is_public(self):
        """Active promo endpoint should not require authentication"""
        response = requests.get(f"{BASE_URL}/api/promo/active")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASSED: /api/promo/active is public (no auth required)")
    
    def test_active_promo_response_structure(self):
        """Active promo should return promo object or null"""
        response = requests.get(f"{BASE_URL}/api/promo/active")
        assert response.status_code == 200
        data = response.json()
        assert "promo" in data, "Response should have 'promo' key"
        print(f"PASSED: Response has 'promo' key, value: {data['promo'] is not None}")
    
    def test_active_promo_fields(self):
        """If active promo exists, verify all required fields"""
        response = requests.get(f"{BASE_URL}/api/promo/active")
        assert response.status_code == 200
        data = response.json()
        
        if data["promo"]:
            promo = data["promo"]
            required_fields = ["id", "title", "message", "referral_target", "reward_months", "start_date", "end_date", "is_active"]
            for field in required_fields:
                assert field in promo, f"Missing field: {field}"
            assert promo["is_active"] == True, "Active promo should have is_active=True"
            print(f"PASSED: Active promo has all required fields. Title: {promo['title']}")
        else:
            print("INFO: No active promo currently exists")


class TestPromoProgressEndpoint:
    """Test GET /api/promo/progress - requires authentication"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    def test_progress_requires_auth(self):
        """Progress endpoint should require authentication"""
        response = requests.get(f"{BASE_URL}/api/promo/progress")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASSED: /api/promo/progress requires authentication (401 without token)")
    
    def test_progress_with_auth(self, owner_token):
        """Progress endpoint should return user's referral progress"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/promo/progress", headers=headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "progress" in data, "Response should have 'progress' key"
        assert "target" in data, "Response should have 'target' key"
        assert "completed" in data, "Response should have 'completed' key"
        print(f"PASSED: Progress endpoint returns data. Progress: {data['progress']}/{data['target']}, Completed: {data['completed']}")
    
    def test_progress_response_structure(self, owner_token):
        """Verify progress response has all required fields"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/promo/progress", headers=headers)
        assert response.status_code == 200
        
        data = response.json()
        # If there's an active promo, verify promo object
        if data.get("promo"):
            promo = data["promo"]
            assert "id" in promo
            assert "title" in promo
            assert "referral_target" in promo
            print(f"PASSED: Progress includes promo details. Promo: {promo['title']}")
        else:
            print("INFO: No active promo, progress returns null promo")


class TestPromoAllEndpoint:
    """Test GET /api/promo/all - admin only"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token (non-admin)"""
        # First try to login
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        
        # If login fails, try to register
        response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD,
            "name": "Free Test User"
        })
        if response.status_code in [200, 201]:
            return response.json().get("access_token")
        
        pytest.skip(f"Free user login/register failed: {response.status_code}")
    
    def test_all_promos_requires_auth(self):
        """All promos endpoint should require authentication"""
        response = requests.get(f"{BASE_URL}/api/promo/all")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASSED: /api/promo/all requires authentication (401 without token)")
    
    def test_all_promos_admin_only(self, free_user_token):
        """Non-admin users should get 403"""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        assert response.status_code == 403, f"Expected 403 for non-admin, got {response.status_code}: {response.text}"
        print("PASSED: /api/promo/all returns 403 for non-admin users")
    
    def test_all_promos_owner_access(self, owner_token):
        """Owner should be able to access all promos"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        assert response.status_code == 200, f"Expected 200 for owner, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "promos" in data, "Response should have 'promos' key"
        assert isinstance(data["promos"], list), "Promos should be a list"
        print(f"PASSED: Owner can access all promos. Count: {len(data['promos'])}")


class TestPromoCreateEndpoint:
    """Test POST /api/promo/create - admin only"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token (non-admin)"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Free user login failed: {response.status_code}")
    
    def test_create_promo_requires_auth(self):
        """Create promo should require authentication"""
        promo_data = {
            "title": "Test Promo",
            "message": "Test message",
            "referral_target": 3,
            "reward_months": 2,
            "start_date": datetime.now().isoformat(),
            "end_date": (datetime.now() + timedelta(days=30)).isoformat()
        }
        response = requests.post(f"{BASE_URL}/api/promo/create", json=promo_data)
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASSED: /api/promo/create requires authentication (401 without token)")
    
    def test_create_promo_admin_only(self, free_user_token):
        """Non-admin users should get 403"""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        promo_data = {
            "title": "Test Promo",
            "message": "Test message",
            "referral_target": 3,
            "reward_months": 2,
            "start_date": datetime.now().isoformat(),
            "end_date": (datetime.now() + timedelta(days=30)).isoformat()
        }
        response = requests.post(f"{BASE_URL}/api/promo/create", json=promo_data, headers=headers)
        assert response.status_code == 403, f"Expected 403 for non-admin, got {response.status_code}: {response.text}"
        print("PASSED: /api/promo/create returns 403 for non-admin users")
    
    def test_create_promo_owner_success(self, owner_token):
        """Owner should be able to create a promo"""
        headers = {"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"}
        promo_data = {
            "title": "TEST_Promo_Campaign",
            "message": "Test promo for automated testing",
            "referral_target": 5,
            "reward_months": 1,
            "start_date": datetime.now().isoformat(),
            "end_date": (datetime.now() + timedelta(days=7)).isoformat()
        }
        response = requests.post(f"{BASE_URL}/api/promo/create", json=promo_data, headers=headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "id" in data, "Response should have 'id'"
        assert data["title"] == "TEST_Promo_Campaign"
        assert data["referral_target"] == 5
        assert data["reward_months"] == 1
        print(f"PASSED: Owner created promo successfully. ID: {data['id']}")
        
        # Store ID for cleanup
        return data["id"]


class TestPromoToggleEndpoint:
    """Test PUT /api/promo/{id}/toggle - admin only"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token (non-admin)"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Free user login failed: {response.status_code}")
    
    def test_toggle_promo_requires_auth(self):
        """Toggle promo should require authentication"""
        response = requests.put(f"{BASE_URL}/api/promo/{ACTIVE_PROMO_ID}/toggle")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASSED: /api/promo/{id}/toggle requires authentication (401 without token)")
    
    def test_toggle_promo_admin_only(self, free_user_token):
        """Non-admin users should get 403"""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.put(f"{BASE_URL}/api/promo/{ACTIVE_PROMO_ID}/toggle", headers=headers)
        assert response.status_code == 403, f"Expected 403 for non-admin, got {response.status_code}: {response.text}"
        print("PASSED: /api/promo/{id}/toggle returns 403 for non-admin users")
    
    def test_toggle_promo_owner_success(self, owner_token):
        """Owner should be able to toggle promo status"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Get current status first
        response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        if response.status_code == 200:
            promos = response.json().get("promos", [])
            if promos:
                promo_id = promos[0]["id"]
                original_status = promos[0]["is_active"]
                
                # Toggle
                response = requests.put(f"{BASE_URL}/api/promo/{promo_id}/toggle", headers=headers)
                assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
                
                data = response.json()
                assert data["is_active"] != original_status, "Status should be toggled"
                print(f"PASSED: Owner toggled promo. New status: {data['is_active']}")
                
                # Toggle back to restore original state
                requests.put(f"{BASE_URL}/api/promo/{promo_id}/toggle", headers=headers)
                print("INFO: Restored original promo status")
            else:
                pytest.skip("No promos available to toggle")
        else:
            pytest.skip(f"Could not fetch promos: {response.status_code}")


class TestPromoDeleteEndpoint:
    """Test DELETE /api/promo/{id} - admin only"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token (non-admin)"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Free user login failed: {response.status_code}")
    
    def test_delete_promo_requires_auth(self):
        """Delete promo should require authentication"""
        response = requests.delete(f"{BASE_URL}/api/promo/fake_id_123")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASSED: /api/promo/{id} DELETE requires authentication (401 without token)")
    
    def test_delete_promo_admin_only(self, free_user_token):
        """Non-admin users should get 403"""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.delete(f"{BASE_URL}/api/promo/fake_id_123", headers=headers)
        assert response.status_code == 403, f"Expected 403 for non-admin, got {response.status_code}: {response.text}"
        print("PASSED: /api/promo/{id} DELETE returns 403 for non-admin users")
    
    def test_delete_promo_not_found(self, owner_token):
        """Delete non-existent promo should return 404"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.delete(f"{BASE_URL}/api/promo/000000000000000000000000", headers=headers)
        assert response.status_code == 404, f"Expected 404 for non-existent promo, got {response.status_code}"
        print("PASSED: Delete non-existent promo returns 404")


class TestPromoCreateAndDelete:
    """Test full create-delete cycle"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    def test_create_and_delete_promo(self, owner_token):
        """Create a promo and then delete it"""
        headers = {"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"}
        
        # Create
        promo_data = {
            "title": "TEST_Delete_Me_Promo",
            "message": "This promo will be deleted",
            "referral_target": 10,
            "reward_months": 3,
            "start_date": datetime.now().isoformat(),
            "end_date": (datetime.now() + timedelta(days=1)).isoformat()
        }
        create_response = requests.post(f"{BASE_URL}/api/promo/create", json=promo_data, headers=headers)
        assert create_response.status_code == 200, f"Create failed: {create_response.status_code}"
        
        promo_id = create_response.json()["id"]
        print(f"Created test promo with ID: {promo_id}")
        
        # Verify it exists
        all_response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        assert all_response.status_code == 200
        promos = all_response.json()["promos"]
        assert any(p["id"] == promo_id for p in promos), "Created promo should be in list"
        
        # Delete
        delete_response = requests.delete(f"{BASE_URL}/api/promo/{promo_id}", headers=headers)
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.status_code}"
        
        # Verify it's gone
        all_response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        promos = all_response.json()["promos"]
        assert not any(p["id"] == promo_id for p in promos), "Deleted promo should not be in list"
        
        print("PASSED: Create and delete promo cycle completed successfully")


class TestCleanupTestPromos:
    """Cleanup any TEST_ prefixed promos"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    def test_cleanup_test_promos(self, owner_token):
        """Delete any promos with TEST_ prefix"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        
        response = requests.get(f"{BASE_URL}/api/promo/all", headers=headers)
        if response.status_code == 200:
            promos = response.json().get("promos", [])
            test_promos = [p for p in promos if p["title"].startswith("TEST_")]
            
            for promo in test_promos:
                delete_response = requests.delete(f"{BASE_URL}/api/promo/{promo['id']}", headers=headers)
                if delete_response.status_code == 200:
                    print(f"Cleaned up test promo: {promo['title']}")
            
            print(f"PASSED: Cleanup complete. Removed {len(test_promos)} test promos")
        else:
            print("INFO: Could not fetch promos for cleanup")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
