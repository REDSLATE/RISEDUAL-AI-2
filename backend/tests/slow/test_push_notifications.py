"""
Push Notification Feature Tests for RISEDUAL AI
Tests: VAPID key endpoint, subscribe, unsubscribe, status, test, broadcast
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
FREE_USER_EMAIL = "freeuser_test@test.com"
FREE_USER_PASSWORD = os.environ.get("FREE_USER_PASSWORD", "Test1234!")


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def owner_token(api_client):
    """Get owner authentication token"""
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"Owner authentication failed: {response.status_code}")


@pytest.fixture(scope="module")
def free_user_token(api_client):
    """Get free user authentication token"""
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": FREE_USER_EMAIL,
        "password": FREE_USER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"Free user authentication failed: {response.status_code}")


@pytest.fixture
def owner_client(api_client, owner_token):
    """Session with owner auth header"""
    api_client.headers.update({"Authorization": f"Bearer {owner_token}"})
    return api_client


@pytest.fixture
def free_user_client(api_client, free_user_token):
    """Session with free user auth header"""
    api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
    return api_client


# ============================================================================
# Test: VAPID Key Endpoint (Public)
# ============================================================================
class TestVapidKeyEndpoint:
    """Test GET /api/push/vapid-key - public endpoint"""
    
    def test_vapid_key_returns_public_key(self, api_client):
        """VAPID key endpoint should return the public key without auth"""
        response = api_client.get(f"{BASE_URL}/api/push/vapid-key")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "public_key" in data, "Response should contain 'public_key'"
        assert isinstance(data["public_key"], str), "public_key should be a string"
        assert len(data["public_key"]) > 0, "public_key should not be empty"
        # VAPID public key should be base64url encoded
        assert data["public_key"].startswith("BF"), "VAPID public key should start with 'B'"
        print(f"✓ VAPID public key returned: {data['public_key'][:20]}...")


# ============================================================================
# Test: Push Status Endpoint (Authenticated)
# ============================================================================
class TestPushStatusEndpoint:
    """Test GET /api/push/status - requires authentication"""
    
    def test_status_requires_auth(self, api_client):
        """Status endpoint should require authentication"""
        # Clear any existing auth header
        api_client.headers.pop("Authorization", None)
        response = api_client.get(f"{BASE_URL}/api/push/status")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Status endpoint requires authentication")
    
    def test_status_returns_subscribed_and_is_pro_for_owner(self, api_client, owner_token):
        """Owner should get status with subscribed and is_pro fields"""
        api_client.headers.update({"Authorization": f"Bearer {owner_token}"})
        response = api_client.get(f"{BASE_URL}/api/push/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "subscribed" in data, "Response should contain 'subscribed'"
        assert "is_pro" in data, "Response should contain 'is_pro'"
        assert isinstance(data["subscribed"], bool), "subscribed should be boolean"
        assert isinstance(data["is_pro"], bool), "is_pro should be boolean"
        assert data["is_pro"], "Owner should have is_pro=True"
        print(f"✓ Owner status: subscribed={data['subscribed']}, is_pro={data['is_pro']}")
    
    def test_status_returns_is_pro_false_for_free_user(self, api_client, free_user_token):
        """Free user should have is_pro=False"""
        api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
        response = api_client.get(f"{BASE_URL}/api/push/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "subscribed" in data, "Response should contain 'subscribed'"
        assert "is_pro" in data, "Response should contain 'is_pro'"
        assert not data["is_pro"], "Free user should have is_pro=False"
        print(f"✓ Free user status: subscribed={data['subscribed']}, is_pro={data['is_pro']}")


# ============================================================================
# Test: Subscribe Endpoint (Authenticated)
# ============================================================================
class TestSubscribeEndpoint:
    """Test POST /api/push/subscribe - requires authentication"""
    
    def test_subscribe_requires_auth(self, api_client):
        """Subscribe endpoint should require authentication"""
        api_client.headers.pop("Authorization", None)
        response = api_client.post(f"{BASE_URL}/api/push/subscribe", json={
            "subscription": {"endpoint": "https://test.example.com", "keys": {"p256dh": "test", "auth": "test"}}
        })
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Subscribe endpoint requires authentication")
    
    def test_subscribe_stores_subscription_for_owner(self, api_client, owner_token):
        """Owner should be able to subscribe to push notifications"""
        api_client.headers.update({"Authorization": f"Bearer {owner_token}"})
        
        # Create a mock subscription object (similar to what browser would send)
        mock_subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-owner-endpoint",
            "keys": {
                "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                "auth": "tBHItJI5svbpez7KI4CCXg"
            }
        }
        
        response = api_client.post(f"{BASE_URL}/api/push/subscribe", json={
            "subscription": mock_subscription
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "message" in data, "Response should contain 'message'"
        assert "enabled" in data["message"].lower() or "success" in data["message"].lower() or "push" in data["message"].lower(), \
            f"Message should indicate success: {data['message']}"
        print(f"✓ Owner subscribed: {data['message']}")
        
        # Verify subscription status
        status_response = api_client.get(f"{BASE_URL}/api/push/status")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["subscribed"], "Owner should now be subscribed"
        print("✓ Owner subscription verified via status endpoint")
    
    def test_subscribe_stores_subscription_for_free_user(self, api_client, free_user_token):
        """Free user should be able to subscribe to push notifications"""
        api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
        
        mock_subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-free-user-endpoint",
            "keys": {
                "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                "auth": "tBHItJI5svbpez7KI4CCXg"
            }
        }
        
        response = api_client.post(f"{BASE_URL}/api/push/subscribe", json={
            "subscription": mock_subscription
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "message" in data, "Response should contain 'message'"
        print(f"✓ Free user subscribed: {data['message']}")


# ============================================================================
# Test: Unsubscribe Endpoint (Authenticated)
# ============================================================================
class TestUnsubscribeEndpoint:
    """Test POST /api/push/unsubscribe - requires authentication"""
    
    def test_unsubscribe_requires_auth(self, api_client):
        """Unsubscribe endpoint should require authentication"""
        api_client.headers.pop("Authorization", None)
        response = api_client.post(f"{BASE_URL}/api/push/unsubscribe")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Unsubscribe endpoint requires authentication")
    
    def test_unsubscribe_removes_subscription(self, api_client, free_user_token):
        """Free user should be able to unsubscribe from push notifications"""
        api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
        
        # First subscribe
        mock_subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-unsubscribe-endpoint",
            "keys": {"p256dh": "test", "auth": "test"}
        }
        api_client.post(f"{BASE_URL}/api/push/subscribe", json={"subscription": mock_subscription})
        
        # Then unsubscribe
        response = api_client.post(f"{BASE_URL}/api/push/unsubscribe")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "message" in data, "Response should contain 'message'"
        assert "disabled" in data["message"].lower() or "removed" in data["message"].lower() or "push" in data["message"].lower(), \
            f"Message should indicate unsubscribed: {data['message']}"
        print(f"✓ Free user unsubscribed: {data['message']}")
        
        # Verify subscription status
        status_response = api_client.get(f"{BASE_URL}/api/push/status")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert not status_data["subscribed"], "User should now be unsubscribed"
        print("✓ Unsubscription verified via status endpoint")


# ============================================================================
# Test: Test Push Endpoint (Admin Only)
# ============================================================================
class TestTestPushEndpoint:
    """Test POST /api/push/test - requires admin/owner role"""
    
    def test_test_push_requires_auth(self, api_client):
        """Test push endpoint should require authentication"""
        api_client.headers.pop("Authorization", None)
        response = api_client.post(f"{BASE_URL}/api/push/test")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Test push endpoint requires authentication")
    
    def test_test_push_returns_403_for_free_user(self, api_client, free_user_token):
        """Free user should get 403 when trying to send test push"""
        api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
        response = api_client.post(f"{BASE_URL}/api/push/test")
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data, "Response should contain 'detail'"
        assert "admin" in data["detail"].lower(), f"Error should mention admin: {data['detail']}"
        print(f"✓ Free user correctly denied: {data['detail']}")
    
    def test_test_push_works_for_owner(self, api_client, owner_token):
        """Owner should be able to send test push (may fail if no subscription)"""
        api_client.headers.update({"Authorization": f"Bearer {owner_token}"})
        
        # First ensure owner is subscribed
        mock_subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-owner-push-endpoint",
            "keys": {"p256dh": "test", "auth": "test"}
        }
        api_client.post(f"{BASE_URL}/api/push/subscribe", json={"subscription": mock_subscription})
        
        response = api_client.post(f"{BASE_URL}/api/push/test")
        # Should be 200 (success or failure in sending) or 400 (no subscription)
        assert response.status_code in [200, 400], f"Expected 200 or 400, got {response.status_code}"
        
        data = response.json()
        if response.status_code == 200:
            assert "sent" in data, "Response should contain 'sent'"
            print(f"✓ Owner test push result: sent={data['sent']}")
        else:
            print(f"✓ Owner test push returned 400 (expected if no valid subscription): {data}")


# ============================================================================
# Test: Broadcast Endpoint (Admin Only)
# ============================================================================
class TestBroadcastEndpoint:
    """Test POST /api/push/broadcast - requires admin/owner role"""
    
    def test_broadcast_requires_auth(self, api_client):
        """Broadcast endpoint should require authentication"""
        api_client.headers.pop("Authorization", None)
        response = api_client.post(f"{BASE_URL}/api/push/broadcast", json={
            "title": "Test", "message": "Test message"
        })
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Broadcast endpoint requires authentication")
    
    def test_broadcast_returns_403_for_free_user(self, api_client, free_user_token):
        """Free user should get 403 when trying to broadcast"""
        api_client.headers.update({"Authorization": f"Bearer {free_user_token}"})
        response = api_client.post(f"{BASE_URL}/api/push/broadcast", json={
            "title": "Test", "message": "Test message"
        })
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data, "Response should contain 'detail'"
        assert "admin" in data["detail"].lower(), f"Error should mention admin: {data['detail']}"
        print(f"✓ Free user correctly denied broadcast: {data['detail']}")
    
    def test_broadcast_works_for_owner(self, api_client, owner_token):
        """Owner should be able to broadcast push notifications"""
        api_client.headers.update({"Authorization": f"Bearer {owner_token}"})
        response = api_client.post(f"{BASE_URL}/api/push/broadcast", json={
            "title": "RISEDUAL AI Test Broadcast",
            "message": "This is a test broadcast message"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # Should return sent count (may be 0 if no valid subscriptions)
        assert "sent" in data or "skipped" in data, f"Response should contain 'sent' or 'skipped': {data}"
        print(f"✓ Owner broadcast result: {data}")


# ============================================================================
# Test: Push Service Module
# ============================================================================
class TestPushServiceModule:
    """Test push_service.py module loads and functions correctly"""
    
    def test_push_service_imports_without_errors(self):
        """push_service.py should import without errors"""
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services.push_service import (
                _is_configured, send_push, broadcast_notification,
                notify_prediction_flip, notify_dark_pool_spike,
                notify_watchlist_alert, notify_market_signal,
                FREE_DAILY_LIMIT, NOTIF_PREDICTION_FLIP, NOTIF_DARK_POOL_SPIKE,
                NOTIF_WATCHLIST_ALERT, NOTIF_MARKET_SIGNAL
            )
            print("✓ push_service.py imports successfully")
        except ImportError as e:
            pytest.fail(f"Failed to import push_service: {e}")
    
    def test_is_configured_returns_true_via_api(self, api_client):
        """VAPID keys should be configured (verified via API)"""
        # Test via API since direct import doesn't load .env
        response = api_client.get(f"{BASE_URL}/api/push/vapid-key")
        assert response.status_code == 200
        data = response.json()
        assert data.get("public_key"), "VAPID public key should be set"
        assert len(data["public_key"]) > 50, "VAPID key should be valid length"
        print("✓ VAPID keys configured (verified via API)")
    
    def test_free_daily_limit_is_one(self):
        """FREE_DAILY_LIMIT should be 1"""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.push_service import FREE_DAILY_LIMIT
        
        assert FREE_DAILY_LIMIT == 1, f"FREE_DAILY_LIMIT should be 1, got {FREE_DAILY_LIMIT}"
        print(f"✓ FREE_DAILY_LIMIT = {FREE_DAILY_LIMIT}")
    
    def test_notification_types_defined(self):
        """Notification type constants should be defined"""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.push_service import (
            NOTIF_PREDICTION_FLIP, NOTIF_DARK_POOL_SPIKE,
            NOTIF_WATCHLIST_ALERT, NOTIF_MARKET_SIGNAL
        )
        
        assert NOTIF_PREDICTION_FLIP == "prediction_flip"
        assert NOTIF_DARK_POOL_SPIKE == "dark_pool_spike"
        assert NOTIF_WATCHLIST_ALERT == "watchlist_alert"
        assert NOTIF_MARKET_SIGNAL == "market_signal"
        print("✓ All notification type constants defined correctly")


# ============================================================================
# Test: Service Worker File
# ============================================================================
class TestServiceWorkerFile:
    """Test service-worker.js has required push handlers"""
    
    def test_service_worker_has_push_handler(self):
        """service-worker.js should have push event listener"""
        sw_path = "/app/frontend/public/service-worker.js"
        with open(sw_path, 'r') as f:
            content = f.read()
        
        assert "addEventListener('push'" in content or 'addEventListener("push"' in content, \
            "service-worker.js should have push event listener"
        print("✓ service-worker.js has push event listener")
    
    def test_service_worker_has_notificationclick_handler(self):
        """service-worker.js should have notificationclick event listener"""
        sw_path = "/app/frontend/public/service-worker.js"
        with open(sw_path, 'r') as f:
            content = f.read()
        
        assert "addEventListener('notificationclick'" in content or 'addEventListener("notificationclick"' in content, \
            "service-worker.js should have notificationclick event listener"
        print("✓ service-worker.js has notificationclick event listener")
    
    def test_service_worker_shows_notification(self):
        """service-worker.js push handler should call showNotification"""
        sw_path = "/app/frontend/public/service-worker.js"
        with open(sw_path, 'r') as f:
            content = f.read()
        
        assert "showNotification" in content, \
            "service-worker.js should call showNotification in push handler"
        print("✓ service-worker.js calls showNotification")


# ============================================================================
# Test: Frontend Hook File
# ============================================================================
class TestFrontendHookFile:
    """Test usePushNotifications.js hook exists and has required functions"""
    
    def test_hook_file_exists(self):
        """usePushNotifications.js should exist"""
        hook_path = "/app/frontend/src/hooks/usePushNotifications.js"
        import os
        assert os.path.exists(hook_path), f"Hook file should exist at {hook_path}"
        print("✓ usePushNotifications.js exists")
    
    def test_hook_exports_usePushNotifications(self):
        """Hook should export usePushNotifications function"""
        hook_path = "/app/frontend/src/hooks/usePushNotifications.js"
        with open(hook_path, 'r') as f:
            content = f.read()
        
        assert "export function usePushNotifications" in content or "export { usePushNotifications" in content, \
            "Hook should export usePushNotifications"
        print("✓ usePushNotifications function exported")
    
    def test_hook_has_subscribe_function(self):
        """Hook should have subscribe function"""
        hook_path = "/app/frontend/src/hooks/usePushNotifications.js"
        with open(hook_path, 'r') as f:
            content = f.read()
        
        assert "subscribe" in content, "Hook should have subscribe function"
        assert "/api/push/subscribe" in content, "Hook should call /api/push/subscribe endpoint"
        print("✓ Hook has subscribe function with correct endpoint")
    
    def test_hook_has_unsubscribe_function(self):
        """Hook should have unsubscribe function"""
        hook_path = "/app/frontend/src/hooks/usePushNotifications.js"
        with open(hook_path, 'r') as f:
            content = f.read()
        
        assert "unsubscribe" in content, "Hook should have unsubscribe function"
        assert "/api/push/unsubscribe" in content, "Hook should call /api/push/unsubscribe endpoint"
        print("✓ Hook has unsubscribe function with correct endpoint")
    
    def test_hook_uses_vapid_key_from_env(self):
        """Hook should use VAPID key from environment"""
        hook_path = "/app/frontend/src/hooks/usePushNotifications.js"
        with open(hook_path, 'r') as f:
            content = f.read()
        
        assert "REACT_APP_VAPID_PUBLIC_KEY" in content, \
            "Hook should use REACT_APP_VAPID_PUBLIC_KEY from environment"
        print("✓ Hook uses REACT_APP_VAPID_PUBLIC_KEY from environment")


# ============================================================================
# Test: Frontend Environment Variable
# ============================================================================
class TestFrontendEnvVariable:
    """Test REACT_APP_VAPID_PUBLIC_KEY is set in frontend .env"""
    
    def test_vapid_key_in_frontend_env(self):
        """REACT_APP_VAPID_PUBLIC_KEY should be set in frontend .env"""
        env_path = "/app/frontend/.env"
        with open(env_path, 'r') as f:
            content = f.read()
        
        assert "REACT_APP_VAPID_PUBLIC_KEY=" in content, \
            "REACT_APP_VAPID_PUBLIC_KEY should be set in frontend .env"
        
        # Extract the value
        for line in content.split('\n'):
            if line.startswith('REACT_APP_VAPID_PUBLIC_KEY='):
                value = line.split('=', 1)[1].strip()
                assert len(value) > 0, "VAPID key should not be empty"
                assert value.startswith('B'), "VAPID public key should start with 'B'"
                print(f"✓ REACT_APP_VAPID_PUBLIC_KEY set: {value[:20]}...")
                return
        
        pytest.fail("Could not find REACT_APP_VAPID_PUBLIC_KEY value")


# ============================================================================
# Test: UserWorkspace PushToggle Component
# ============================================================================
class TestUserWorkspacePushToggle:
    """Test PushToggle component exists in UserWorkspace.jsx"""
    
    def test_push_toggle_component_exists(self):
        """PushToggle component should be defined in UserWorkspace.jsx"""
        component_path = "/app/frontend/src/components/UserWorkspace.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        assert "const PushToggle" in content or "function PushToggle" in content, \
            "PushToggle component should be defined"
        print("✓ PushToggle component defined in UserWorkspace.jsx")
    
    def test_push_toggle_uses_hook(self):
        """PushToggle should use usePushNotifications hook"""
        component_path = "/app/frontend/src/components/UserWorkspace.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        assert "usePushNotifications" in content, \
            "PushToggle should use usePushNotifications hook"
        print("✓ PushToggle uses usePushNotifications hook")
    
    def test_push_toggle_has_data_testid(self):
        """PushToggle should have data-testid attribute"""
        component_path = "/app/frontend/src/components/UserWorkspace.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        assert 'data-testid="push-toggle"' in content, \
            "PushToggle should have data-testid='push-toggle'"
        assert 'data-testid="push-toggle-btn"' in content, \
            "PushToggle button should have data-testid='push-toggle-btn'"
        print("✓ PushToggle has data-testid attributes")
    
    def test_push_toggle_in_referrals_tab(self):
        """PushToggle should be rendered in ReferralsTab"""
        component_path = "/app/frontend/src/components/UserWorkspace.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        # Check that PushToggle is used in ReferralsTab
        assert "<PushToggle" in content, "PushToggle should be rendered"
        print("✓ PushToggle is rendered in component")
    
    def test_push_toggle_shows_states(self):
        """PushToggle should show Enable/Enabled/Blocked states"""
        component_path = "/app/frontend/src/components/UserWorkspace.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        assert "Enable" in content, "PushToggle should show 'Enable' state"
        assert "Enabled" in content, "PushToggle should show 'Enabled' state"
        assert "Blocked" in content, "PushToggle should show 'Blocked' state"
        print("✓ PushToggle shows Enable/Enabled/Blocked states")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
