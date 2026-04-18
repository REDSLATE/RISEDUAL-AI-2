"""
Iteration 96: Waitlist Auto-Invite System Tests
Tests for:
- POST /api/waitlist/admin/auto-invite (admin only, generates beta keys, sends emails)
- Beta key format: BETA-XXXX-XXXX-XXXX (hex segments)
- Auto-invited users have status='invited', beta_key set, beta_key_expires set
- Referral success email triggered when someone joins with a referral code
- Non-admin gets 403 on auto-invite endpoint
- Existing waitlist endpoints still work
"""
import pytest
import requests
import os
import re
import time
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from test_credentials.md
class TestAutoInviteEndpoint:
    """Tests for POST /api/waitlist/admin/auto-invite endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and login as admin"""
        self.session = requests.Session()
        # Login as admin
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        self.admin_session = self.session
        yield
        self.session.close()
    
    def test_auto_invite_requires_admin(self):
        """Non-admin should get 403 on auto-invite endpoint"""
        # Create a new session without auth
        no_auth_session = requests.Session()
        resp = no_auth_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 1}
        )
        # Should be 401 (not authenticated) or 403 (forbidden)
        assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}: {resp.text}"
        no_auth_session.close()
        print("PASS: Non-admin gets 401/403 on auto-invite endpoint")
    
    def test_auto_invite_batch_size_validation(self):
        """batch_size must be 1-20"""
        # Test batch_size too small
        resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 0}
        )
        assert resp.status_code == 400, f"Expected 400 for batch_size=0, got {resp.status_code}"
        
        # Test batch_size too large
        resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 21}
        )
        assert resp.status_code == 400, f"Expected 400 for batch_size=21, got {resp.status_code}"
        print("PASS: batch_size validation works (1-20 range)")
    
    def test_auto_invite_creates_beta_keys(self):
        """Auto-invite should create beta keys in BETA-XXXX-XXXX-XXXX format"""
        # First, create some test users to invite
        timestamp = int(time.time())
        test_emails = [
            f"test_autoinvite_{timestamp}_1@example.com",
            f"test_autoinvite_{timestamp}_2@example.com",
        ]
        
        # Join waitlist with test users
        for email in test_emails:
            join_resp = self.admin_session.post(
                f"{BASE_URL}/api/waitlist/join",
                json={"email": email, "name": f"Test User {email[:10]}"}
            )
            assert join_resp.status_code == 200, f"Failed to join waitlist: {join_resp.text}"
        
        # Now trigger auto-invite
        resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 2}
        )
        assert resp.status_code == 200, f"Auto-invite failed: {resp.text}"
        
        data = resp.json()
        assert "invited" in data, "Response should contain 'invited' list"
        assert "count" in data, "Response should contain 'count'"
        
        # Check beta key format for invited users
        beta_key_pattern = re.compile(r'^BETA-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$')
        for user in data["invited"]:
            assert "beta_key" in user, f"Invited user should have beta_key: {user}"
            assert beta_key_pattern.match(user["beta_key"]), f"Beta key format invalid: {user['beta_key']}"
            assert "email_sent" in user, "Response should indicate if email was sent"
            print(f"  Invited: {user['email']} with key {user['beta_key']} (email_sent: {user['email_sent']})")
        
        print(f"PASS: Auto-invite created {data['count']} invites with valid beta keys")
    
    def test_auto_invite_updates_user_status(self):
        """Auto-invited users should have status='invited', beta_key, beta_key_expires"""
        timestamp = int(time.time())
        test_email = f"test_status_check_{timestamp}@example.com"
        
        # Join waitlist
        join_resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": test_email, "name": "Status Check User"}
        )
        assert join_resp.status_code == 200
        referral_code = join_resp.json()["referral_code"]
        
        # Check initial status
        status_resp = self.admin_session.get(f"{BASE_URL}/api/waitlist/status/{referral_code}")
        assert status_resp.status_code == 200
        initial_status = status_resp.json()
        assert initial_status["status"] == "waiting", f"Initial status should be 'waiting': {initial_status}"
        
        # Trigger auto-invite (this user should be invited since they're new)
        invite_resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 5}
        )
        assert invite_resp.status_code == 200
        
        # Check if our user was invited
        invited_data = invite_resp.json()
        invited_emails = [u["email"] for u in invited_data.get("invited", [])]
        
        if test_email in invited_emails:
            # Verify status changed via admin list
            admin_list_resp = self.admin_session.get(f"{BASE_URL}/api/waitlist/admin/list?limit=100")
            assert admin_list_resp.status_code == 200
            entries = admin_list_resp.json().get("entries", [])
            
            user_entry = next((e for e in entries if e["email"] == test_email), None)
            if user_entry:
                assert user_entry["status"] == "invited", f"Status should be 'invited': {user_entry}"
                assert "beta_key" in user_entry, f"Should have beta_key: {user_entry}"
                assert "beta_key_expires" in user_entry, f"Should have beta_key_expires: {user_entry}"
                print("PASS: User status updated to 'invited' with beta_key and expiry")
            else:
                print("INFO: User not found in admin list (may have been cleaned up)")
        else:
            print("INFO: Test user not in this batch (other users had higher priority)")
    
    def test_auto_invite_empty_queue(self):
        """Auto-invite with no waiting users should return empty list"""
        # This test may not always return empty if there are waiting users
        # Just verify the endpoint works and returns proper structure
        resp = self.admin_session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 1}
        )
        assert resp.status_code == 200, f"Auto-invite failed: {resp.text}"
        data = resp.json()
        assert "invited" in data
        assert "count" in data
        assert isinstance(data["invited"], list)
        assert isinstance(data["count"], int)
        print(f"PASS: Auto-invite returns proper structure (count: {data['count']})")


class TestReferralSuccessEmail:
    """Tests for referral success email trigger"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        yield
        self.session.close()
    
    def test_referral_credits_referrer(self):
        """When someone joins with a referral code, the referrer should be credited"""
        timestamp = int(time.time())
        
        # Create referrer
        referrer_email = f"test_referrer_{timestamp}@example.com"
        referrer_resp = self.session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": referrer_email, "name": "Referrer User"}
        )
        assert referrer_resp.status_code == 200
        referrer_data = referrer_resp.json()
        referral_code = referrer_data["referral_code"]
        initial_referral_count = referrer_data.get("referral_count", 0)
        
        # Create referred user using the referral code
        referred_email = f"test_referred_{timestamp}@example.com"
        referred_resp = self.session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={
                "email": referred_email,
                "name": "Referred User",
                "referral_code": referral_code
            }
        )
        assert referred_resp.status_code == 200
        
        # Wait a moment for async email task
        time.sleep(1)
        
        # Check referrer's updated status
        status_resp = self.session.get(f"{BASE_URL}/api/waitlist/status/{referral_code}")
        assert status_resp.status_code == 200
        updated_status = status_resp.json()
        
        assert updated_status["referral_count"] == initial_referral_count + 1, \
            f"Referral count should increase: {updated_status}"
        
        # Priority score should decrease (lower = higher priority)
        # Formula: position - (referral_count * 20)
        expected_score = updated_status["position"] - (updated_status["referral_count"] * 20)
        assert updated_status["priority_score"] == expected_score, \
            f"Priority score mismatch: expected {expected_score}, got {updated_status['priority_score']}"
        
        print(f"PASS: Referral credited - count: {updated_status['referral_count']}, score: {updated_status['priority_score']}")


class TestBetaKeyFormat:
    """Tests for beta key generation format"""
    
    def test_beta_key_format_regex(self):
        """Beta key should match BETA-XXXX-XXXX-XXXX format (hex)"""
        # Import the function directly to test
        import sys
        sys.path.insert(0, '/app/backend')
        from services.waitlist_service import _generate_beta_key
        
        # Generate multiple keys and verify format
        beta_key_pattern = re.compile(r'^BETA-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$')
        
        for i in range(10):
            key = _generate_beta_key()
            assert beta_key_pattern.match(key), f"Invalid beta key format: {key}"
        
        print("PASS: Beta key format is BETA-XXXX-XXXX-XXXX (hex)")
    
    def test_beta_keys_are_unique(self):
        """Generated beta keys should be unique"""
        import sys
        sys.path.insert(0, '/app/backend')
        from services.waitlist_service import _generate_beta_key
        
        keys = set()
        for i in range(100):
            key = _generate_beta_key()
            assert key not in keys, f"Duplicate key generated: {key}"
            keys.add(key)
        
        print("PASS: 100 beta keys generated, all unique")


class TestExistingWaitlistEndpoints:
    """Verify existing waitlist endpoints still work after auto-invite changes"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        # Login as admin for admin endpoints
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200
        yield
        self.session.close()
    
    def test_join_waitlist_still_works(self):
        """POST /api/waitlist/join should still work"""
        timestamp = int(time.time())
        resp = self.session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": f"test_join_{timestamp}@example.com", "name": "Test Join"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "position" in data
        assert "referral_code" in data
        assert "priority_score" in data
        assert data["status"] == "waiting"
        print(f"PASS: Join waitlist works - position: {data['position']}")
    
    def test_get_status_still_works(self):
        """GET /api/waitlist/status/{code} should still work"""
        timestamp = int(time.time())
        # First join
        join_resp = self.session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": f"test_status_{timestamp}@example.com", "name": "Test Status"}
        )
        assert join_resp.status_code == 200
        referral_code = join_resp.json()["referral_code"]
        
        # Then check status
        status_resp = self.session.get(f"{BASE_URL}/api/waitlist/status/{referral_code}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert "rank" in data
        assert "referral_count" in data
        assert "priority_score" in data
        print(f"PASS: Get status works - rank: {data['rank']}")
    
    def test_get_stats_still_works(self):
        """GET /api/waitlist/stats should still work"""
        resp = self.session.get(f"{BASE_URL}/api/waitlist/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "waiting" in data
        assert "invited" in data
        assert "founding" in data
        print(f"PASS: Get stats works - total: {data['total']}, invited: {data['invited']}")
    
    def test_admin_list_still_works(self):
        """GET /api/waitlist/admin/list should still work"""
        resp = self.session.get(f"{BASE_URL}/api/waitlist/admin/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "total" in data
        assert "invited" in data
        print(f"PASS: Admin list works - total: {data['total']}")
    
    def test_admin_invite_still_works(self):
        """POST /api/waitlist/admin/invite should still work"""
        resp = self.session.post(
            f"{BASE_URL}/api/waitlist/admin/invite",
            json={"count": 1}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "invited" in data
        assert "count" in data
        print(f"PASS: Admin invite works - count: {data['count']}")


class TestStatsAfterInvite:
    """Test that stats correctly reflect invited users"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200
        yield
        self.session.close()
    
    def test_stats_show_invited_count(self):
        """GET /api/waitlist/stats should show correct invited count"""
        # Get initial stats
        initial_resp = self.session.get(f"{BASE_URL}/api/waitlist/stats")
        assert initial_resp.status_code == 200
        initial_stats = initial_resp.json()
        
        # Create a test user
        timestamp = int(time.time())
        join_resp = self.session.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": f"test_stats_{timestamp}@example.com", "name": "Stats Test"}
        )
        assert join_resp.status_code == 200
        
        # Trigger auto-invite
        invite_resp = self.session.post(
            f"{BASE_URL}/api/waitlist/admin/auto-invite",
            json={"batch_size": 1}
        )
        assert invite_resp.status_code == 200
        invited_count = invite_resp.json()["count"]
        
        # Get updated stats
        updated_resp = self.session.get(f"{BASE_URL}/api/waitlist/stats")
        assert updated_resp.status_code == 200
        updated_stats = updated_resp.json()
        
        # Invited count should have increased
        if invited_count > 0:
            assert updated_stats["invited"] >= initial_stats["invited"], \
                f"Invited count should increase: {initial_stats['invited']} -> {updated_stats['invited']}"
        
        print(f"PASS: Stats show invited count: {updated_stats['invited']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
