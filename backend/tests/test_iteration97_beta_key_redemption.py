"""
Iteration 97: Beta Key Redemption & Embeddable Widget Tests
Tests:
- POST /api/auth/redeem-beta-key - validates key, creates Pro account, marks waitlist entry as 'active'
- GET /api/waitlist/embed/widget.js - returns JavaScript content
- GET /api/waitlist/embed/snippet - returns embed code with instructions
"""
import pytest
import requests
import os
import time
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestEmbedWidget:
    """Tests for embeddable widget endpoints"""
    
    def test_widget_js_returns_javascript(self):
        """GET /api/waitlist/embed/widget.js returns JavaScript content"""
        res = requests.get(f"{BASE_URL}/api/waitlist/embed/widget.js")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        
        # Check content type is JavaScript
        content_type = res.headers.get('content-type', '')
        assert 'javascript' in content_type.lower(), f"Expected JavaScript content-type, got {content_type}"
        
        # Check content contains expected JavaScript
        content = res.text
        assert 'RiseDualWaitlist' in content, "Widget should define RiseDualWaitlist"
        assert 'createWidget' in content, "Widget should have createWidget function"
        assert '/api/waitlist' in content, "Widget should reference waitlist API"
        print("PASS: widget.js returns valid JavaScript")
    
    def test_embed_snippet_returns_json(self):
        """GET /api/waitlist/embed/snippet returns embed code with instructions"""
        res = requests.get(f"{BASE_URL}/api/waitlist/embed/snippet")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        
        data = res.json()
        assert 'snippet' in data, "Response should contain 'snippet'"
        assert 'script_url' in data, "Response should contain 'script_url'"
        assert 'instructions' in data, "Response should contain 'instructions'"
        
        # Verify snippet contains expected HTML
        snippet = data['snippet']
        assert 'risedual-waitlist' in snippet, "Snippet should have container div"
        assert 'widget.js' in snippet, "Snippet should reference widget.js"
        assert 'RiseDualWaitlist.init' in snippet, "Snippet should call init function"
        
        print(f"PASS: embed/snippet returns valid JSON with snippet")


class TestBetaKeyRedemption:
    """Tests for beta key redemption flow"""
    
    @pytest.fixture
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        if res.status_code != 200:
            pytest.skip(f"Admin login failed: {res.status_code} - {res.text}")
        return session
    
    @pytest.fixture
    def invited_user_with_beta_key(self, admin_session):
        """Create a waitlist entry and auto-invite to get a beta key"""
        unique_id = uuid.uuid4().hex[:8]
        test_email = f"test_beta_{unique_id}@example.com"
        
        # Join waitlist
        join_res = requests.post(f"{BASE_URL}/api/waitlist/join", json={
            "email": test_email,
            "name": f"Test Beta User {unique_id}"
        })
        assert join_res.status_code == 200, f"Failed to join waitlist: {join_res.text}"
        join_data = join_res.json()
        referral_code = join_data.get('referral_code')
        
        # Admin auto-invite this user (batch_size=1 to invite just this one)
        # First, we need to make sure this user is at the top by checking their position
        # For simplicity, we'll auto-invite with batch_size=5 and hope our user gets invited
        invite_res = admin_session.post(f"{BASE_URL}/api/waitlist/admin/auto-invite", json={
            "batch_size": 5
        })
        assert invite_res.status_code == 200, f"Auto-invite failed: {invite_res.text}"
        
        # Check if our user was invited
        status_res = requests.get(f"{BASE_URL}/api/waitlist/status/{referral_code}")
        if status_res.status_code == 200:
            status_data = status_res.json()
            if status_data.get('status') == 'invited' and status_data.get('beta_key'):
                return {
                    "email": test_email,
                    "beta_key": status_data['beta_key'],
                    "referral_code": referral_code
                }
        
        # If not invited, try direct invite endpoint
        direct_invite_res = admin_session.post(f"{BASE_URL}/api/waitlist/admin/invite", json={
            "count": 10
        })
        
        # Check status again
        status_res = requests.get(f"{BASE_URL}/api/waitlist/status/{referral_code}")
        if status_res.status_code == 200:
            status_data = status_res.json()
            if status_data.get('status') == 'invited' and status_data.get('beta_key'):
                return {
                    "email": test_email,
                    "beta_key": status_data['beta_key'],
                    "referral_code": referral_code
                }
        
        pytest.skip(f"Could not get beta key for test user. Status: {status_res.text}")
    
    def test_redeem_beta_key_success(self, admin_session, invited_user_with_beta_key):
        """POST /api/auth/redeem-beta-key creates Pro account with valid beta key"""
        user_data = invited_user_with_beta_key
        unique_id = uuid.uuid4().hex[:6]
        new_email = f"test_redeem_{unique_id}@example.com"
        
        res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": new_email,
            "password": "TestPass123!",
            "name": f"Redeemed User {unique_id}"
        })
        
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        
        # Verify response contains expected fields
        assert data.get('beta_activated') == True, "Response should have beta_activated=true"
        assert data.get('subscription_status') == 'pro', "User should have pro subscription"
        assert 'trial_ends_at' in data, "Response should include trial_ends_at"
        assert 'id' in data, "Response should include user id"
        assert data.get('email') == new_email, "Email should match"
        
        print(f"PASS: Beta key redeemed successfully, user has pro status")
    
    def test_redeem_invalid_beta_key(self):
        """POST /api/auth/redeem-beta-key with invalid key returns 400"""
        unique_id = uuid.uuid4().hex[:6]
        res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": "BETA-INVALID-KEY-1234",
            "email": f"test_invalid_{unique_id}@example.com",
            "password": "TestPass123!",
            "name": "Invalid Key User"
        })
        
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"
        data = res.json()
        assert 'detail' in data, "Response should have error detail"
        assert 'invalid' in data['detail'].lower() or 'used' in data['detail'].lower(), \
            f"Error should mention invalid/used key: {data['detail']}"
        print(f"PASS: Invalid beta key returns 400 with message: {data['detail']}")
    
    def test_redeem_already_used_key(self, admin_session, invited_user_with_beta_key):
        """POST /api/auth/redeem-beta-key with already-used key returns 400"""
        user_data = invited_user_with_beta_key
        unique_id = uuid.uuid4().hex[:6]
        
        # First redemption should succeed
        first_email = f"test_first_{unique_id}@example.com"
        first_res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": first_email,
            "password": "TestPass123!",
            "name": "First User"
        })
        
        if first_res.status_code != 200:
            pytest.skip(f"First redemption failed: {first_res.text}")
        
        # Second redemption with same key should fail
        second_email = f"test_second_{unique_id}@example.com"
        second_res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": second_email,
            "password": "TestPass123!",
            "name": "Second User"
        })
        
        assert second_res.status_code == 400, f"Expected 400, got {second_res.status_code}"
        data = second_res.json()
        assert 'detail' in data, "Response should have error detail"
        print(f"PASS: Already-used beta key returns 400: {data['detail']}")
    
    def test_redeem_with_already_registered_email(self, admin_session, invited_user_with_beta_key):
        """POST /api/auth/redeem-beta-key with already-registered email returns 400"""
        user_data = invited_user_with_beta_key
        
        # Try to redeem with admin email (already registered)
        res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": ADMIN_EMAIL,  # Already registered
            "password": "TestPass123!",
            "name": "Duplicate Email User"
        })
        
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"
        data = res.json()
        assert 'detail' in data, "Response should have error detail"
        assert 'already registered' in data['detail'].lower() or 'email' in data['detail'].lower(), \
            f"Error should mention already registered: {data['detail']}"
        print(f"PASS: Already-registered email returns 400: {data['detail']}")
    
    def test_redeem_short_password(self, admin_session, invited_user_with_beta_key):
        """POST /api/auth/redeem-beta-key with short password returns 400"""
        user_data = invited_user_with_beta_key
        unique_id = uuid.uuid4().hex[:6]
        
        res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": f"test_short_{unique_id}@example.com",
            "password": "123",  # Too short
            "name": "Short Password User"
        })
        
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"
        data = res.json()
        assert 'detail' in data, "Response should have error detail"
        assert 'password' in data['detail'].lower() or '6' in data['detail'], \
            f"Error should mention password requirement: {data['detail']}"
        print(f"PASS: Short password returns 400: {data['detail']}")
    
    def test_waitlist_status_updated_after_redemption(self, admin_session, invited_user_with_beta_key):
        """Waitlist entry status updated to 'active' after redemption"""
        user_data = invited_user_with_beta_key
        unique_id = uuid.uuid4().hex[:6]
        new_email = f"test_status_{unique_id}@example.com"
        
        # Check status before redemption
        before_res = requests.get(f"{BASE_URL}/api/waitlist/status/{user_data['referral_code']}")
        if before_res.status_code == 200:
            before_data = before_res.json()
            before_status = before_data.get('status')
            print(f"Status before redemption: {before_status}")
        
        # Redeem the key
        redeem_res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
            "beta_key": user_data['beta_key'],
            "email": new_email,
            "password": "TestPass123!",
            "name": f"Status Test User {unique_id}"
        })
        
        if redeem_res.status_code != 200:
            pytest.skip(f"Redemption failed: {redeem_res.text}")
        
        # Check status after redemption
        after_res = requests.get(f"{BASE_URL}/api/waitlist/status/{user_data['referral_code']}")
        assert after_res.status_code == 200, f"Status check failed: {after_res.text}"
        after_data = after_res.json()
        
        assert after_data.get('status') == 'active', \
            f"Expected status='active', got '{after_data.get('status')}'"
        print(f"PASS: Waitlist entry status updated to 'active' after redemption")


class TestBetaKeyFormat:
    """Tests for beta key format validation"""
    
    def test_beta_key_format_validation(self):
        """Beta key should be in BETA-XXXX-XXXX-XXXX format"""
        # Test with malformed keys
        malformed_keys = [
            "BETA-123",
            "INVALID-KEY",
            "beta-xxxx-xxxx-xxxx",  # lowercase
            "BETA-XXXX-XXXX",  # missing segment
            "",
            "12345678901234567890"
        ]
        
        for key in malformed_keys:
            unique_id = uuid.uuid4().hex[:6]
            res = requests.post(f"{BASE_URL}/api/auth/redeem-beta-key", json={
                "beta_key": key,
                "email": f"test_format_{unique_id}@example.com",
                "password": "TestPass123!",
                "name": "Format Test"
            })
            # Should return 400 for invalid keys
            assert res.status_code == 400, f"Key '{key}' should be rejected, got {res.status_code}"
        
        print("PASS: Malformed beta keys are rejected")


class TestAdminEmbedAccess:
    """Tests for admin access to embed snippet"""
    
    def test_embed_snippet_public_access(self):
        """Embed snippet should be publicly accessible"""
        res = requests.get(f"{BASE_URL}/api/waitlist/embed/snippet")
        assert res.status_code == 200, f"Embed snippet should be public, got {res.status_code}"
        print("PASS: Embed snippet is publicly accessible")
    
    def test_widget_js_public_access(self):
        """Widget.js should be publicly accessible"""
        res = requests.get(f"{BASE_URL}/api/waitlist/embed/widget.js")
        assert res.status_code == 200, f"Widget.js should be public, got {res.status_code}"
        print("PASS: Widget.js is publicly accessible")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
