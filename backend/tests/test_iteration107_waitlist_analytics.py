"""Iteration 107: Waitlist Analytics Dashboard Tests
Tests the new GET /api/waitlist/admin/analytics endpoint and related functionality.
"""
import pytest
import requests
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestWaitlistAnalyticsEndpoint:
    """Tests for the new /api/waitlist/admin/analytics endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for each test"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login_admin(self):
        """Login as admin and return session with cookies"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        return self.session
    
    def _login_owner(self):
        """Login as owner and return session with cookies"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Owner login failed: {response.text}"
        return self.session
    
    # ── Auth Tests ──
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin credentials returns 200"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        assert "user" in data or "email" in data
        print(f"✓ Admin login successful: {ADMIN_EMAIL}")
    
    def test_owner_login_returns_200(self):
        """POST /api/auth/login with owner credentials returns 200"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        assert "user" in data or "email" in data
        print(f"✓ Owner login successful: {OWNER_EMAIL}")
    
    # ── Public Endpoint Tests ──
    
    def test_waitlist_stats_public_endpoint(self):
        """GET /api/waitlist/stats still works (public endpoint)"""
        response = self.session.get(f"{BASE_URL}/api/waitlist/stats")
        assert response.status_code == 200
        data = response.json()
        # Verify expected fields
        assert "total" in data
        assert "waiting" in data
        assert "invited" in data
        assert "founding" in data
        print(f"✓ Waitlist stats: total={data['total']}, waiting={data['waiting']}, invited={data['invited']}")
    
    # ── Analytics Endpoint Auth Tests ──
    
    def test_analytics_without_auth_returns_401(self):
        """GET /api/waitlist/admin/analytics without auth returns 401"""
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Analytics endpoint correctly requires authentication")
    
    def test_analytics_with_admin_auth_returns_200(self):
        """GET /api/waitlist/admin/analytics with admin auth returns 200"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ Analytics endpoint accessible with admin auth")
    
    def test_analytics_with_owner_auth_returns_200(self):
        """GET /api/waitlist/admin/analytics with owner auth returns 200"""
        self._login_owner()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ Analytics endpoint accessible with owner auth")
    
    # ── Analytics Response Structure Tests ──
    
    def test_analytics_returns_daily_signups_array(self):
        """GET /api/waitlist/admin/analytics returns daily_signups array"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "daily_signups" in data, "Missing daily_signups field"
        assert isinstance(data["daily_signups"], list), "daily_signups should be a list"
        
        # If there's data, verify structure
        if len(data["daily_signups"]) > 0:
            entry = data["daily_signups"][0]
            assert "date" in entry, "daily_signups entry missing 'date'"
            assert "total" in entry, "daily_signups entry missing 'total'"
            assert "organic" in entry, "daily_signups entry missing 'organic'"
            assert "referred" in entry, "daily_signups entry missing 'referred'"
        
        print(f"✓ daily_signups array returned with {len(data['daily_signups'])} entries")
    
    def test_analytics_returns_funnel_object(self):
        """GET /api/waitlist/admin/analytics returns funnel object"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "funnel" in data, "Missing funnel field"
        funnel = data["funnel"]
        
        # Verify funnel structure
        assert "total" in funnel, "funnel missing 'total'"
        assert "waiting" in funnel, "funnel missing 'waiting'"
        assert "invited" in funnel, "funnel missing 'invited'"
        assert "active" in funnel, "funnel missing 'active'"
        assert "founding" in funnel, "funnel missing 'founding'"
        
        print(f"✓ funnel object: total={funnel['total']}, waiting={funnel['waiting']}, invited={funnel['invited']}, active={funnel['active']}, founding={funnel['founding']}")
    
    def test_analytics_returns_referral_metrics(self):
        """GET /api/waitlist/admin/analytics returns referral_metrics"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "referral_metrics" in data, "Missing referral_metrics field"
        rm = data["referral_metrics"]
        
        # Verify referral_metrics structure
        assert "total_referred" in rm, "referral_metrics missing 'total_referred'"
        assert "total_organic" in rm, "referral_metrics missing 'total_organic'"
        assert "total_referrers" in rm, "referral_metrics missing 'total_referrers'"
        assert "referral_rate" in rm, "referral_metrics missing 'referral_rate'"
        assert "invite_conversion" in rm, "referral_metrics missing 'invite_conversion'"
        
        print(f"✓ referral_metrics: rate={rm['referral_rate']}%, referrers={rm['total_referrers']}, referred={rm['total_referred']}")
    
    def test_analytics_returns_top_referrers(self):
        """GET /api/waitlist/admin/analytics returns top_referrers list"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "top_referrers" in data, "Missing top_referrers field"
        assert isinstance(data["top_referrers"], list), "top_referrers should be a list"
        
        # If there are referrers, verify structure and email masking
        if len(data["top_referrers"]) > 0:
            referrer = data["top_referrers"][0]
            assert "email" in referrer, "top_referrer missing 'email'"
            assert "referral_count" in referrer, "top_referrer missing 'referral_count'"
            assert "referral_code" in referrer, "top_referrer missing 'referral_code'"
            # Verify email is masked (contains ***)
            assert "***" in referrer["email"], f"Email should be masked, got: {referrer['email']}"
        
        print(f"✓ top_referrers list returned with {len(data['top_referrers'])} entries")
    
    def test_analytics_returns_score_distribution(self):
        """GET /api/waitlist/admin/analytics returns score_distribution"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "score_distribution" in data, "Missing score_distribution field"
        assert isinstance(data["score_distribution"], list), "score_distribution should be a list"
        
        # Verify structure
        if len(data["score_distribution"]) > 0:
            bucket = data["score_distribution"][0]
            assert "range" in bucket, "score_distribution entry missing 'range'"
            assert "count" in bucket, "score_distribution entry missing 'count'"
        
        print(f"✓ score_distribution returned with {len(data['score_distribution'])} buckets")
    
    def test_analytics_returns_invite_timeline(self):
        """GET /api/waitlist/admin/analytics returns invite_timeline"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=30")
        assert response.status_code == 200
        data = response.json()
        
        assert "invite_timeline" in data, "Missing invite_timeline field"
        assert isinstance(data["invite_timeline"], list), "invite_timeline should be a list"
        
        print(f"✓ invite_timeline returned with {len(data['invite_timeline'])} entries")
    
    # ── Period Parameter Tests ──
    
    def test_analytics_with_7_days_period(self):
        """GET /api/waitlist/admin/analytics?days=7 works"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=7")
        assert response.status_code == 200
        data = response.json()
        
        assert "period_days" in data, "Missing period_days field"
        assert data["period_days"] == 7, f"Expected period_days=7, got {data['period_days']}"
        
        print("✓ Analytics with 7-day period works correctly")
    
    def test_analytics_with_14_days_period(self):
        """GET /api/waitlist/admin/analytics?days=14 works"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=14")
        assert response.status_code == 200
        data = response.json()
        
        assert data["period_days"] == 14, f"Expected period_days=14, got {data['period_days']}"
        print("✓ Analytics with 14-day period works correctly")
    
    def test_analytics_with_90_days_period(self):
        """GET /api/waitlist/admin/analytics?days=90 works"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics?days=90")
        assert response.status_code == 200
        data = response.json()
        
        assert data["period_days"] == 90, f"Expected period_days=90, got {data['period_days']}"
        print("✓ Analytics with 90-day period works correctly")
    
    def test_analytics_default_period_is_30_days(self):
        """GET /api/waitlist/admin/analytics defaults to 30 days"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/analytics")
        assert response.status_code == 200
        data = response.json()
        
        assert data["period_days"] == 30, f"Expected default period_days=30, got {data['period_days']}"
        print("✓ Analytics defaults to 30-day period")
    
    # ── Existing Admin Endpoints Still Work ──
    
    def test_admin_list_still_works(self):
        """GET /api/waitlist/admin/list still works"""
        self._login_admin()
        response = self.session.get(f"{BASE_URL}/api/waitlist/admin/list")
        assert response.status_code == 200
        data = response.json()
        
        assert "entries" in data
        assert "total" in data
        print(f"✓ Admin list endpoint works: {data['total']} total entries")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
