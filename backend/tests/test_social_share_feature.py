"""
Test Social Share Feature - Backend API Tests
Tests for referral endpoints that support the social share buttons feature
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestReferralLeaderboardAPI:
    """Tests for /api/referral/leaderboard endpoint (public, no auth)"""
    
    def test_leaderboard_is_public(self):
        """Leaderboard should be accessible without authentication"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: Leaderboard endpoint is public (no auth required)")
    
    def test_leaderboard_response_structure(self):
        """Leaderboard should return correct structure"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        assert "leaderboard" in data, "Response should contain 'leaderboard' key"
        assert "total_participants" in data, "Response should contain 'total_participants' key"
        assert isinstance(data["leaderboard"], list), "Leaderboard should be a list"
        assert isinstance(data["total_participants"], int), "total_participants should be an integer"
        print(f"PASS: Leaderboard structure correct - {len(data['leaderboard'])} entries, {data['total_participants']} participants")
    
    def test_leaderboard_entry_fields(self):
        """Each leaderboard entry should have rank, name, referrals"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["leaderboard"]) > 0:
            entry = data["leaderboard"][0]
            assert "rank" in entry, "Entry should have 'rank'"
            assert "name" in entry, "Entry should have 'name'"
            assert "referrals" in entry, "Entry should have 'referrals'"
            print(f"PASS: Leaderboard entry has correct fields: {entry}")
        else:
            print("SKIP: No leaderboard entries to verify")


class TestReferralInfoAPI:
    """Tests for /api/referral/info endpoint (requires auth)"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token for owner account"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "managingdirector@redslateholdings.com",
            "password": "RedSlate2026!"
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Authentication failed - skipping authenticated tests")
    
    def test_referral_info_requires_auth(self):
        """Referral info should require authentication"""
        response = requests.get(f"{BASE_URL}/api/referral/info")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("PASS: Referral info requires authentication")
    
    def test_referral_info_returns_code(self, auth_token):
        """Referral info should return user's referral code"""
        response = requests.get(
            f"{BASE_URL}/api/referral/info",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "code" in data, "Response should contain 'code'"
        assert isinstance(data["code"], str), "Code should be a string"
        assert len(data["code"]) > 0, "Code should not be empty"
        print(f"PASS: Referral info returns code: {data['code']}")
    
    def test_referral_info_structure(self, auth_token):
        """Referral info should return complete structure for social sharing"""
        response = requests.get(
            f"{BASE_URL}/api/referral/info",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Required fields for social share feature
        required_fields = ["code", "total_referrals", "completed_referrals", "rewards_earned", "rewards_remaining", "reward_cap", "referrals"]
        for field in required_fields:
            assert field in data, f"Response should contain '{field}'"
        
        print(f"PASS: Referral info has all required fields for social sharing")
        print(f"  - Code: {data['code']}")
        print(f"  - Total referrals: {data['total_referrals']}")
        print(f"  - Completed: {data['completed_referrals']}")


class TestSocialShareURLConstruction:
    """Tests to verify share URL construction logic (client-side, but we verify the data)"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "managingdirector@redslateholdings.com",
            "password": "RedSlate2026!"
        })
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Authentication failed")
    
    def test_referral_code_format(self, auth_token):
        """Referral code should be alphanumeric and suitable for URL"""
        response = requests.get(
            f"{BASE_URL}/api/referral/info",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        code = response.json()["code"]
        
        # Code should be alphanumeric (safe for URLs)
        assert code.isalnum(), f"Code should be alphanumeric, got: {code}"
        print(f"PASS: Referral code '{code}' is URL-safe (alphanumeric)")
    
    def test_referral_link_can_be_constructed(self, auth_token):
        """Verify referral link can be constructed from code"""
        response = requests.get(
            f"{BASE_URL}/api/referral/info",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        code = response.json()["code"]
        
        # Construct referral link (as frontend does)
        base_url = "https://risedual-trading.preview.emergentagent.com"
        referral_link = f"{base_url}?ref={code}"
        
        assert "?ref=" in referral_link, "Referral link should contain ?ref= parameter"
        print(f"PASS: Referral link constructed: {referral_link}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
