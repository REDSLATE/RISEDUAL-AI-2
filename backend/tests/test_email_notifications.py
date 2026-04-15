"""
Test suite for Resend email notification integration in RISEDUAL AI referral system.
Tests: email_service.py functions, graceful degradation with placeholder API key,
and referral flow integration with email notifications.
"""
import pytest
import requests
import os
import sys
import asyncio

# Add backend to path for imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from conftest
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD


class TestEmailServiceModule:
    """Test email_service.py module loading and configuration"""
    
    def test_email_service_imports_without_errors(self):
        """Verify email_service.py can be imported without errors"""
        try:
            from services.email_service import (
                send_referral_signup_email,
                send_reward_earned_email,
                send_welcome_referral_email,
                _is_configured
            )
            assert callable(send_referral_signup_email)
            assert callable(send_reward_earned_email)
            assert callable(send_welcome_referral_email)
            assert callable(_is_configured)
            print("PASS: email_service.py imports successfully")
        except ImportError as e:
            pytest.fail(f"Failed to import email_service: {e}")
    
    def test_is_configured_returns_false_with_placeholder_key(self):
        """_is_configured() should return False when API key is placeholder"""
        from services.email_service import _is_configured, RESEND_API_KEY
        
        result = _is_configured()
        assert result == False, f"Expected False with placeholder key, got {result}"
        assert RESEND_API_KEY.startswith('re_YOUR'), f"Expected placeholder key, got {RESEND_API_KEY}"
        print(f"PASS: _is_configured() returns False (API key: {RESEND_API_KEY[:15]}...)")
    
    def test_resend_api_key_in_env(self):
        """Verify RESEND_API_KEY is set in environment"""
        from services.email_service import RESEND_API_KEY
        
        assert RESEND_API_KEY is not None, "RESEND_API_KEY should not be None"
        assert len(RESEND_API_KEY) > 0, "RESEND_API_KEY should not be empty"
        assert RESEND_API_KEY == "re_YOUR_API_KEY_HERE", f"Expected placeholder, got {RESEND_API_KEY}"
        print(f"PASS: RESEND_API_KEY is set to placeholder value")
    
    def test_sender_email_in_env(self):
        """Verify SENDER_EMAIL is set in environment"""
        from services.email_service import SENDER_EMAIL
        
        assert SENDER_EMAIL is not None, "SENDER_EMAIL should not be None"
        assert len(SENDER_EMAIL) > 0, "SENDER_EMAIL should not be empty"
        assert "@" in SENDER_EMAIL, "SENDER_EMAIL should be a valid email format"
        print(f"PASS: SENDER_EMAIL is set to {SENDER_EMAIL}")


class TestEmailFunctionsGracefulDegradation:
    """Test that email functions return False gracefully when not configured"""
    
    @pytest.mark.asyncio
    async def test_send_referral_signup_email_returns_false_when_not_configured(self):
        """send_referral_signup_email should return False without crashing"""
        from services.email_service import send_referral_signup_email
        
        result = await send_referral_signup_email(
            referrer_email="test@example.com",
            referrer_name="Test User",
            referred_email="friend@example.com"
        )
        
        assert result == False, f"Expected False when not configured, got {result}"
        print("PASS: send_referral_signup_email returns False gracefully")
    
    @pytest.mark.asyncio
    async def test_send_reward_earned_email_returns_false_when_not_configured(self):
        """send_reward_earned_email should return False without crashing"""
        from services.email_service import send_reward_earned_email
        
        result = await send_reward_earned_email(
            referrer_email="test@example.com",
            referrer_name="Test User",
            referred_email="friend@example.com"
        )
        
        assert result == False, f"Expected False when not configured, got {result}"
        print("PASS: send_reward_earned_email returns False gracefully")
    
    @pytest.mark.asyncio
    async def test_send_welcome_referral_email_returns_false_when_not_configured(self):
        """send_welcome_referral_email should return False without crashing"""
        from services.email_service import send_welcome_referral_email
        
        result = await send_welcome_referral_email(
            user_email="newuser@example.com",
            user_name="New User",
            referrer_name="Referrer Name"
        )
        
        assert result == False, f"Expected False when not configured, got {result}"
        print("PASS: send_welcome_referral_email returns False gracefully")


class TestEmailHTMLTemplates:
    """Test that HTML templates render correctly"""
    
    def test_referral_signup_html_renders(self):
        """_referral_signup_html should generate valid HTML string"""
        from services.email_service import _referral_signup_html
        
        html = _referral_signup_html("John Doe", "friend@example.com")
        
        assert isinstance(html, str), "Should return a string"
        assert len(html) > 100, "HTML should have substantial content"
        assert "<!DOCTYPE html>" in html, "Should be valid HTML"
        assert "John Doe" in html, "Should contain referrer name"
        assert "friend@example.com" in html, "Should contain referred email"
        assert "Your friend just signed up" in html, "Should contain expected heading"
        assert "RISEDUAL AI" in html, "Should contain app name"
        print("PASS: _referral_signup_html renders correctly")
    
    def test_reward_earned_html_renders(self):
        """_reward_earned_html should generate valid HTML string"""
        from services.email_service import _reward_earned_html
        
        html = _reward_earned_html("Jane Smith", "subscriber@example.com")
        
        assert isinstance(html, str), "Should return a string"
        assert len(html) > 100, "HTML should have substantial content"
        assert "<!DOCTYPE html>" in html, "Should be valid HTML"
        assert "Jane Smith" in html, "Should contain referrer name"
        assert "subscriber@example.com" in html, "Should contain referred email"
        assert "You earned a free month" in html, "Should contain expected heading"
        assert "+1 Month Free" in html, "Should contain reward text"
        print("PASS: _reward_earned_html renders correctly")
    
    def test_welcome_referral_html_renders(self):
        """_welcome_referral_html should generate valid HTML string"""
        from services.email_service import _welcome_referral_html
        
        html = _welcome_referral_html("New User", "Referrer Name")
        
        assert isinstance(html, str), "Should return a string"
        assert len(html) > 100, "HTML should have substantial content"
        assert "<!DOCTYPE html>" in html, "Should be valid HTML"
        assert "New User" in html, "Should contain user name"
        assert "Referrer Name" in html, "Should contain referrer name"
        assert "Welcome to RISEDUAL AI" in html, "Should contain expected heading"
        assert "7-Day Pro Trial" in html, "Should contain trial info"
        print("PASS: _welcome_referral_html renders correctly")


class TestReferralFlowWithPlaceholderEmail:
    """Test that referral flows work correctly even with placeholder email key"""
    
    @pytest.fixture
    def owner_token(self):
        """Get owner authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code == 200:
            data = response.json()
            return data.get("access_token") or data.get("token")
        pytest.skip(f"Owner login failed: {response.status_code}")
    
    def test_referral_validate_endpoint_works(self):
        """Referral code validation should work regardless of email config"""
        # Get owner's referral code first
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip("Login failed")
        
        token = response.json().get("access_token") or response.json().get("token")
        
        # Get referral info
        headers = {"Authorization": f"Bearer {token}"}
        info_response = requests.get(f"{BASE_URL}/api/referral/info", headers=headers)
        
        if info_response.status_code == 200:
            code = info_response.json().get("code")
            if code:
                # Validate the code
                validate_response = requests.get(f"{BASE_URL}/api/referral/validate/{code}")
                assert validate_response.status_code == 200
                data = validate_response.json()
                assert data.get("valid") == True
                print(f"PASS: Referral code {code} validates correctly")
            else:
                print("PASS: Referral info endpoint works (no code yet)")
        else:
            pytest.skip(f"Referral info failed: {info_response.status_code}")
    
    def test_referral_info_endpoint_works(self, owner_token):
        """Referral info endpoint should work regardless of email config"""
        headers = {"Authorization": f"Bearer {owner_token}"}
        response = requests.get(f"{BASE_URL}/api/referral/info", headers=headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "code" in data, "Response should contain 'code'"
        assert "total_referrals" in data, "Response should contain 'total_referrals'"
        assert "completed_referrals" in data, "Response should contain 'completed_referrals'"
        assert "rewards_earned" in data, "Response should contain 'rewards_earned'"
        
        print(f"PASS: Referral info works - code: {data['code']}, total: {data['total_referrals']}")
    
    def test_referral_leaderboard_endpoint_works(self):
        """Leaderboard endpoint should work regardless of email config"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "leaderboard" in data, "Response should contain 'leaderboard'"
        assert "total_participants" in data, "Response should contain 'total_participants'"
        
        print(f"PASS: Leaderboard works - {len(data['leaderboard'])} entries, {data['total_participants']} participants")


class TestReferralPyEmailIntegration:
    """Test that referral.py correctly uses asyncio.create_task for email sends"""
    
    def test_referral_py_uses_asyncio_create_task_for_reward_email(self):
        """Verify referral.py uses asyncio.create_task for non-blocking email sends in complete_referral_reward"""
        import inspect
        from routes.referral import complete_referral_reward
        
        source = inspect.getsource(complete_referral_reward)
        
        assert "asyncio.create_task" in source, "complete_referral_reward should use asyncio.create_task"
        assert "send_reward_earned_email" in source, "complete_referral_reward should call send_reward_earned_email"
        print("PASS: complete_referral_reward uses asyncio.create_task for non-blocking email")
    
    def test_email_service_import_in_complete_referral_reward(self):
        """Verify email_service is imported in complete_referral_reward"""
        import inspect
        from routes.referral import complete_referral_reward
        
        source = inspect.getsource(complete_referral_reward)
        
        assert "from services.email_service import" in source, "Should import from email_service"
        print("PASS: complete_referral_reward imports email_service correctly")


class TestResendPackageInstalled:
    """Verify resend package is installed"""
    
    def test_resend_package_importable(self):
        """resend package should be importable"""
        try:
            import resend
            assert hasattr(resend, 'Emails'), "resend should have Emails attribute"
            assert hasattr(resend, 'api_key'), "resend should have api_key attribute"
            print(f"PASS: resend package installed and importable")
        except ImportError as e:
            pytest.fail(f"resend package not installed: {e}")


class TestBackendEnvConfiguration:
    """Test backend .env has required email configuration"""
    
    def test_env_file_has_resend_api_key(self):
        """backend/.env should have RESEND_API_KEY"""
        env_path = "/app/backend/.env"
        with open(env_path, 'r') as f:
            content = f.read()
        
        assert "RESEND_API_KEY=" in content, "RESEND_API_KEY should be in .env"
        print("PASS: RESEND_API_KEY found in backend/.env")
    
    def test_env_file_has_sender_email(self):
        """backend/.env should have SENDER_EMAIL"""
        env_path = "/app/backend/.env"
        with open(env_path, 'r') as f:
            content = f.read()
        
        assert "SENDER_EMAIL=" in content, "SENDER_EMAIL should be in .env"
        print("PASS: SENDER_EMAIL found in backend/.env")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
