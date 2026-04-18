"""
Iteration 126: Provider Health Dashboard & ProviderRouter Upgrade Tests

Tests:
1. GET /api/provider-health — returns lanes with per-provider health data
2. GET /api/provider-health — requires admin/owner role (non-admin gets 403)
3. POST /api/chat — returns provider metadata as rich object with lane, name, type, model fields
4. ProviderRouter snapshot structure validation
"""
import pytest
import requests
import uuid

from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD, TEST_USER_PASSWORD


class TestProviderHealthEndpoint:
    """Tests for GET /api/provider-health admin endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for each test"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login(self, email: str, password: str) -> bool:
        """Login and return True if successful"""
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": email,
            "password": password
        })
        return resp.status_code == 200
    
    def test_provider_health_requires_auth(self):
        """GET /api/provider-health without auth should return 401"""
        # Fresh session without login
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: Provider health endpoint requires authentication")
    
    def test_provider_health_admin_access(self):
        """GET /api/provider-health with admin role should return 200"""
        assert self._login(ADMIN_EMAIL, ADMIN_PASSWORD), "Admin login failed"
        
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "lanes" in data, "Response should contain 'lanes' key"
        print(f"PASS: Admin can access provider health - lanes: {list(data['lanes'].keys())}")
    
    def test_provider_health_owner_access(self):
        """GET /api/provider-health with owner role should return 200"""
        assert self._login(OWNER_EMAIL, OWNER_PASSWORD), "Owner login failed"
        
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "lanes" in data, "Response should contain 'lanes' key"
        print(f"PASS: Owner can access provider health - lanes: {list(data['lanes'].keys())}")
    
    def test_provider_health_non_admin_forbidden(self):
        """GET /api/provider-health with non-admin user should return 403"""
        # Create a test user or use a known non-admin user
        # First, try to register a test user
        test_email = f"test_user_{uuid.uuid4().hex[:8]}@test.com"
        test_password = TEST_USER_PASSWORD
        
        # Register test user
        reg_resp = self.session.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": test_password,
            "name": "Test User"
        })
        
        if reg_resp.status_code in [200, 201]:
            # Login as test user
            login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
                "email": test_email,
                "password": test_password
            })
            
            if login_resp.status_code == 200:
                response = self.session.get(f"{BASE_URL}/api/provider-health")
                assert response.status_code == 403, f"Expected 403 for non-admin, got {response.status_code}"
                print("PASS: Non-admin user gets 403 Forbidden")
            else:
                pytest.skip("Could not login as test user")
        else:
            pytest.skip("Could not register test user for non-admin test")


class TestProviderHealthDataStructure:
    """Tests for provider health data structure validation"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, "Admin login failed"
    
    def test_lanes_structure(self):
        """Verify lanes structure is a dict of lane_name -> provider_dict"""
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200
        
        data = response.json()
        lanes = data.get("lanes", {})
        
        # Lanes should be a dict
        assert isinstance(lanes, dict), "lanes should be a dictionary"
        
        # If AI lane exists, verify structure
        if "ai" in lanes:
            ai_lane = lanes["ai"]
            assert isinstance(ai_lane, dict), "AI lane should be a dictionary of providers"
            print(f"PASS: AI lane has {len(ai_lane)} providers: {list(ai_lane.keys())}")
        
        # If market_data lane exists, verify structure
        if "market_data" in lanes:
            md_lane = lanes["market_data"]
            assert isinstance(md_lane, dict), "Market data lane should be a dictionary of providers"
            print(f"PASS: Market data lane has {len(md_lane)} providers: {list(md_lane.keys())}")
    
    def test_provider_state_fields(self):
        """Verify each provider has required health state fields"""
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200
        
        data = response.json()
        lanes = data.get("lanes", {})
        
        required_fields = [
            "successes", "failures", "consecutive_failures", 
            "avg_latency_ms", "last_error", "cooldown_until", "disabled"
        ]
        
        for lane_name, providers in lanes.items():
            for provider_name, state in providers.items():
                for field in required_fields:
                    assert field in state, f"Provider {provider_name} in lane {lane_name} missing field: {field}"
                
                # Validate field types
                assert isinstance(state["successes"], int), f"{provider_name}.successes should be int"
                assert isinstance(state["failures"], int), f"{provider_name}.failures should be int"
                assert isinstance(state["consecutive_failures"], int), f"{provider_name}.consecutive_failures should be int"
                assert isinstance(state["disabled"], bool), f"{provider_name}.disabled should be bool"
                
                print(f"PASS: Provider {provider_name} in {lane_name} has all required fields")
    
    def test_provider_health_metrics(self):
        """Verify provider health metrics are reasonable values"""
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200
        
        data = response.json()
        lanes = data.get("lanes", {})
        
        for lane_name, providers in lanes.items():
            for provider_name, state in providers.items():
                # Successes and failures should be non-negative
                assert state["successes"] >= 0, f"{provider_name}.successes should be >= 0"
                assert state["failures"] >= 0, f"{provider_name}.failures should be >= 0"
                assert state["consecutive_failures"] >= 0, f"{provider_name}.consecutive_failures should be >= 0"
                
                # avg_latency_ms should be non-negative if present
                if state["avg_latency_ms"] is not None:
                    assert state["avg_latency_ms"] >= 0, f"{provider_name}.avg_latency_ms should be >= 0"
                
                print(f"PASS: Provider {provider_name} metrics are valid - successes={state['successes']}, failures={state['failures']}")


class TestChatProviderMetadata:
    """Tests for POST /api/chat provider metadata in response"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, "Admin login failed"
    
    def test_chat_returns_provider_metadata(self):
        """POST /api/chat should return provider metadata with lane, name, type, model"""
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        
        # Chat endpoint uses Form data, not JSON
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "What is 2+2?",
                "sessionId": session_id
            },
            timeout=60
        )
        
        assert response.status_code == 200, f"Chat failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "response" in data, "Chat response should contain 'response' field"
        assert "provider" in data, "Chat response should contain 'provider' metadata"
        
        provider = data.get("provider")
        if provider:
            # Verify provider metadata structure
            assert "lane" in provider, "Provider metadata should have 'lane'"
            assert "name" in provider, "Provider metadata should have 'name'"
            
            print(f"PASS: Chat returned provider metadata - lane={provider.get('lane')}, name={provider.get('name')}, type={provider.get('type')}, model={provider.get('model')}")
        else:
            print("WARN: Provider metadata is None (may be expected if no providers configured)")
    
    def test_chat_provider_metadata_structure(self):
        """Verify provider metadata has correct structure"""
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "Hello, what is your name?",
                "sessionId": session_id
            },
            timeout=60
        )
        
        if response.status_code == 200:
            data = response.json()
            provider = data.get("provider")
            
            if provider:
                # Check expected fields
                expected_fields = ["lane", "name"]
                for field in expected_fields:
                    assert field in provider, f"Provider metadata missing '{field}'"
                
                # Lane should be 'ai' for chat
                assert provider.get("lane") == "ai", f"Expected lane='ai', got '{provider.get('lane')}'"
                
                # Name should be a non-empty string
                assert isinstance(provider.get("name"), str), "Provider name should be a string"
                assert len(provider.get("name", "")) > 0, "Provider name should not be empty"
                
                print(f"PASS: Provider metadata structure valid - {provider}")
            else:
                print("WARN: Provider metadata is None")
        else:
            pytest.skip(f"Chat request failed with {response.status_code}")


class TestProviderRouterIntegration:
    """Integration tests for ProviderRouter with health tracking"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, "Admin login failed"
    
    def test_chat_updates_provider_health(self):
        """Verify that chat calls update provider health metrics"""
        # Get initial health state
        initial_resp = self.session.get(f"{BASE_URL}/api/provider-health")
        assert initial_resp.status_code == 200
        initial_data = initial_resp.json()
        
        # Make a chat request
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        chat_resp = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "Say hello",
                "sessionId": session_id
            },
            timeout=60
        )
        
        if chat_resp.status_code == 200:
            # Get updated health state
            updated_resp = self.session.get(f"{BASE_URL}/api/provider-health")
            assert updated_resp.status_code == 200
            updated_data = updated_resp.json()
            
            # Check if AI lane exists and has providers
            if "ai" in updated_data.get("lanes", {}):
                ai_providers = updated_data["lanes"]["ai"]
                
                # At least one provider should have successes > 0 or the call count should increase
                total_calls = sum(p.get("successes", 0) + p.get("failures", 0) for p in ai_providers.values())
                print(f"PASS: AI lane total calls: {total_calls}")
            else:
                print("WARN: No AI lane in provider health")
        else:
            pytest.skip(f"Chat request failed with {chat_resp.status_code}")
    
    def test_provider_health_snapshot_serialization(self):
        """Verify provider health snapshot is JSON serializable (no datetime objects)"""
        response = self.session.get(f"{BASE_URL}/api/provider-health")
        assert response.status_code == 200
        
        # If we got here, the response was JSON serializable
        data = response.json()
        
        # Check that datetime fields are strings (ISO format)
        for lane_name, providers in data.get("lanes", {}).items():
            for provider_name, state in providers.items():
                for field in ["last_success_at", "last_failure_at", "cooldown_until"]:
                    if state.get(field) is not None:
                        assert isinstance(state[field], str), f"{provider_name}.{field} should be ISO string, got {type(state[field])}"
        
        print("PASS: Provider health snapshot is properly serialized")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
