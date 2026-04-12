"""
Iteration 94: Code Quality Refactoring Regression Tests
Tests whale_radar.py refactoring (3 helper functions) and RiseDualGPTChat.jsx component extraction.

Refactored files:
- whale_radar.py: _build_whale_event, _build_tick_event, _process_snapshot helpers
- RiseDualGPTChat.jsx: Reduced 424→343 lines, uses ChatHeader and MemoryPanel components
"""

import pytest
import requests
import os
import json
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from conftest_creds.py
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestWhaleRadarRefactoring:
    """Test whale_radar.py refactored SSE endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_whale_radar_sse_endpoint_exists(self):
        """Test that /api/stream/whale-radar SSE endpoint is accessible"""
        # SSE endpoints return streaming response, so we just check it doesn't 404
        response = self.session.get(
            f"{BASE_URL}/api/stream/whale-radar",
            stream=True,
            timeout=5
        )
        # SSE should return 200 with text/event-stream content type
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert "text/event-stream" in response.headers.get("content-type", ""), \
            f"Expected text/event-stream, got {response.headers.get('content-type')}"
        response.close()
    
    def test_whale_radar_returns_radar_status_event(self):
        """Test that whale radar SSE returns radar_status event first"""
        response = self.session.get(
            f"{BASE_URL}/api/stream/whale-radar",
            stream=True,
            timeout=10
        )
        assert response.status_code == 200
        
        # Read first few events
        events_received = []
        try:
            for i, line in enumerate(response.iter_lines(decode_unicode=True)):
                if line:
                    events_received.append(line)
                if i > 10 or len(events_received) > 5:  # Limit iterations
                    break
        except Exception as e:
            pass  # Timeout is expected
        finally:
            response.close()
        
        # Check that we received some events
        assert len(events_received) > 0, "No events received from whale radar"
        
        # First event should be radar_status
        event_data = "".join(events_received)
        assert "radar_status" in event_data, f"Expected radar_status event, got: {event_data[:200]}"
    
    def test_whale_radar_tickers_list(self):
        """Test that radar_status contains expected tickers"""
        response = self.session.get(
            f"{BASE_URL}/api/stream/whale-radar",
            stream=True,
            timeout=10
        )
        assert response.status_code == 200
        
        radar_data = None
        try:
            for line in response.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        data = json.loads(data_str)
                        if data.get("type") == "radar_status":
                            radar_data = data
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        finally:
            response.close()
        
        assert radar_data is not None, "No radar_status event found"
        assert "tickers" in radar_data, "radar_status missing tickers field"
        
        expected_tickers = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK", "SHIB"]
        assert radar_data["tickers"] == expected_tickers, \
            f"Expected {expected_tickers}, got {radar_data['tickers']}"


class TestChatEndpointRegression:
    """Test chat endpoints after ai.py refactoring"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        
        data = login_response.json()
        self.access_token = data.get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
    
    def test_chat_endpoint_works(self):
        """Test POST /api/chat returns valid response"""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={"message": "What is Bitcoin?", "sessionId": f"test_{int(time.time())}"},
            headers={"Content-Type": None}  # Let requests set multipart
        )
        # Remove Content-Type for multipart
        del self.session.headers["Content-Type"]
        
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={"message": "What is Bitcoin?", "sessionId": f"test_{int(time.time())}"}
        )
        
        assert response.status_code == 200, f"Chat failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "response" in data or "message" in data, f"Missing response field: {data}"
    
    def test_chat_limit_endpoint(self):
        """Test GET /api/chat/limit returns rate limit info"""
        response = self.session.get(f"{BASE_URL}/api/chat/limit")
        assert response.status_code == 200, f"Chat limit failed: {response.text}"
        
        data = response.json()
        assert "limit" in data, "Missing limit field"
        assert "used" in data, "Missing used field"
        assert "remaining" in data, "Missing remaining field"
        assert "is_pro" in data, "Missing is_pro field"


class TestChatMemoryEndpoints:
    """Test chat memory endpoints for pro users"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session as admin (pro user)"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin (pro user)
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        
        data = login_response.json()
        self.access_token = data.get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
    
    def test_get_memory_endpoint(self):
        """Test GET /api/chat/memory returns memories for pro user"""
        response = self.session.get(f"{BASE_URL}/api/chat/memory")
        assert response.status_code == 200, f"Get memory failed: {response.text}"
        
        data = response.json()
        assert "memories" in data, "Missing memories field"
        assert "enabled" in data, "Missing enabled field"
        assert isinstance(data["memories"], list), "memories should be a list"
    
    def test_pin_memory_endpoint(self):
        """Test POST /api/chat/memory/pin works"""
        response = self.session.post(
            f"{BASE_URL}/api/chat/memory/pin",
            json={"content": f"TEST_MEMORY_{int(time.time())}: User prefers technical analysis"}
        )
        # 200 or 201 for success, 400 if limit reached
        assert response.status_code in [200, 201, 400], f"Pin memory failed: {response.text}"
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert "memory_id" in data or "message" in data, f"Unexpected response: {data}"


class TestAuthEndpointRegression:
    """Test auth endpoints still work after refactoring"""
    
    def test_login_endpoint(self):
        """Test POST /api/auth/login works"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        
        data = response.json()
        assert "access_token" in data, "Missing access_token"
        assert "email" in data, "Missing email"
        assert data["email"] == ADMIN_EMAIL, f"Email mismatch: {data['email']}"
    
    def test_login_invalid_credentials(self):
        """Test login with invalid credentials returns 401"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "invalid@test.com", "password": "wrongpassword"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestHealthEndpoint:
    """Basic health check"""
    
    def test_root_endpoint(self):
        """Test root endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/")
        # Root endpoint should return 200 or redirect
        assert response.status_code in [200, 307, 308], f"Root check failed: {response.text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
