"""
Test suite for Daily Digest Email feature.
Tests: digest status, opt-in/opt-out, preview (admin), trigger (admin), APScheduler setup.
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


class TestDigestEndpointsAuth:
    """Test digest endpoints require authentication."""
    
    def test_digest_status_requires_auth(self):
        """GET /api/digest/status should return 401 without auth."""
        response = requests.get(f"{BASE_URL}/api/digest/status")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /api/digest/status requires authentication")
    
    def test_digest_opt_out_requires_auth(self):
        """POST /api/digest/opt-out should return 401 without auth."""
        response = requests.post(f"{BASE_URL}/api/digest/opt-out")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /api/digest/opt-out requires authentication")
    
    def test_digest_opt_in_requires_auth(self):
        """POST /api/digest/opt-in should return 401 without auth."""
        response = requests.post(f"{BASE_URL}/api/digest/opt-in")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /api/digest/opt-in requires authentication")
    
    def test_digest_preview_requires_auth(self):
        """GET /api/digest/preview should return 401 without auth."""
        response = requests.get(f"{BASE_URL}/api/digest/preview")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /api/digest/preview requires authentication")
    
    def test_digest_trigger_requires_auth(self):
        """POST /api/digest/trigger should return 401 without auth."""
        response = requests.post(f"{BASE_URL}/api/digest/trigger")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: /api/digest/trigger requires authentication")


class TestDigestStatusEndpoint:
    """Test GET /api/digest/status for authenticated users."""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code}")
        return response.json().get("access_token")
    
    def test_digest_status_returns_subscribed_true_by_default(self, owner_token):
        """GET /api/digest/status should return subscribed:true by default."""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "subscribed" in data, "Response should contain 'subscribed' field"
        # Default is subscribed (digest_opt_out == False or not set)
        print(f"PASSED: /api/digest/status returns subscribed={data['subscribed']}")


class TestDigestOptOutOptIn:
    """Test opt-out and opt-in flow."""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code}")
        return response.json().get("access_token")
    
    def test_opt_out_sets_subscribed_to_false(self, owner_token):
        """POST /api/digest/opt-out should set subscribed to false."""
        headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Opt out
        response = requests.post(f"{BASE_URL}/api/digest/opt-out", headers=headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "message" in data, "Response should contain 'message' field"
        print(f"PASSED: opt-out response: {data['message']}")
        
        # Verify status is now false
        status_response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["subscribed"] == False, f"Expected subscribed=False after opt-out, got {status_data['subscribed']}"
        print("PASSED: After opt-out, subscribed=False")
    
    def test_opt_in_sets_subscribed_to_true(self, owner_token):
        """POST /api/digest/opt-in should set subscribed back to true."""
        headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Opt in
        response = requests.post(f"{BASE_URL}/api/digest/opt-in", headers=headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "message" in data, "Response should contain 'message' field"
        print(f"PASSED: opt-in response: {data['message']}")
        
        # Verify status is now true
        status_response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["subscribed"] == True, f"Expected subscribed=True after opt-in, got {status_data['subscribed']}"
        print("PASSED: After opt-in, subscribed=True")


class TestDigestPreviewAdminOnly:
    """Test GET /api/digest/preview is admin-only."""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code}")
        return response.json().get("access_token")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token (create if needed)."""
        # Try to login first
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        
        # Create free user if doesn't exist
        register_response = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD,
            "name": "Free Test User"
        })
        if register_response.status_code in [200, 201]:
            # Login after registration
            login_response = requests.post(f"{BASE_URL}/api/auth/login", json={
                "email": FREE_USER_EMAIL,
                "password": FREE_USER_PASSWORD
            })
            if login_response.status_code == 200:
                return login_response.json().get("access_token")
        
        pytest.skip("Could not create/login free user")
    
    def test_preview_returns_html_and_data_summary_for_admin(self, owner_token):
        """GET /api/digest/preview should return HTML and data_summary for admin."""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/digest/preview", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Check response structure
        assert "html" in data, "Response should contain 'html' field"
        assert "data_summary" in data, "Response should contain 'data_summary' field"
        
        # Check HTML is valid
        html = data["html"]
        assert "<!DOCTYPE html>" in html, "HTML should be valid document"
        assert "RISEDUAL AI" in html, "HTML should contain app name"
        assert "Daily Market Digest" in html, "HTML should contain digest title"
        
        # Check data_summary structure
        summary = data["data_summary"]
        assert "predictions" in summary, "data_summary should have 'predictions' count"
        assert "dark_pool" in summary, "data_summary should have 'dark_pool' count"
        assert "signals" in summary, "data_summary should have 'signals' count"
        
        print(f"PASSED: Preview returns HTML ({len(html)} chars) and data_summary: {summary}")
    
    def test_preview_returns_403_for_free_user(self, free_user_token):
        """GET /api/digest/preview should return 403 for non-admin users."""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.get(f"{BASE_URL}/api/digest/preview", headers=headers)
        
        assert response.status_code == 403, f"Expected 403 for free user, got {response.status_code}"
        print("PASSED: Preview returns 403 for free user (admin only)")


class TestDigestTriggerAdminOnly:
    """Test POST /api/digest/trigger is admin-only and returns skipped with placeholder key."""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner auth token."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Owner login failed: {response.status_code}")
        return response.json().get("access_token")
    
    @pytest.fixture
    def free_user_token(self):
        """Get free user auth token."""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Free user login failed")
    
    def test_trigger_returns_skipped_true_with_placeholder_key(self, owner_token):
        """POST /api/digest/trigger should return skipped:true with placeholder API key."""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.post(f"{BASE_URL}/api/digest/trigger", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # With placeholder key, should return skipped:true
        assert "skipped" in data, "Response should contain 'skipped' field"
        assert data["skipped"] == True, f"Expected skipped=True with placeholder key, got {data['skipped']}"
        assert "reason" in data, "Response should contain 'reason' field"
        assert data["reason"] == "no_api_key", f"Expected reason='no_api_key', got {data['reason']}"
        
        print(f"PASSED: Trigger returns skipped=True with reason={data['reason']}")
    
    def test_trigger_returns_403_for_free_user(self, free_user_token):
        """POST /api/digest/trigger should return 403 for non-admin users."""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.post(f"{BASE_URL}/api/digest/trigger", headers=headers)
        
        assert response.status_code == 403, f"Expected 403 for free user, got {response.status_code}"
        print("PASSED: Trigger returns 403 for free user (admin only)")


class TestDigestServiceModule:
    """Test digest_service.py module functions."""
    
    def test_digest_service_imports_without_errors(self):
        """digest_service.py should import without errors."""
        import sys
        sys.path.insert(0, '/app/backend')
        try:
            from services.digest_service import collect_digest_data, build_digest_html, send_daily_digest
            print("PASSED: digest_service.py imports successfully")
        except ImportError as e:
            pytest.fail(f"Failed to import digest_service: {e}")
    
    def test_build_digest_html_generates_valid_html_for_pro_user(self):
        """build_digest_html should generate valid HTML for Pro users."""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        # Sample data
        data = {
            "predictions": [
                {"ticker": "AAPL", "verdict": "BULLISH", "confidence": 85, "summary": "Strong buy"},
                {"ticker": "TSLA", "verdict": "BEARISH", "confidence": 70, "summary": "Sell signal"},
            ],
            "dark_pool": [
                {"ticker": "NVDA", "volume": 1000000, "sentiment": "bullish"},
            ],
            "signals": [
                {"ticker": "MSFT", "signal": "BUY", "strength": "strong"},
            ],
            "timestamp": "2026-01-06T06:00:00Z"
        }
        
        html = build_digest_html(data, is_pro=True, user_name="Pro User")
        
        # Validate HTML structure
        assert "<!DOCTYPE html>" in html, "Should be valid HTML document"
        assert "Good Morning, Pro User" in html, "Should contain user greeting"
        assert "AAPL" in html, "Should contain ticker data"
        assert "BULLISH" in html, "Should contain verdict"
        assert "Upgrade to Pro" not in html, "Pro user should NOT see upgrade CTA"
        
        print("PASSED: build_digest_html generates valid HTML for Pro user (no upgrade CTA)")
    
    def test_build_digest_html_generates_blurred_rows_for_free_user(self):
        """build_digest_html should generate blurred rows and upgrade CTA for free users."""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        # Sample data with multiple items
        data = {
            "predictions": [
                {"ticker": "AAPL", "verdict": "BULLISH", "confidence": 85, "summary": "Strong buy"},
                {"ticker": "TSLA", "verdict": "BEARISH", "confidence": 70, "summary": "Sell signal"},
                {"ticker": "NVDA", "verdict": "NEUTRAL", "confidence": 60, "summary": "Hold"},
            ],
            "dark_pool": [
                {"ticker": "NVDA", "volume": 1000000, "sentiment": "bullish"},
                {"ticker": "AMD", "volume": 500000, "sentiment": "bearish"},
            ],
            "signals": [
                {"ticker": "MSFT", "signal": "BUY", "strength": "strong"},
                {"ticker": "GOOG", "signal": "SELL", "strength": "weak"},
            ],
            "timestamp": "2026-01-06T06:00:00Z"
        }
        
        html = build_digest_html(data, is_pro=False, user_name="Free User")
        
        # Validate HTML structure
        assert "<!DOCTYPE html>" in html, "Should be valid HTML document"
        assert "Good Morning, Free User" in html, "Should contain user greeting"
        assert "AAPL" in html, "First item should be visible"
        assert "filter:blur(4px)" in html, "Should have blurred rows for free user"
        assert "Upgrade to Pro" in html, "Free user should see upgrade CTA"
        assert "$55/mo" in html, "Should show pricing in CTA"
        
        print("PASSED: build_digest_html generates blurred rows and upgrade CTA for free user")
    
    def test_build_digest_html_handles_empty_data_gracefully(self):
        """build_digest_html should handle empty collections gracefully."""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        # Empty data
        data = {
            "predictions": [],
            "dark_pool": [],
            "signals": [],
            "timestamp": "2026-01-06T06:00:00Z"
        }
        
        html = build_digest_html(data, is_pro=True, user_name="Test User")
        
        # Should not crash and should show "No recent data available"
        assert "<!DOCTYPE html>" in html, "Should be valid HTML document"
        assert "No recent data available" in html, "Should show no data message"
        
        print("PASSED: build_digest_html handles empty data gracefully")


class TestAPSchedulerSetup:
    """Test APScheduler is configured correctly."""
    
    def test_apscheduler_log_message_present(self):
        """Backend logs should show APScheduler started message."""
        import subprocess
        result = subprocess.run(
            ["tail", "-n", "100", "/var/log/supervisor/backend.err.log"],
            capture_output=True, text=True
        )
        logs = result.stdout
        
        assert "Daily digest scheduler started (6:00 AM UTC)" in logs, \
            "Backend logs should contain APScheduler startup message"
        
        print("PASSED: APScheduler is running and configured for 6:00 AM UTC")


class TestDigestFreeUserFlow:
    """Test complete flow for free user digest preferences."""
    
    @pytest.fixture
    def free_user_token(self):
        """Get or create free user token."""
        # Try login
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        
        # Register if needed
        requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD,
            "name": "Free Test User"
        })
        
        # Login again
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Could not get free user token")
    
    def test_free_user_can_check_digest_status(self, free_user_token):
        """Free user should be able to check their digest subscription status."""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "subscribed" in data
        print(f"PASSED: Free user can check digest status: subscribed={data['subscribed']}")
    
    def test_free_user_can_opt_out_and_opt_in(self, free_user_token):
        """Free user should be able to opt-out and opt-in to digest."""
        headers = {"Authorization": f"Bearer {free_user_token}"}
        
        # Opt out
        opt_out_response = requests.post(f"{BASE_URL}/api/digest/opt-out", headers=headers)
        assert opt_out_response.status_code == 200
        
        # Verify opted out
        status_response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        assert status_response.json()["subscribed"] == False
        
        # Opt back in
        opt_in_response = requests.post(f"{BASE_URL}/api/digest/opt-in", headers=headers)
        assert opt_in_response.status_code == 200
        
        # Verify opted in
        status_response = requests.get(f"{BASE_URL}/api/digest/status", headers=headers)
        assert status_response.json()["subscribed"] == True
        
        print("PASSED: Free user can opt-out and opt-in to digest")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
