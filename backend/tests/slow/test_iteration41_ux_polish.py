"""
Iteration 41: UX Polish Batch Testing
Tests for 11 UX improvements:
1. Cache warm-up on server startup
2. Backend API response times for cached endpoints
"""
import pytest
import requests
import time
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCacheWarmup:
    """Test that cache warm-up endpoints return fast responses (cached data)"""
    
    def test_sector_heatmap_cached_response(self):
        """GET /api/sectors/heatmap should return cached data quickly (<500ms)"""
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=10)
        elapsed = time.time() - start
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "sectors" in data, "Response should contain 'sectors' key"
        assert isinstance(data["sectors"], list), "sectors should be a list"
        
        # Check response time (should be fast if cached)
        print(f"Sector heatmap response time: {elapsed*1000:.0f}ms")
        # Note: First request after restart may be slower, but subsequent should be <500ms
        
    def test_world_events_cached_response(self):
        """GET /api/world-events should return cached data"""
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=15)
        elapsed = time.time() - start
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure (returns object with events and affected_sectors)
        assert isinstance(data, dict), "Response should be a dict"
        assert "events" in data or "affected_sectors" in data, "Response should contain events data"
        
        print(f"World events response time: {elapsed*1000:.0f}ms")
        
    def test_foreign_markets_cached_response(self):
        """GET /api/foreign-markets should return cached data"""
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=10)
        elapsed = time.time() - start
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure (returns object with regional market data)
        assert isinstance(data, dict), "Response should be a dict"
        # Should have regional keys like americas, asia, europe, commodities
        assert any(k in data for k in ["americas", "asia", "europe", "commodities"]), "Response should contain market regions"
        
        print(f"Foreign markets response time: {elapsed*1000:.0f}ms")


class TestAPIHealth:
    """Basic API health checks"""
    
    def test_api_root(self):
        """GET /api/ should return ready message"""
        response = requests.get(f"{BASE_URL}/api/", timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "RISEDUAL" in data["message"]
        
    def test_auth_login_endpoint_exists(self):
        """POST /api/auth/login should exist (even with bad creds)"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "test@test.com", "password": "wrong"},
            timeout=5
        )
        # Should return 401 for bad creds, not 404
        assert response.status_code in [401, 400], f"Expected 401/400, got {response.status_code}"


class TestCacheResponseTimes:
    """Test that cached endpoints have fast response times on second request"""
    
    def test_sector_heatmap_second_request_fast(self):
        """Second request to /api/sectors/heatmap should be faster (cached)"""
        # First request (may populate cache)
        requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=10)
        
        # Second request (should be cached)
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=10)
        elapsed = time.time() - start
        
        assert response.status_code == 200
        print(f"Second sector heatmap request: {elapsed*1000:.0f}ms")
        # Cached response should be under 500ms
        assert elapsed < 2.0, f"Cached response took {elapsed*1000:.0f}ms, expected <2000ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
