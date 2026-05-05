"""
Comprehensive Pre-Deployment Tests for RISEDUAL AI Trading Platform
Tests all critical flows: auth, market data, AI hypothesis, journal, referrals, admin, etc.
"""
import pytest
import requests
import os
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "http://localhost:8001"

# Test credentials from environment (never hardcode secrets)
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", OWNER_EMAIL)
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
FREE_USER_EMAIL = os.environ.get("FREE_USER_EMAIL", "freeuser_test@test.com")
FREE_USER_PASSWORD = os.environ.get("FREE_USER_PASSWORD", "")


class TestAuthEndpoints:
    """Authentication endpoint tests"""
    
    def test_owner_login_returns_jwt_with_correct_role(self):
        """Owner login should return JWT with role=owner and subscription=pro"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["role"] == "owner"
        assert data["subscription_status"] == "pro"
        assert data["email"] == OWNER_EMAIL
    
    def test_free_user_login_returns_jwt_with_free_subscription(self):
        """Free user login should return JWT with subscription=free"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["subscription_status"] == "free"
        assert data["role"] == "user"
    
    def test_auth_me_returns_correct_profile(self):
        """GET /api/auth/me should return correct user profile"""
        # Login first
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_resp.json()["access_token"]
        
        # Get profile
        response = requests.get(f"{BASE_URL}/api/auth/me", headers={
            "Authorization": f"Bearer {token}"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == OWNER_EMAIL
        assert data["role"] == "owner"
        assert data["subscription_status"] == "pro"


class TestMarketDataEndpoints:
    """Market data endpoint tests (Alpha Vantage, Finnhub)"""
    
    def test_stocks_ticker_returns_data(self):
        """GET /api/stocks/ticker should return stock data"""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # Check structure
        first = data[0]
        assert "symbol" in first
        assert "price" in first
    
    def test_crypto_prices_returns_data(self):
        """GET /api/crypto/prices should return crypto prices"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # Check for BTC
        symbols = [c["symbol"] for c in data]
        assert "BTC" in symbols or "ETH" in symbols


class TestGovFilingsEndpoint:
    """Government filings endpoint tests (Finnhub integration)"""
    
    def test_gov_filings_returns_finnhub_data(self):
        """GET /api/gov-filings should return Finnhub data with insider trades"""
        response = requests.get(f"{BASE_URL}/api/gov-filings")
        assert response.status_code == 200
        data = response.json()
        
        # Check for insider trades
        assert "insider_trades" in data or "insider_count" in data
        
        # Verify insider_count > 0
        insider_count = data.get("insider_count", len(data.get("insider_trades", [])))
        assert insider_count > 0, "Expected insider_count > 0 from Finnhub"


class TestAIHypothesisEndpoints:
    """AI Hypothesis endpoint tests (multi-model)"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    @pytest.fixture
    def free_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_pro_user_gpt52_returns_full_hypothesis(self, owner_token):
        """Pro user should get full hypothesis with GPT-5.2"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=90
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("is_pro")
        assert "verdict" in data
        assert data["verdict"] in ["BUY", "SELL", "HOLD", "STRONG_BUY", "STRONG_SELL"]
    
    def test_free_user_gpt52_returns_teaser(self, free_token):
        """Free user should get teaser response with GPT-5.2"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=gpt-5.2",
            headers={"Authorization": f"Bearer {free_token}"},
            timeout=90
        )
        assert response.status_code == 200
        data = response.json()
        assert not data.get("is_pro")
        assert "teaser" in data
        assert data["teaser"]["verdict"] == "LOCKED"
    
    def test_free_user_claude_returns_403(self, free_token):
        """Free user should get 403 for premium models"""
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL?model=claude-sonnet-4.5",
            headers={"Authorization": f"Bearer {free_token}"},
            timeout=30
        )
        assert response.status_code == 403
        assert "Premium AI models require a Pro subscription" in response.json()["detail"]


class TestTradingJournalEndpoints:
    """Trading journal endpoint tests"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_journal_trades_returns_trades(self, owner_token):
        """GET /api/journal/trades should return user's trades"""
        response = requests.get(
            f"{BASE_URL}/api/journal/trades",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "trades" in data
        assert isinstance(data["trades"], list)
    
    def test_journal_analytics_returns_stats(self, owner_token):
        """GET /api/journal/analytics should return win_rate, total_pnl, by_ticker"""
        response = requests.get(
            f"{BASE_URL}/api/journal/analytics",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "win_rate" in data
        assert "total_pnl" in data or "total_trades" in data
        assert "by_ticker" in data


class TestWorkspaceEndpoints:
    """Workspace endpoint tests"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_watchlist_returns_tickers(self, owner_token):
        """GET /api/workspace/watchlist should return tickers"""
        response = requests.get(
            f"{BASE_URL}/api/workspace/watchlist",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "tickers" in data
        assert isinstance(data["tickers"], list)


class TestReferralEndpoints:
    """Referral endpoint tests"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_referral_info_returns_code(self, owner_token):
        """GET /api/referral/info should return referral code and stats"""
        response = requests.get(
            f"{BASE_URL}/api/referral/info",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "code" in data
        assert len(data["code"]) == 8
        assert "total_referrals" in data
        assert "rewards_remaining" in data
    
    def test_referral_leaderboard_returns_data(self):
        """GET /api/referral/leaderboard should return leaderboard"""
        response = requests.get(f"{BASE_URL}/api/referral/leaderboard")
        assert response.status_code == 200
        data = response.json()
        assert "leaderboard" in data
        assert "total_participants" in data


class TestAdminEndpoints:
    """Admin endpoint tests (owner only)"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    @pytest.fixture
    def free_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": FREE_USER_EMAIL,
            "password": FREE_USER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_admin_users_returns_list_for_owner(self, owner_token):
        """GET /api/auth/admin/users should return user list for owner"""
        response = requests.get(
            f"{BASE_URL}/api/auth/admin/users",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert isinstance(data["users"], list)
        assert len(data["users"]) > 0
    
    def test_admin_users_forbidden_for_free_user(self, free_token):
        """GET /api/auth/admin/users should return 403 for non-admin"""
        response = requests.get(
            f"{BASE_URL}/api/auth/admin/users",
            headers={"Authorization": f"Bearer {free_token}"}
        )
        assert response.status_code == 403


class TestPushNotificationEndpoints:
    """Push notification endpoint tests"""
    
    @pytest.fixture
    def owner_token(self):
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        return resp.json()["access_token"]
    
    def test_push_status_returns_subscription_status(self, owner_token):
        """GET /api/push/status should return subscription status"""
        response = requests.get(
            f"{BASE_URL}/api/push/status",
            headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "subscribed" in data
        assert "is_pro" in data


class TestPromoEndpoints:
    """Promo campaign endpoint tests"""
    
    def test_promo_active_returns_promo(self):
        """GET /api/promo/active should return active promos"""
        response = requests.get(f"{BASE_URL}/api/promo/active")
        assert response.status_code == 200
        data = response.json()
        # May or may not have active promo
        assert "promo" in data or data == {}


class TestMiscEndpoints:
    """Miscellaneous endpoint tests"""
    
    def test_health_check(self):
        """GET /api/ should return 200"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
    
    def test_world_events_returns_data(self):
        """GET /api/world-events should return events"""
        response = requests.get(f"{BASE_URL}/api/world-events")
        assert response.status_code == 200
        data = response.json()
        # Response can be list, or dict with events/affected_sectors/high_impact_events
        assert isinstance(data, (list, dict))
    
    def test_dark_pool_returns_data(self):
        """GET /api/dark-pool should return dark pool data"""
        response = requests.get(f"{BASE_URL}/api/dark-pool")
        assert response.status_code == 200
    
    def test_market_prediction_returns_data(self):
        """GET /api/market/prediction should return prediction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction")
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
