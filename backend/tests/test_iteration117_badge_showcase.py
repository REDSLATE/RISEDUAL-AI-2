"""
Iteration 117: Badge Showcase on Public User Profiles & Leaderboard
Tests for:
- GET /api/referral/leaderboard - returns entries with user_id and badge fields
- GET /api/referral/profile/{user_id} - returns public profile with badge info
- GET /api/referral/profile/{invalid_id} - returns 404
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"
ADMIN_USER_ID = "69d8c194e0cdcd6613a00891"

OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"


class TestLeaderboardWithBadges:
    """Tests for GET /api/referral/leaderboard with badge field"""

    def test_leaderboard_returns_200(self):
        """Leaderboard endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: Leaderboard returns 200")

    def test_leaderboard_structure(self):
        """Leaderboard should return leaderboard array and total_participants"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        assert "leaderboard" in data, "Response missing 'leaderboard' field"
        assert "total_participants" in data, "Response missing 'total_participants' field"
        assert isinstance(data["leaderboard"], list), "leaderboard should be a list"
        assert isinstance(data["total_participants"], int), "total_participants should be int"
        print(f"PASS: Leaderboard structure valid - {len(data['leaderboard'])} entries, {data['total_participants']} participants")

    def test_leaderboard_entries_have_required_fields(self):
        """Each leaderboard entry should have rank, name, referrals, user_id, badge"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        required_fields = ["rank", "name", "referrals", "user_id", "badge"]
        
        for entry in data["leaderboard"]:
            for field in required_fields:
                assert field in entry, f"Entry missing required field: {field}"
            
            # Validate badge is one of the expected types
            valid_badges = ["creator", "founding", "beta", "pro", "free"]
            assert entry["badge"] in valid_badges, f"Invalid badge type: {entry['badge']}"
            
            # Validate user_id is present and non-empty
            assert entry["user_id"], f"user_id should not be empty"
            
        print(f"PASS: All {len(data['leaderboard'])} entries have required fields including badge")

    def test_leaderboard_badge_values(self):
        """Verify badge values are valid types"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        valid_badges = ["creator", "founding", "beta", "pro", "free"]
        badge_counts = {b: 0 for b in valid_badges}
        
        for entry in data["leaderboard"]:
            badge = entry.get("badge", "free")
            assert badge in valid_badges, f"Invalid badge: {badge}"
            badge_counts[badge] += 1
        
        print(f"PASS: Badge distribution: {badge_counts}")


class TestPublicProfile:
    """Tests for GET /api/referral/profile/{user_id}"""

    def test_profile_valid_user_returns_200(self):
        """Profile endpoint should return 200 for valid user_id"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print(f"PASS: Profile for admin user returns 200")

    def test_profile_invalid_user_returns_404(self):
        """Profile endpoint should return 404 for invalid user_id"""
        invalid_id = "000000000000000000000000"
        response = requests.get(f"{BASE_URL}/api/referral/profile/{invalid_id}")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        print("PASS: Profile for invalid user returns 404")

    def test_profile_malformed_id_returns_404(self):
        """Profile endpoint should return 404 for malformed user_id"""
        malformed_id = "not-a-valid-id"
        response = requests.get(f"{BASE_URL}/api/referral/profile/{malformed_id}")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        print("PASS: Profile for malformed ID returns 404")

    def test_profile_structure(self):
        """Profile should return all required fields"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        required_fields = [
            "user_id", "name", "badge", "role", "founding_member", 
            "beta_access", "subscription_status", "total_referrals", 
            "leaderboard_rank", "member_since"
        ]
        
        for field in required_fields:
            assert field in data, f"Profile missing required field: {field}"
        
        print(f"PASS: Profile has all required fields: {list(data.keys())}")

    def test_admin_profile_has_creator_badge(self):
        """Admin user should have 'creator' badge"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        assert data["badge"] == "creator", f"Expected 'creator' badge for admin, got '{data['badge']}'"
        assert data["role"] == "admin", f"Expected 'admin' role, got '{data['role']}'"
        print(f"PASS: Admin user has creator badge and admin role")

    def test_profile_name_is_masked(self):
        """Profile name should be masked (e.g., 'J*** D***')"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        name = data.get("name", "")
        # Masked names should contain asterisks
        assert "*" in name or name == "Anonymous", f"Name should be masked, got: {name}"
        print(f"PASS: Profile name is masked: {name}")

    def test_profile_referral_stats(self):
        """Profile should include referral stats"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        assert "total_referrals" in data, "Missing total_referrals"
        assert isinstance(data["total_referrals"], int), "total_referrals should be int"
        
        # leaderboard_rank can be None if user has no completed referrals
        assert "leaderboard_rank" in data, "Missing leaderboard_rank"
        
        print(f"PASS: Profile has referral stats - total: {data['total_referrals']}, rank: {data['leaderboard_rank']}")

    def test_profile_badge_types(self):
        """Profile badge should be one of valid types"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        valid_badges = ["creator", "founding", "beta", "pro", "free"]
        assert data["badge"] in valid_badges, f"Invalid badge: {data['badge']}"
        print(f"PASS: Profile badge '{data['badge']}' is valid")


class TestBadgePriority:
    """Tests for badge priority logic: creator > founding > beta > pro > free"""

    def test_admin_gets_creator_badge(self):
        """Admin role should get creator badge (highest priority)"""
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        
        # Admin should have creator badge regardless of other flags
        assert data["badge"] == "creator", f"Admin should have creator badge, got: {data['badge']}"
        print("PASS: Admin correctly gets creator badge")


class TestLeaderboardProfileIntegration:
    """Integration tests between leaderboard and profile endpoints"""

    def test_leaderboard_user_ids_are_valid_for_profile(self):
        """User IDs from leaderboard should work with profile endpoint"""
        lb_response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert lb_response.status_code == 200
        lb_data = lb_response.json()
        
        if not lb_data["leaderboard"]:
            print("SKIP: No leaderboard entries to test")
            return
        
        # Test first entry's user_id
        first_entry = lb_data["leaderboard"][0]
        user_id = first_entry["user_id"]
        
        profile_response = requests.get(f"{BASE_URL}/api/referral/profile/{user_id}")
        assert profile_response.status_code == 200, f"Profile for leaderboard user {user_id} failed"
        
        profile_data = profile_response.json()
        assert profile_data["user_id"] == user_id, "user_id mismatch"
        
        # Badge should match between leaderboard and profile
        assert profile_data["badge"] == first_entry["badge"], \
            f"Badge mismatch: leaderboard={first_entry['badge']}, profile={profile_data['badge']}"
        
        print(f"PASS: Leaderboard user_id {user_id} works with profile endpoint, badges match")


class TestAuthenticatedEndpoints:
    """Test that profile endpoint is public (no auth required)"""

    def test_profile_is_public_no_auth_required(self):
        """Profile endpoint should work without authentication"""
        # No cookies, no auth headers
        response = requests.get(f"{BASE_URL}/api/referral/profile/{ADMIN_USER_ID}")
        assert response.status_code == 200, f"Profile should be public, got {response.status_code}"
        print("PASS: Profile endpoint is public (no auth required)")

    def test_leaderboard_is_public_no_auth_required(self):
        """Leaderboard endpoint should work without authentication"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200, f"Leaderboard should be public, got {response.status_code}"
        print("PASS: Leaderboard endpoint is public (no auth required)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
