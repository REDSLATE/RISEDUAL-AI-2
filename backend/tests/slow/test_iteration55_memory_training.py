"""Iteration 55: Memory Training Feature Tests

Tests the NEW memory training feature that bulk-ingests 2 years of historical market data.
Training has already been completed with ~2973 episodes stored.

Features tested:
- POST /api/accuracy/memory/train — triggers background training (Pro only, requires auth)
- POST /api/accuracy/memory/train without auth — should return 401/403
- GET /api/accuracy/memory/train/status — returns training progress/completion status
- GET /api/accuracy/memory — should show ~2973 total_episodes after training
- GET /api/accuracy/memory — verify embedding_model is 'all-MiniLM-L6-v2 (local)'
- Regression tests: sectors/heatmap, stocks/quote/AAPL, auth/login
"""
import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from /app/memory/test_credentials.md
class TestMemoryTrainingAuth:
    """Test authentication requirements for memory training endpoints."""
    
    def test_memory_train_without_auth_returns_401_or_403(self):
        """POST /api/accuracy/memory/train without auth should return 401/403."""
        response = requests.post(f"{BASE_URL}/api/accuracy/memory/train")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}: {response.text}"
        print(f"PASS: POST /api/accuracy/memory/train without auth returns {response.status_code}")
    
    def test_memory_train_status_without_auth_returns_401_or_403(self):
        """GET /api/accuracy/memory/train/status without auth should return 401/403."""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory/train/status")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}: {response.text}"
        print(f"PASS: GET /api/accuracy/memory/train/status without auth returns {response.status_code}")
    
    def test_memory_stats_without_auth_returns_401_or_403(self):
        """GET /api/accuracy/memory without auth should return 401/403."""
        response = requests.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}: {response.text}"
        print(f"PASS: GET /api/accuracy/memory without auth returns {response.status_code}")


class TestMemoryTrainingWithAuth:
    """Test memory training endpoints with valid Pro authentication."""
    
    @pytest.fixture(autouse=True)
    def setup_session(self):
        """Login and get authenticated session."""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        print("PASS: Admin login successful")
        yield
        self.session.close()
    
    def test_memory_stats_shows_training_complete(self):
        """GET /api/accuracy/memory should show ~2973 total_episodes after training."""
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        print(f"Memory stats response: {data}")
        
        # Verify structure
        assert "total_episodes" in data, "Missing total_episodes field"
        assert "embedding_model" in data, "Missing embedding_model field"
        assert "initialized" in data, "Missing initialized field"
        
        # Verify training completed with ~2973 episodes
        total_episodes = data.get("total_episodes", 0)
        assert total_episodes >= 2900, f"Expected ~2973 episodes, got {total_episodes}"
        print(f"PASS: Memory has {total_episodes} episodes (expected ~2973)")
        
        # Verify embedding model
        embedding_model = data.get("embedding_model", "")
        assert "all-MiniLM-L6-v2" in embedding_model, f"Expected all-MiniLM-L6-v2, got {embedding_model}"
        print(f"PASS: Embedding model is '{embedding_model}'")
        
        # Verify initialized
        assert data.get("initialized"), "Memory should be initialized"
        print("PASS: Memory is initialized")
    
    def test_memory_train_status_shows_complete_or_idle(self):
        """GET /api/accuracy/memory/train/status should show training status."""
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory/train/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        print(f"Training status response: {data}")
        
        # Status should be 'complete', 'idle', or 'running'
        status = data.get("status", "")
        assert status in ["complete", "idle", "running", "already_running"], f"Unexpected status: {status}"
        print(f"PASS: Training status is '{status}'")
        
        # If complete, verify stats
        if status == "complete":
            assert "total_regimes_ingested" in data or "symbols_processed" in data, "Complete status should have stats"
            print(f"PASS: Training completed with stats: {data}")
    
    def test_memory_train_endpoint_responds(self):
        """POST /api/accuracy/memory/train should respond (may be already_running or started)."""
        response = self.session.post(f"{BASE_URL}/api/accuracy/memory/train")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        print(f"Training trigger response: {data}")
        
        # Should return 'already_running', 'started', or similar
        status = data.get("status", "")
        assert status in ["already_running", "started", "complete"], f"Unexpected status: {status}"
        print(f"PASS: POST /api/accuracy/memory/train returned status '{status}'")


class TestRegressionEndpoints:
    """Regression tests for existing endpoints."""
    
    def test_auth_login_works(self):
        """POST /api/auth/login should work with valid credentials."""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.status_code} - {response.text}"
        print("PASS: POST /api/auth/login returns 200")
    
    def test_auth_login_invalid_credentials(self):
        """POST /api/auth/login with invalid credentials should return 401."""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: POST /api/auth/login with invalid creds returns 401")
    
    def test_sectors_heatmap_returns_11_sectors(self):
        """GET /api/sectors/heatmap should return 11 sectors."""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        sectors = data.get("sectors", [])
        assert len(sectors) == 11, f"Expected 11 sectors, got {len(sectors)}"
        print(f"PASS: GET /api/sectors/heatmap returns {len(sectors)} sectors")
    
    def test_stocks_quote_aapl(self):
        """GET /api/stocks/quote/AAPL should return quote data."""
        response = requests.get(f"{BASE_URL}/api/stocks/quote/AAPL")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "symbol" in data or "price" in data or "currentPrice" in data, f"Missing expected fields: {data}"
        print("PASS: GET /api/stocks/quote/AAPL returns quote data")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
