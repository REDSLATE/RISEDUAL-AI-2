"""
Iteration 95: Beta Waitlist System Tests
Tests for the complete waitlist feature including:
- Public endpoints: join, status, leaderboard, stats
- Admin endpoints: list, invite, select-founding
- Referral system with priority scoring
"""
import pytest
import requests
import uuid
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"


class TestWaitlistPublicEndpoints:
    """Tests for public waitlist endpoints (no auth required)"""
    
    def test_join_waitlist_new_user(self):
        """POST /api/waitlist/join - creates new waitlist entry"""
        unique_email = f"test_join_{uuid.uuid4().hex[:8]}@test.com"
        response = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": unique_email, "name": "Test User"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("already_joined") == False, "New user should not be already_joined"
        assert "position" in data, "Response should include position"
        assert "referral_code" in data, "Response should include referral_code"
        assert data["referral_code"].startswith("RD"), "Referral code should start with RD"
        assert len(data["referral_code"]) == 8, "Referral code should be 8 chars (RD + 6 hex)"
        assert "priority_score" in data, "Response should include priority_score"
        assert data["priority_score"] == data["position"], "Initial priority_score equals position"
        assert data.get("status") == "waiting", "Initial status should be 'waiting'"
        assert "total_waitlist" in data, "Response should include total_waitlist"
        
        # Store for cleanup
        self.__class__.test_referral_code = data["referral_code"]
        self.__class__.test_email = unique_email
        print(f"✓ Created waitlist entry: {unique_email} at position #{data['position']}")
    
    def test_join_waitlist_duplicate_email(self):
        """POST /api/waitlist/join with same email returns already_joined=true"""
        # Use the email from previous test
        email = getattr(self.__class__, 'test_email', f"test_dup_{uuid.uuid4().hex[:8]}@test.com")
        
        # First join (if not already done)
        requests.post(f"{BASE_URL}/api/waitlist/join", json={"email": email, "name": "Test"})
        
        # Second join should return already_joined
        response = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": email, "name": "Test User Again"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("already_joined") == True, "Duplicate email should return already_joined=true"
        assert "referral_code" in data, "Should still return referral_code"
        print(f"✓ Duplicate join correctly returns already_joined=true")
    
    def test_join_waitlist_with_referral_code(self):
        """POST /api/waitlist/join with referral_code credits the referrer"""
        # First create a referrer
        referrer_email = f"test_referrer_{uuid.uuid4().hex[:8]}@test.com"
        referrer_resp = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": referrer_email, "name": "Referrer"}
        )
        assert referrer_resp.status_code == 200
        referrer_data = referrer_resp.json()
        referrer_code = referrer_data["referral_code"]
        initial_position = referrer_data["position"]
        
        # Now join with referral code
        referred_email = f"test_referred_{uuid.uuid4().hex[:8]}@test.com"
        referred_resp = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": referred_email, "name": "Referred User", "referral_code": referrer_code}
        )
        assert referred_resp.status_code == 200
        
        # Check referrer's status - should have 1 referral and lower priority score
        status_resp = requests.get(f"{BASE_URL}/api/waitlist/status/{referrer_code}")
        assert status_resp.status_code == 200
        
        status_data = status_resp.json()
        assert status_data["referral_count"] == 1, "Referrer should have 1 referral"
        expected_score = initial_position - (1 * 20)  # position - (referral_count * 20)
        assert status_data["priority_score"] == expected_score, f"Priority score should be {expected_score}"
        print(f"✓ Referral credited: {referrer_code} now has score {status_data['priority_score']}")
    
    def test_join_waitlist_invalid_email(self):
        """POST /api/waitlist/join with invalid email returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": "invalid-email", "name": "Test"}
        )
        assert response.status_code == 400, f"Expected 400 for invalid email, got {response.status_code}"
        print("✓ Invalid email correctly rejected with 400")
    
    def test_get_waitlist_status(self):
        """GET /api/waitlist/status/{code} returns user's waitlist status"""
        # Create a user first
        email = f"test_status_{uuid.uuid4().hex[:8]}@test.com"
        join_resp = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": email, "name": "Status Test User"}
        )
        assert join_resp.status_code == 200
        code = join_resp.json()["referral_code"]
        
        # Get status
        response = requests.get(f"{BASE_URL}/api/waitlist/status/{code}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["email"] == email.lower(), "Email should match"
        assert "rank" in data, "Should include rank"
        assert "referral_count" in data, "Should include referral_count"
        assert "priority_score" in data, "Should include priority_score"
        assert "total_waitlist" in data, "Should include total_waitlist"
        assert "status" in data, "Should include status"
        assert data["rank"] >= 1, "Rank should be at least 1"
        print(f"✓ Status check: rank #{data['rank']}, score {data['priority_score']}")
    
    def test_get_waitlist_status_not_found(self):
        """GET /api/waitlist/status/{code} returns 404 for invalid code"""
        response = requests.get(f"{BASE_URL}/api/waitlist/status/INVALID123")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✓ Invalid referral code correctly returns 404")
    
    def test_get_waitlist_stats(self):
        """GET /api/waitlist/stats returns waitlist statistics"""
        response = requests.get(f"{BASE_URL}/api/waitlist/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert "total" in data, "Should include total count"
        assert "waiting" in data, "Should include waiting count"
        assert "invited" in data, "Should include invited count"
        assert "founding" in data, "Should include founding count"
        assert isinstance(data["total"], int), "Total should be integer"
        assert data["total"] >= 0, "Total should be non-negative"
        print(f"✓ Stats: total={data['total']}, waiting={data['waiting']}, invited={data['invited']}, founding={data['founding']}")
    
    def test_get_waitlist_leaderboard(self):
        """GET /api/waitlist/leaderboard returns top referrers"""
        response = requests.get(f"{BASE_URL}/api/waitlist/leaderboard")
        assert response.status_code == 200
        
        data = response.json()
        assert "leaderboard" in data, "Should include leaderboard array"
        assert isinstance(data["leaderboard"], list), "Leaderboard should be a list"
        
        # If there are entries, verify structure
        if len(data["leaderboard"]) > 0:
            entry = data["leaderboard"][0]
            assert "referral_code" in entry, "Entry should have referral_code"
            assert "referral_count" in entry, "Entry should have referral_count"
            assert "priority_score" in entry, "Entry should have priority_score"
            assert "email" not in entry, "Entry should NOT expose email"
            print(f"✓ Leaderboard has {len(data['leaderboard'])} entries")
        else:
            print("✓ Leaderboard is empty (no referrals yet)")


class TestWaitlistAdminEndpoints:
    """Tests for admin-only waitlist endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup_admin_session(self):
        """Login as admin before each test"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Admin login failed: {login_resp.text}")
        yield
        self.session.close()
    
    def test_admin_list_waitlist(self):
        """GET /api/waitlist/admin/list returns full waitlist for admin"""
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/list?limit=10")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "entries" in data, "Should include entries array"
        assert "total" in data, "Should include total count"
        assert "waiting" in data, "Should include waiting count"
        assert "invited" in data, "Should include invited count"
        assert "founding_count" in data, "Should include founding_count"
        
        if len(data["entries"]) > 0:
            entry = data["entries"][0]
            assert "email" in entry, "Admin view should include email"
            assert "position" in entry, "Should include position"
            assert "priority_score" in entry, "Should include priority_score"
            assert "status" in entry, "Should include status"
        print(f"✓ Admin list: {len(data['entries'])} entries, total={data['total']}")
    
    def test_admin_list_sort_by_priority(self):
        """GET /api/waitlist/admin/list?sort_by=priority sorts by priority_score"""
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/list?sort_by=priority&limit=20")
        assert response.status_code == 200
        
        data = response.json()
        entries = data.get("entries", [])
        if len(entries) >= 2:
            # Verify sorted by priority_score ascending (lower = higher priority)
            for i in range(len(entries) - 1):
                assert entries[i]["priority_score"] <= entries[i+1]["priority_score"], \
                    "Entries should be sorted by priority_score ascending"
        print("✓ Admin list sorted by priority correctly")
    
    def test_admin_list_sort_by_position(self):
        """GET /api/waitlist/admin/list?sort_by=position sorts by signup order"""
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/list?sort_by=position&limit=20")
        assert response.status_code == 200
        
        data = response.json()
        entries = data.get("entries", [])
        if len(entries) >= 2:
            for i in range(len(entries) - 1):
                assert entries[i]["position"] <= entries[i+1]["position"], \
                    "Entries should be sorted by position ascending"
        print("✓ Admin list sorted by position correctly")
    
    def test_admin_invite_users(self):
        """POST /api/waitlist/admin/invite invites top N users"""
        # First ensure we have some waiting users
        for i in range(3):
            email = f"test_invite_{uuid.uuid4().hex[:8]}@test.com"
            requests.post(f"{BASE_URL}/api/waitlist/join", json={"email": email, "name": f"Invite Test {i}"})
        
        response = self.session.post(
            f"{BASE_URL}/api/waitlist/admin/invite",
            json={"count": 2}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "invited" in data, "Should include invited array"
        assert "count" in data, "Should include count"
        assert isinstance(data["invited"], list), "Invited should be a list"
        print(f"✓ Admin invited {data['count']} users")
    
    def test_admin_invite_invalid_count(self):
        """POST /api/waitlist/admin/invite with invalid count returns 400"""
        response = self.session.post(
            f"{BASE_URL}/api/waitlist/admin/invite",
            json={"count": 150}  # Max is 100
        )
        assert response.status_code == 400, f"Expected 400 for count > 100, got {response.status_code}"
        print("✓ Invalid invite count correctly rejected")
    
    def test_admin_select_founding_100(self):
        """POST /api/waitlist/admin/select-founding selects Founding 100"""
        response = self.session.post(f"{BASE_URL}/api/waitlist/admin/select-founding")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "founders" in data, "Should include founders array"
        assert "count" in data, "Should include count"
        print(f"✓ Selected {data['count']} founding members")


class TestWaitlistAdminAuth:
    """Tests for admin endpoint authorization"""
    
    def test_admin_list_requires_auth(self):
        """GET /api/waitlist/admin/list returns 401/403 without auth"""
        response = requests.get(f"{BASE_URL}/api/waitlist/admin/list")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Admin list correctly requires authentication")
    
    def test_admin_invite_requires_auth(self):
        """POST /api/waitlist/admin/invite returns 401/403 without auth"""
        response = requests.post(
            f"{BASE_URL}/api/waitlist/admin/invite",
            json={"count": 5}
        )
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Admin invite correctly requires authentication")
    
    def test_admin_select_founding_requires_auth(self):
        """POST /api/waitlist/admin/select-founding returns 401/403 without auth"""
        response = requests.post(f"{BASE_URL}/api/waitlist/admin/select-founding")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Admin select-founding correctly requires authentication")
    
    def test_regular_user_cannot_access_admin(self):
        """Regular user cannot access admin endpoints"""
        # Create and login as regular user
        session = requests.Session()
        test_email = f"test_regular_{uuid.uuid4().hex[:8]}@test.com"
        
        # Register
        reg_resp = session.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": test_email, "password": "TestPass123!", "name": "Regular User"}
        )
        
        if reg_resp.status_code == 200:
            # Try to access admin endpoint
            response = session.get(f"{BASE_URL}/api/waitlist/admin/list")
            assert response.status_code == 403, f"Regular user should get 403, got {response.status_code}"
            print("✓ Regular user correctly denied admin access")
        else:
            # If registration failed, skip
            pytest.skip("Could not create regular user for test")
        
        session.close()


class TestWaitlistPriorityScoring:
    """Tests for the priority scoring system"""
    
    def test_priority_score_formula(self):
        """Verify priority_score = position - (referral_count * 20)"""
        # Create referrer
        referrer_email = f"test_score_{uuid.uuid4().hex[:8]}@test.com"
        referrer_resp = requests.post(
            f"{BASE_URL}/api/waitlist/join",
            json={"email": referrer_email, "name": "Score Test Referrer"}
        )
        assert referrer_resp.status_code == 200
        referrer_data = referrer_resp.json()
        referrer_code = referrer_data["referral_code"]
        position = referrer_data["position"]
        
        # Initial score should equal position
        assert referrer_data["priority_score"] == position, "Initial score should equal position"
        
        # Add 2 referrals
        for i in range(2):
            email = f"test_ref_{uuid.uuid4().hex[:8]}@test.com"
            requests.post(
                f"{BASE_URL}/api/waitlist/join",
                json={"email": email, "name": f"Referred {i}", "referral_code": referrer_code}
            )
        
        # Check updated score
        status_resp = requests.get(f"{BASE_URL}/api/waitlist/status/{referrer_code}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        
        expected_score = position - (2 * 20)  # 2 referrals * 20 spots each
        assert status_data["priority_score"] == expected_score, \
            f"Expected score {expected_score}, got {status_data['priority_score']}"
        assert status_data["referral_count"] == 2, "Should have 2 referrals"
        print(f"✓ Priority score formula verified: {position} - (2 * 20) = {expected_score}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
