"""
Test suite for Referral Leaderboard feature
Tests: GET /api/referral/leaderboard endpoint (PUBLIC - no auth required)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"


class TestLeaderboardEndpoint:
    """Tests for the public leaderboard endpoint"""
    
    def test_leaderboard_is_public_no_auth_required(self):
        """Leaderboard should be accessible without authentication"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "leaderboard" in data, "Response should contain 'leaderboard' key"
        assert "total_participants" in data, "Response should contain 'total_participants' key"
        print(f"✓ Leaderboard is public (no auth required)")
    
    def test_leaderboard_returns_array(self):
        """Leaderboard should return an array"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["leaderboard"], list), "leaderboard should be a list"
        print(f"✓ Leaderboard returns array with {len(data['leaderboard'])} entries")
    
    def test_leaderboard_entry_structure(self):
        """Each leaderboard entry should have rank, name, referrals"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["leaderboard"]) > 0:
            entry = data["leaderboard"][0]
            assert "rank" in entry, "Entry should have 'rank'"
            assert "name" in entry, "Entry should have 'name'"
            assert "referrals" in entry, "Entry should have 'referrals'"
            assert isinstance(entry["rank"], int), "rank should be integer"
            assert isinstance(entry["name"], str), "name should be string"
            assert isinstance(entry["referrals"], int), "referrals should be integer"
            print(f"✓ Entry structure correct: rank={entry['rank']}, name={entry['name']}, referrals={entry['referrals']}")
        else:
            print("✓ Leaderboard is empty (no completed referrals yet)")
    
    def test_leaderboard_names_are_masked(self):
        """Names should be partially masked (e.g., 'John' -> 'J***')"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["leaderboard"]) > 0:
            for entry in data["leaderboard"]:
                name = entry["name"]
                # Masked names should contain asterisks
                if name != "Anonymous":
                    assert "*" in name, f"Name '{name}' should be masked with asterisks"
                    # First character of each word should be visible
                    parts = name.split()
                    for part in parts:
                        assert len(part) > 1, f"Masked part '{part}' should have at least 2 chars"
                        assert part[0] != "*", f"First char of '{part}' should not be asterisk"
                print(f"✓ Name properly masked: {name}")
        else:
            print("✓ Leaderboard is empty - no names to mask")
    
    def test_leaderboard_sorted_by_referrals_descending(self):
        """Leaderboard should be sorted by referral count (highest first)"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["leaderboard"]) > 1:
            referral_counts = [entry["referrals"] for entry in data["leaderboard"]]
            assert referral_counts == sorted(referral_counts, reverse=True), \
                f"Leaderboard not sorted descending: {referral_counts}"
            print(f"✓ Leaderboard sorted by referrals descending: {referral_counts}")
        else:
            print("✓ Only 0-1 entries - sorting not applicable")
    
    def test_leaderboard_ranks_are_sequential(self):
        """Ranks should be sequential starting from 1"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["leaderboard"]) > 0:
            ranks = [entry["rank"] for entry in data["leaderboard"]]
            expected_ranks = list(range(1, len(ranks) + 1))
            assert ranks == expected_ranks, f"Ranks should be sequential: got {ranks}, expected {expected_ranks}"
            print(f"✓ Ranks are sequential: {ranks}")
        else:
            print("✓ Leaderboard is empty - no ranks to check")
    
    def test_leaderboard_limited_to_top_10(self):
        """Leaderboard should be limited to top 10 entries"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["leaderboard"]) <= 10, \
            f"Leaderboard should have max 10 entries, got {len(data['leaderboard'])}"
        print(f"✓ Leaderboard limited to top 10 (currently {len(data['leaderboard'])} entries)")
    
    def test_total_participants_count(self):
        """total_participants should be a non-negative integer"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data["total_participants"], int), "total_participants should be integer"
        assert data["total_participants"] >= 0, "total_participants should be non-negative"
        print(f"✓ total_participants = {data['total_participants']}")


class TestNameMaskingLogic:
    """Tests for the _mask_name helper function behavior"""
    
    def test_owner_has_completed_referrals(self):
        """Verify owner account has completed referrals (should appear on leaderboard)"""
        # Login as owner
        login_response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert login_response.status_code == 200, f"Owner login failed: {login_response.text}"
        token = login_response.json().get("access_token")
        
        # Get referral info
        headers = {"Authorization": f"Bearer {token}"}
        info_response = requests.get(f"{BASE_URL}/api/referral/info", headers=headers)
        assert info_response.status_code == 200
        info = info_response.json()
        
        completed = info.get("completed_referrals", 0)
        print(f"✓ Owner has {completed} completed referrals")
        
        # If owner has completed referrals, they should be on leaderboard
        if completed > 0:
            leaderboard_response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
            leaderboard = leaderboard_response.json()["leaderboard"]
            
            # Find entry with matching referral count
            matching_entries = [e for e in leaderboard if e["referrals"] == completed]
            assert len(matching_entries) > 0, \
                f"Owner with {completed} referrals should appear on leaderboard"
            print(f"✓ Owner appears on leaderboard with {completed} referrals")


class TestLeaderboardDataIntegrity:
    """Tests for data integrity between referral info and leaderboard"""
    
    def test_leaderboard_only_shows_completed_referrals(self):
        """Leaderboard should only count completed referrals, not pending"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        
        # All entries should have referrals > 0
        for entry in data["leaderboard"]:
            assert entry["referrals"] > 0, \
                f"Entry with 0 referrals should not be on leaderboard: {entry}"
        print(f"✓ All leaderboard entries have completed referrals > 0")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
