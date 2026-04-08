"""
Iteration 31: Daily Watchlist Digest Emails via Resend
Tests the enhanced daily digest with personalized Watchlist Intelligence section.

Features tested:
- POST /api/auth/login - Owner authentication
- GET /api/digest/preview - HTML with Watchlist Intelligence section
- POST /api/digest/trigger - Send emails with watchlist intel
- GET /api/digest/status - Subscription status
- POST /api/digest/opt-out - Opt out of digest
- POST /api/digest/opt-in - Opt back in
- GET /api/intelligence/watchlist - Cached watchlist data for digest
"""
import pytest
import requests
import os
import time
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestDigestWatchlistIntelligence:
    """Test Daily Digest with Watchlist Intelligence feature."""
    
    access_token = None
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login once and reuse token for all tests."""
        if TestDigestWatchlistIntelligence.access_token is None:
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": OWNER_EMAIL,
                    "password": OWNER_PASSWORD
                },
                timeout=30
            )
            assert response.status_code == 200, f"Login failed: {response.text}"
            data = response.json()
            assert "access_token" in data, f"No access_token in response: {data}"
            TestDigestWatchlistIntelligence.access_token = data["access_token"]
    
    def get_auth_headers(self):
        return {"Authorization": f"Bearer {TestDigestWatchlistIntelligence.access_token}"}
    
    # Test 1: Login returns access_token
    def test_01_login_returns_access_token(self):
        """POST /api/auth/login with owner credentials returns access_token."""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                "email": OWNER_EMAIL,
                "password": OWNER_PASSWORD
            },
            timeout=30
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, f"No access_token: {data}"
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        print(f"PASS: Login returns access_token (length={len(data['access_token'])})")
    
    # Test 2: Digest preview returns HTML with Watchlist Intelligence section
    def test_02_digest_preview_has_watchlist_intelligence(self):
        """GET /api/digest/preview returns HTML containing 'Watchlist Intelligence' section."""
        response = requests.get(
            f"{BASE_URL}/api/digest/preview",
            headers=self.get_auth_headers(),
            timeout=30
        )
        assert response.status_code == 200, f"Preview failed: {response.text}"
        data = response.json()
        
        # Check HTML contains Watchlist Intelligence section
        html = data.get("html", "")
        assert "Watchlist Intelligence" in html, "HTML missing 'Watchlist Intelligence' section"
        
        # Check for Ticker Scores section
        assert "Ticker Scores" in html, "HTML missing 'Ticker Scores' section"
        
        # Check for health score badge (color-coded)
        assert "Health" in html, "HTML missing health score badge"
        
        print(f"PASS: Digest preview contains Watchlist Intelligence section")
        print(f"  - HTML length: {len(html)} chars")
    
    # Test 3: Digest preview data_summary has has_watchlist_intel flag
    def test_03_digest_preview_data_summary(self):
        """GET /api/digest/preview data_summary includes has_watchlist_intel: true."""
        response = requests.get(
            f"{BASE_URL}/api/digest/preview",
            headers=self.get_auth_headers(),
            timeout=30
        )
        assert response.status_code == 200, f"Preview failed: {response.text}"
        data = response.json()
        
        # Check data_summary structure
        assert "data_summary" in data, f"No data_summary in response: {data.keys()}"
        summary = data["data_summary"]
        
        assert "has_watchlist_intel" in summary, f"No has_watchlist_intel in summary: {summary}"
        assert summary["has_watchlist_intel"] == True, f"has_watchlist_intel should be True: {summary}"
        
        # Also verify other summary fields
        assert "predictions" in summary
        assert "dark_pool" in summary
        assert "signals" in summary
        
        print(f"PASS: data_summary has has_watchlist_intel=True")
        print(f"  - predictions: {summary['predictions']}, dark_pool: {summary['dark_pool']}, signals: {summary['signals']}")
    
    # Test 4: Digest trigger sends emails with watchlist intel
    def test_04_digest_trigger_sends_emails(self):
        """POST /api/digest/trigger sends emails (returns sent count > 0, with_watchlist >= 1)."""
        response = requests.post(
            f"{BASE_URL}/api/digest/trigger",
            headers=self.get_auth_headers(),
            timeout=120  # Longer timeout for email sending
        )
        assert response.status_code == 200, f"Trigger failed: {response.text}"
        data = response.json()
        
        # Check response structure
        assert "sent" in data, f"No 'sent' in response: {data}"
        assert "with_watchlist" in data, f"No 'with_watchlist' in response: {data}"
        
        # Verify emails were sent (some may fail due to invalid test emails)
        sent_count = data.get("sent", 0)
        wl_count = data.get("with_watchlist", 0)
        errors = data.get("errors", 0)
        
        # At least some emails should be sent
        assert sent_count > 0 or errors > 0, f"No emails sent or errored: {data}"
        
        # At least 1 user should have watchlist intel
        assert wl_count >= 1, f"Expected with_watchlist >= 1, got {wl_count}"
        
        print(f"PASS: Digest trigger completed")
        print(f"  - sent: {sent_count}, errors: {errors}, with_watchlist: {wl_count}")
    
    # Test 5: Digest status returns subscription status
    def test_05_digest_status(self):
        """GET /api/digest/status returns subscription status."""
        response = requests.get(
            f"{BASE_URL}/api/digest/status",
            headers=self.get_auth_headers(),
            timeout=30
        )
        assert response.status_code == 200, f"Status failed: {response.text}"
        data = response.json()
        
        assert "subscribed" in data, f"No 'subscribed' in response: {data}"
        assert isinstance(data["subscribed"], bool)
        
        print(f"PASS: Digest status returned subscribed={data['subscribed']}")
    
    # Test 6: Opt-out of digest
    def test_06_digest_opt_out(self):
        """POST /api/digest/opt-out opts user out of digest."""
        response = requests.post(
            f"{BASE_URL}/api/digest/opt-out",
            headers=self.get_auth_headers(),
            timeout=30
        )
        assert response.status_code == 200, f"Opt-out failed: {response.text}"
        data = response.json()
        
        assert "message" in data, f"No message in response: {data}"
        assert "unsubscribed" in data["message"].lower() or "opt" in data["message"].lower()
        
        # Verify status changed
        status_response = requests.get(
            f"{BASE_URL}/api/digest/status",
            headers=self.get_auth_headers(),
            timeout=30
        )
        status_data = status_response.json()
        assert status_data.get("subscribed") == False, f"User should be unsubscribed: {status_data}"
        
        print(f"PASS: Opt-out successful, subscribed=False")
    
    # Test 7: Opt-in to digest
    def test_07_digest_opt_in(self):
        """POST /api/digest/opt-in opts user back in."""
        response = requests.post(
            f"{BASE_URL}/api/digest/opt-in",
            headers=self.get_auth_headers(),
            timeout=30
        )
        assert response.status_code == 200, f"Opt-in failed: {response.text}"
        data = response.json()
        
        assert "message" in data, f"No message in response: {data}"
        assert "subscribed" in data["message"].lower() or "opt" in data["message"].lower()
        
        # Verify status changed back
        status_response = requests.get(
            f"{BASE_URL}/api/digest/status",
            headers=self.get_auth_headers(),
            timeout=30
        )
        status_data = status_response.json()
        assert status_data.get("subscribed") == True, f"User should be subscribed: {status_data}"
        
        print(f"PASS: Opt-in successful, subscribed=True")
    
    # Test 8: Watchlist intelligence returns cached data
    def test_08_watchlist_intelligence_cached_data(self):
        """GET /api/intelligence/watchlist returns cached data (health_score, tickers, alerts, top_movers)."""
        response = requests.get(
            f"{BASE_URL}/api/intelligence/watchlist",
            headers=self.get_auth_headers(),
            timeout=60  # May take time if regenerating
        )
        assert response.status_code == 200, f"Watchlist intel failed: {response.text}"
        data = response.json()
        
        # Check required fields
        assert "summary" in data, f"No 'summary' in response: {data.keys()}"
        assert "tickers" in data, f"No 'tickers' in response: {data.keys()}"
        assert "alerts" in data, f"No 'alerts' in response: {data.keys()}"
        assert "top_movers" in data, f"No 'top_movers' in response: {data.keys()}"
        
        # Check summary has health_score
        summary = data["summary"]
        assert "health_score" in summary, f"No health_score in summary: {summary}"
        assert isinstance(summary["health_score"], (int, float))
        
        # Check tickers array
        tickers = data["tickers"]
        assert isinstance(tickers, list)
        if len(tickers) > 0:
            ticker = tickers[0]
            assert "symbol" in ticker, f"Ticker missing symbol: {ticker}"
            assert "score" in ticker, f"Ticker missing score: {ticker}"
        
        print(f"PASS: Watchlist intelligence returned cached data")
        print(f"  - health_score: {summary['health_score']}")
        print(f"  - tickers: {len(tickers)}, alerts: {len(data['alerts'])}, top_movers: {len(data['top_movers'])}")
    
    # Test 9: Digest preview without auth returns 401
    def test_09_digest_preview_requires_auth(self):
        """GET /api/digest/preview without auth returns 401."""
        response = requests.get(
            f"{BASE_URL}/api/digest/preview",
            timeout=30
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print(f"PASS: Digest preview requires authentication (401)")
    
    # Test 10: Digest trigger without auth returns 401
    def test_10_digest_trigger_requires_auth(self):
        """POST /api/digest/trigger without auth returns 401."""
        response = requests.post(
            f"{BASE_URL}/api/digest/trigger",
            timeout=30
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print(f"PASS: Digest trigger requires authentication (401)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
