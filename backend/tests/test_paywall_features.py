"""
Test paywall features for RISEDUAL AI trading platform.
Tests: Chat rate limits, Watchlist caps, Portfolio Analyzer, Market Signals, Export
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestAuthLogin:
    """Test authentication endpoints"""
    
    def test_owner_login_returns_pro_status(self):
        """Owner account should have pro subscription"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "No access_token in response"
        assert data.get("subscription_status") == "pro", f"Expected pro, got {data.get('subscription_status')}"
        assert data.get("role") == "owner", f"Expected owner role, got {data.get('role')}"
    
    def test_admin_login_returns_pro_status(self):
        """Admin account should have pro subscription"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data.get("subscription_status") == "pro"


class TestChatRateLimit:
    """Test AI Chat rate limit endpoint"""
    
    def test_chat_limit_unauthenticated(self):
        """Unauthenticated users get default free limit"""
        response = requests.get(f"{BASE_URL}/api/chat/limit")
        assert response.status_code == 200
        data = response.json()
        assert data.get("limit") == 5, f"Expected limit 5, got {data.get('limit')}"
        assert data.get("remaining") == 5
        assert data.get("is_pro") == False
    
    def test_chat_limit_pro_user(self):
        """Pro users get unlimited (-1)"""
        # Login as owner (pro)
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_res.json().get("access_token")
        
        response = requests.get(
            f"{BASE_URL}/api/chat/limit",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("limit") == -1, f"Pro user should have unlimited (-1), got {data.get('limit')}"
        assert data.get("is_pro") == True


class TestWatchlistCap:
    """Test watchlist cap for free users"""
    
    @pytest.fixture
    def free_user_token(self):
        """Create/login a free test user"""
        test_email = f"TEST_free_user_{os.urandom(4).hex()}@test.com"
        # Register new user (will be free by default)
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Free User"
        })
        if reg_res.status_code == 200:
            return reg_res.json().get("access_token")
        # If registration fails (user exists), try login
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": test_email,
            "password": "TestPass123!"
        })
        return login_res.json().get("access_token")
    
    def test_watchlist_add_exceeds_cap_free_user(self, free_user_token):
        """Free user should get 403 after adding 3 tickers"""
        if not free_user_token:
            pytest.skip("Could not create free user")
        
        headers = {"Authorization": f"Bearer {free_user_token}"}
        
        # Add 3 tickers (should succeed)
        for i, ticker in enumerate(["AAPL", "MSFT", "GOOGL"]):
            res = requests.post(
                f"{BASE_URL}/api/workspace/watchlist/add",
                json={"ticker": ticker},
                headers=headers
            )
            # First 3 should succeed (200) or already exist
            assert res.status_code in [200, 400], f"Ticker {i+1} failed: {res.text}"
        
        # 4th ticker should fail with 403
        res = requests.post(
            f"{BASE_URL}/api/workspace/watchlist/add",
            json={"ticker": "NVDA"},
            headers=headers
        )
        assert res.status_code == 403, f"Expected 403 for 4th ticker, got {res.status_code}: {res.text}"
        assert "limited to 3" in res.json().get("detail", "").lower() or "free" in res.json().get("detail", "").lower()
    
    def test_watchlist_add_pro_user_no_cap(self):
        """Pro user should have no watchlist cap"""
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_res.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        
        # Pro user can add many tickers
        for ticker in ["TEST1", "TEST2", "TEST3", "TEST4", "TEST5"]:
            res = requests.post(
                f"{BASE_URL}/api/workspace/watchlist/add",
                json={"ticker": ticker},
                headers=headers
            )
            assert res.status_code == 200, f"Pro user failed to add {ticker}: {res.text}"


class TestMarketSignals:
    """Test Market Signals Pro-only endpoint"""
    
    def test_signals_unauthenticated(self):
        """Unauthenticated request should return 401"""
        response = requests.get(f"{BASE_URL}/api/signals")
        assert response.status_code == 401
    
    def test_signals_free_user_empty(self):
        """Free user should get empty signals with is_pro=False"""
        # Register a new free user
        test_email = f"TEST_signals_free_{os.urandom(4).hex()}@test.com"
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Signals Free"
        })
        if reg_res.status_code != 200:
            pytest.skip("Could not create free user")
        
        token = reg_res.json().get("access_token")
        response = requests.get(
            f"{BASE_URL}/api/signals",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("signals") == [], f"Free user should get empty signals"
        assert data.get("is_pro") == False
    
    def test_signals_pro_user(self):
        """Pro user should get signals with is_pro=True"""
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_res.json().get("access_token")
        
        response = requests.get(
            f"{BASE_URL}/api/signals",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("is_pro") == True


class TestPortfolioAnalyzer:
    """Test Portfolio Analyzer Pro-only endpoint"""
    
    def test_portfolio_analyze_unauthenticated(self):
        """Unauthenticated request should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/portfolio/analyze",
            json={"holdings": [{"ticker": "AAPL", "shares": 10, "avg_price": 150}]}
        )
        assert response.status_code == 401
    
    def test_portfolio_analyze_free_user_403(self):
        """Free user should get 403"""
        test_email = f"TEST_portfolio_free_{os.urandom(4).hex()}@test.com"
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Portfolio Free"
        })
        if reg_res.status_code != 200:
            pytest.skip("Could not create free user")
        
        token = reg_res.json().get("access_token")
        response = requests.post(
            f"{BASE_URL}/api/portfolio/analyze",
            json={"holdings": [{"ticker": "AAPL", "shares": 10, "avg_price": 150}]},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        assert "pro feature" in response.json().get("detail", "").lower()
    
    def test_portfolio_analyze_pro_user_success(self):
        """Pro user should get analysis result"""
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_res.json().get("access_token")
        
        response = requests.post(
            f"{BASE_URL}/api/portfolio/analyze",
            json={"holdings": [
                {"ticker": "AAPL", "shares": 10, "avg_price": 150},
                {"ticker": "MSFT", "shares": 5, "avg_price": 300}
            ]},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60  # AI analysis may take time
        )
        assert response.status_code == 200, f"Pro user analysis failed: {response.text}"
        data = response.json()
        assert "health_score" in data or "summary" in data, f"Missing analysis fields: {data.keys()}"


class TestHypothesisPaywall:
    """Test AI Hypothesis paywall"""
    
    def test_hypothesis_free_user_locked(self):
        """Free user should get locked/teaser response"""
        test_email = f"TEST_hypothesis_free_{os.urandom(4).hex()}@test.com"
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Hypothesis Free"
        })
        if reg_res.status_code != 200:
            pytest.skip("Could not create free user")
        
        token = reg_res.json().get("access_token")
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("is_pro") == False, f"Free user should have is_pro=False"
        assert "teaser" in data, f"Free user should get teaser: {data.keys()}"
        assert data.get("teaser", {}).get("verdict") == "LOCKED"
    
    def test_hypothesis_pro_user_full(self):
        """Pro user should get full hypothesis"""
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD
        })
        token = login_res.json().get("access_token")
        
        response = requests.get(
            f"{BASE_URL}/api/hypothesis/AAPL",
            headers={"Authorization": f"Bearer {token}"},
            timeout=90  # AI may take time
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("is_pro") == True, f"Pro user should have is_pro=True"
        assert "verdict" in data, f"Pro user should get verdict: {data.keys()}"
        assert data.get("verdict") in ["BUY", "SELL", "HOLD"], f"Invalid verdict: {data.get('verdict')}"


class TestExportHypothesis:
    """Test PDF/text export Pro-only endpoint"""
    
    def test_export_unauthenticated(self):
        """Unauthenticated request should return 401"""
        response = requests.get(f"{BASE_URL}/api/export/hypothesis/AAPL")
        assert response.status_code == 401
    
    def test_export_free_user_403(self):
        """Free user should get 403"""
        test_email = f"TEST_export_free_{os.urandom(4).hex()}@test.com"
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "name": "Test Export Free"
        })
        if reg_res.status_code != 200:
            pytest.skip("Could not create free user")
        
        token = reg_res.json().get("access_token")
        response = requests.get(
            f"{BASE_URL}/api/export/hypothesis/AAPL",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        assert "pro feature" in response.json().get("detail", "").lower()


class TestDarkPoolEndpoint:
    """Test Dark Pool data endpoint (public but blur wall on frontend)"""
    
    def test_dark_pool_returns_data(self):
        """Dark pool endpoint should return data (blur is frontend-only)"""
        response = requests.get(f"{BASE_URL}/api/dark-pool")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
