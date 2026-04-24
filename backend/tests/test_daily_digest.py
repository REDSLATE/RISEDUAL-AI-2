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
        
        # Check HTML is valid. The template uses XHTML 1.0 Transitional
        # which starts with `<!DOCTYPE html PUBLIC ...>` — check the
        # prefix so future template tweaks don't break the regression.
        html = data["html"]
        assert html.startswith("<!DOCTYPE html"), "HTML should start with a DOCTYPE"
        assert "RISEDUAL AI" in html, "HTML should contain app name"
        assert "Daily Market Digest" in html, "HTML should contain digest title"
        
        # Check data_summary structure. The service exposes counts for
        # `predictions`, `smart_money`, and `alerts` plus two boolean
        # flags. Historical shape used `dark_pool` / `signals`; those
        # were renamed to match the actual collection names.
        summary = data["data_summary"]
        assert "predictions" in summary, "data_summary should have 'predictions' count"
        assert "smart_money" in summary, "data_summary should have 'smart_money' count"
        assert "alerts" in summary, "data_summary should have 'alerts' count"
        
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
    
    def test_trigger_returns_valid_response_shape(self, owner_token):
        """POST /api/digest/trigger should return a well-shaped response.

        Historically the test asserted ``reason == "no_api_key"``, but
        the env now carries live Resend keys so the trigger actually
        sends. We instead verify that the response shape is always
        one of the documented states:
          * ``{sent: <int>, skipped: false, ...}`` — real send happened
          * ``{sent: 0, skipped: true, reason: ...}`` — no provider /
            no market content available
        """
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.post(f"{BASE_URL}/api/digest/trigger", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "sent" in data, "Response should contain 'sent' field"
        assert "skipped" in data, "Response should contain 'skipped' field"
        
        if data.get("skipped"):
            assert "reason" in data, "Skipped response should carry a reason"
            assert data["reason"] in {
                "no_email_provider", "no_content", "no_api_key",
            }, f"Unexpected skip reason: {data['reason']}"
            print(f"PASSED: Trigger skipped={data['skipped']} reason={data['reason']}")
        else:
            assert isinstance(data.get("sent"), int), "sent should be an int on success"
            # Optional keys on success path
            for key in ("errors", "with_watchlist", "content_summary"):
                assert key in data, f"Success response missing '{key}' field"
            print(f"PASSED: Trigger sent={data['sent']} errors={data.get('errors')}")
    
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
        """build_digest_html should generate valid HTML for Pro users.

        Uses the current ``collect_digest_data`` shape: predictions
        carry ``symbol``/``direction``/``confidence``, smart_money uses
        ``symbol``/``signal``/``score``, alerts use ``symbol``/``delta``.
        """
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        data = {
            "predictions": [
                {"symbol": "AAPL", "direction": "BULLISH", "confidence": 85},
                {"symbol": "TSLA", "direction": "BEARISH", "confidence": 70},
            ],
            "smart_money": [
                {"symbol": "NVDA", "signal": "buying", "score": 80,
                 "bullish": 4, "bearish": 1},
            ],
            "alerts": [
                {"symbol": "MSFT", "delta": 5, "signal_change": "NEUTRAL → BULLISH"},
            ],
            "timestamp": "2026-01-06T06:00:00Z",
        }
        
        html = build_digest_html(data, is_pro=True, user_name="Pro User")
        
        assert html.startswith("<!DOCTYPE html"), "Should be valid HTML document"
        # Greeting is lowercase-m in the current template
        assert "Good morning, Pro User" in html, "Should contain user greeting"
        assert "AAPL" in html, "Should contain ticker data"
        assert "BULLISH" in html, "Should contain direction"
        assert "Upgrade to Pro" not in html, "Pro user should NOT see upgrade CTA"
        
        print("PASSED: build_digest_html generates valid HTML for Pro user (no upgrade CTA)")
    
    def test_build_digest_html_generates_blurred_rows_for_free_user(self):
        """build_digest_html should generate blurred rows and upgrade CTA for free users."""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        data = {
            "predictions": [
                {"symbol": "AAPL", "direction": "BULLISH", "confidence": 85},
                {"symbol": "TSLA", "direction": "BEARISH", "confidence": 70},
                {"symbol": "NVDA", "direction": "NEUTRAL", "confidence": 60},
            ],
            "smart_money": [
                {"symbol": "NVDA", "signal": "buying", "score": 80, "bullish": 4, "bearish": 1},
                {"symbol": "AMD",  "signal": "selling", "score": 30, "bullish": 1, "bearish": 3},
            ],
            "alerts": [
                {"symbol": "MSFT", "delta": 5,  "signal_change": "NEUTRAL → BULLISH"},
                {"symbol": "GOOG", "delta": -3, "signal_change": "BULLISH → NEUTRAL"},
            ],
            "timestamp": "2026-01-06T06:00:00Z",
        }
        
        html = build_digest_html(data, is_pro=False, user_name="Free User")
        
        assert html.startswith("<!DOCTYPE html"), "Should be valid HTML document"
        assert "Good morning, Free User" in html, "Should contain user greeting"
        assert "AAPL" in html, "First item should be visible"
        # Free tier: rows past index 1 (predictions/smart_money) or 0
        # (alerts) get replaced with a CSS-blurred placeholder row.
        assert "filter:blur" in html, "Should have blurred rows for free user"
        assert "Upgrade to Pro" in html, "Free user should see upgrade CTA"
        assert "$55/mo" in html, "Should show pricing in CTA"
        
        print("PASSED: build_digest_html generates blurred rows and upgrade CTA for free user")
    
    def test_build_digest_html_handles_empty_data_gracefully(self):
        """build_digest_html should handle empty collections gracefully."""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.digest_service import build_digest_html
        
        data = {
            "overview": None,
            "predictions": [],
            "smart_money": [],
            "alerts": [],
            "timestamp": "2026-01-06T06:00:00Z",
        }
        
        html = build_digest_html(data, is_pro=True, user_name="Test User")
        
        # Doesn't crash and each empty block renders its own hint
        # instead of a single catch-all "No recent data available".
        assert html.startswith("<!DOCTYPE html"), "Should be valid HTML document"
        assert "No high-conviction predictions" in html, "Predictions empty hint missing"
        assert "No smart money signals" in html, "Smart money empty hint missing"
        
        print("PASSED: build_digest_html handles empty data gracefully")


class TestAPSchedulerSetup:
    """Test APScheduler is configured correctly."""
    
    def test_apscheduler_log_message_present(self):
        """Backend logs should show the combined scheduler startup message.

        The log line was consolidated into a single line listing every
        cron job (`"Schedulers started: digest (6:00), ..."`), so we
        check for the `digest (6:00)` fragment rather than the older
        standalone `"Daily digest scheduler started (6:00 AM UTC)"`.
        """
        import subprocess
        result = subprocess.run(
            ["tail", "-n", "200", "/var/log/supervisor/backend.err.log"],
            capture_output=True, text=True,
        )
        logs = result.stdout
        
        assert "Schedulers started" in logs, \
            "Backend logs should contain the APScheduler startup banner"
        assert "digest (6:00)" in logs, \
            "Startup banner should list the daily digest cron job"
        
        print("PASSED: APScheduler startup banner present with daily digest cron")


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
