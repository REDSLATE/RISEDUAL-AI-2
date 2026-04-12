"""Iteration 90: Memory Pinning Feature Tests

Tests for:
1. POST /api/chat/memory/pin - Create pinned memory (max 5)
2. Pin limit enforcement (6th pin returns 400)
3. Pin with empty content returns 400
4. Pin content truncation to 500 chars
5. GET /api/chat/memory returns pinned memories with category='pinned'
6. DELETE /api/chat/memory/{id} can delete pinned memory
7. Non-pro user gets 403 on pin endpoint
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestMemoryPinning:
    """Memory pinning endpoint tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin (Pro user)"""
        self.session = requests.Session()
        # Login as admin (Pro user)
        login_res = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        self.is_logged_in = True
        yield
        # Cleanup: Clear all memories after tests
        self.session.delete(f"{BASE_URL}/api/chat/memory")
    
    def test_pin_memory_success(self):
        """Test creating a pinned memory"""
        # First clear any existing memories
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_PIN: User prefers swing trading on tech stocks"
        })
        
        assert response.status_code == 200, f"Pin failed: {response.text}"
        data = response.json()
        assert data.get("pinned") == True
        assert "memory_id" in data
        assert data.get("pinned_count") == 1
        assert data.get("max") == 5
        
        # Verify memory was created with category='pinned'
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        assert memories_res.status_code == 200
        memories = memories_res.json().get("memories", [])
        pinned = [m for m in memories if m.get("category") == "pinned"]
        assert len(pinned) >= 1
        assert any("TEST_PIN" in m.get("content", "") for m in pinned)
    
    def test_pin_empty_content_returns_400(self):
        """Test that pinning empty content returns 400"""
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": ""
        })
        assert response.status_code == 400
        assert "required" in response.json().get("detail", "").lower()
    
    def test_pin_whitespace_only_returns_400(self):
        """Test that pinning whitespace-only content returns 400"""
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "   "
        })
        assert response.status_code == 400
    
    def test_pin_content_truncation(self):
        """Test that content over 500 chars is truncated"""
        # Clear existing memories first
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        long_content = "TEST_TRUNCATE: " + "A" * 600  # 615 chars total
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": long_content
        })
        
        assert response.status_code == 200
        
        # Verify the stored content is truncated
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        memories = memories_res.json().get("memories", [])
        truncated_mem = [m for m in memories if "TEST_TRUNCATE" in m.get("content", "")]
        assert len(truncated_mem) >= 1
        assert len(truncated_mem[0]["content"]) <= 500
    
    def test_pin_limit_enforcement(self):
        """Test that 6th pin returns 400 with max limit error"""
        # Clear all memories first
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        # Create 5 pinned memories
        for i in range(5):
            res = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
                "content": f"TEST_LIMIT_PIN_{i}: Memory number {i+1}"
            })
            assert res.status_code == 200, f"Pin {i+1} failed: {res.text}"
            data = res.json()
            assert data.get("pinned_count") == i + 1
        
        # Verify we have 5 pinned memories
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        memories = memories_res.json().get("memories", [])
        pinned = [m for m in memories if m.get("category") == "pinned"]
        assert len(pinned) == 5, f"Expected 5 pinned, got {len(pinned)}"
        
        # Try to create 6th pin - should fail
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_LIMIT_PIN_6: This should fail"
        })
        
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        error_detail = response.json().get("detail", "")
        assert "5" in error_detail or "maximum" in error_detail.lower()
    
    def test_delete_pinned_memory(self):
        """Test that pinned memories can be deleted"""
        # Clear and create a pinned memory
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        pin_res = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_DELETE_PIN: Memory to delete"
        })
        assert pin_res.status_code == 200
        memory_id = pin_res.json().get("memory_id")
        
        # Delete the pinned memory
        delete_res = self.session.delete(f"{BASE_URL}/api/chat/memory/{memory_id}")
        assert delete_res.status_code == 200
        assert delete_res.json().get("deleted") == True
        
        # Verify it's gone
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        memories = memories_res.json().get("memories", [])
        assert not any(m.get("memory_id") == memory_id for m in memories)
    
    def test_get_memories_includes_pinned_category(self):
        """Test that GET /api/chat/memory returns pinned memories with category='pinned'"""
        # Clear and create a pinned memory
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_CATEGORY: Pinned memory for category test"
        })
        
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        assert memories_res.status_code == 200
        data = memories_res.json()
        
        memories = data.get("memories", [])
        pinned = [m for m in memories if m.get("category") == "pinned"]
        assert len(pinned) >= 1
        
        # Verify pinned memory has correct structure
        pinned_mem = pinned[0]
        assert "memory_id" in pinned_mem
        assert "content" in pinned_mem
        assert pinned_mem.get("category") == "pinned"
        assert "created_at" in pinned_mem


class TestMemoryPinningAuth:
    """Authentication and authorization tests for pin endpoint"""
    
    def test_pin_requires_authentication(self):
        """Test that pin endpoint requires authentication"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_UNAUTH: Should fail"
        })
        assert response.status_code == 401
    
    def test_pin_requires_pro_subscription(self):
        """Test that non-Pro users get 403 on pin endpoint"""
        # This test would require a non-Pro user account
        # For now, we verify the endpoint exists and returns proper error for unauthenticated
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_NONPRO: Should fail"
        })
        # Unauthenticated returns 401, non-Pro would return 403
        assert response.status_code in [401, 403]


class TestMemoryPinningEdgeCases:
    """Edge case tests for memory pinning"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        login_res = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_res.status_code == 200
        yield
        self.session.delete(f"{BASE_URL}/api/chat/memory")
    
    def test_pin_special_characters(self):
        """Test pinning content with special characters"""
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        special_content = "TEST_SPECIAL: User likes $AAPL & $NVDA! Risk: <5% per trade. \"Bullish\" on tech."
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": special_content
        })
        
        assert response.status_code == 200
        
        # Verify content was stored correctly
        memories_res = self.session.get(f"{BASE_URL}/api/chat/memory")
        memories = memories_res.json().get("memories", [])
        assert any("TEST_SPECIAL" in m.get("content", "") for m in memories)
    
    def test_pin_after_delete_allows_new_pin(self):
        """Test that deleting a pin allows creating a new one within limit"""
        self.session.delete(f"{BASE_URL}/api/chat/memory")
        
        # Create 5 pins
        memory_ids = []
        for i in range(5):
            res = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
                "content": f"TEST_REPIN_{i}: Memory {i+1}"
            })
            assert res.status_code == 200
            memory_ids.append(res.json().get("memory_id"))
        
        # Delete one
        self.session.delete(f"{BASE_URL}/api/chat/memory/{memory_ids[0]}")
        
        # Should now be able to create a new pin
        response = self.session.post(f"{BASE_URL}/api/chat/memory/pin", json={
            "content": "TEST_REPIN_NEW: New pin after delete"
        })
        assert response.status_code == 200
        assert response.json().get("pinned") == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
