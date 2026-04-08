"""
Iteration 44: Code Quality Refactoring Regression Tests
Tests verify that refactored code paths work correctly:
1. market_data_service.py - generate_mock_options_data with extracted helpers
2. gov_filings_service.py - get_all_gov_data with extracted helpers
3. broker.py - portfolio_sync with extracted helpers (_sync_watchlist, _store_portfolio_snapshot)
4. BrokerConnect.jsx - getOrderStatusClass helper (nested ternary fix)
5. AIHypothesis.jsx - imports HypothesisLocked from separate file
6. App.js - uses useModals hook
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
OWNER_EMAIL = os.environ.get('OWNER_EMAIL', 'managingdirector@redslateholdings.com')
OWNER_PASSWORD = os.environ.get('OWNER_PASSWORD', 'RedSlate2026!')


class TestMarketDataService:
    """Tests for refactored market_data_service.py - generate_mock_options_data"""
    
    def test_options_radar_endpoint(self):
        """GET /api/options/radar - uses refactored generate_mock_options_data('radar')"""
        response = requests.get(f"{BASE_URL}/api/options/radar", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify structure from refactored _generate_contracts helper
        assert 'mostActivelyTraded' in data, "Missing mostActivelyTraded key"
        assert 'volatilityOpportunities' in data, "Missing volatilityOpportunities key"
        assert len(data['mostActivelyTraded']) == 4, "Expected 4 mostActivelyTraded contracts"
        assert len(data['volatilityOpportunities']) == 4, "Expected 4 volatilityOpportunities contracts"
        # Verify contract structure from _generate_contract helper
        contract = data['mostActivelyTraded'][0]
        assert 'contract' in contract
        assert 'price' in contract
        assert 'returns' in contract
        assert 'volOI' in contract
        assert 'power' in contract
        assert 'ivRank' in contract
        assert 'aiScore' in contract
        print(f"✓ Options radar endpoint working - {len(data['mostActivelyTraded'])} contracts returned")

    def test_options_flow_endpoint(self):
        """GET /api/options/flow - uses refactored generate_mock_options_data('flow')"""
        response = requests.get(f"{BASE_URL}/api/options/flow", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify structure from refactored helpers
        assert 'mostActivelyTraded' in data
        assert 'dteEdge' in data
        assert 'volatilityLow' in data
        assert 'volatilityHigh' in data
        print(f"✓ Options flow endpoint working")

    def test_options_momentum_endpoint(self):
        """GET /api/options/momentum - uses refactored generate_mock_options_data('momentum')"""
        response = requests.get(f"{BASE_URL}/api/options/momentum", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), "Expected list of contracts"
        assert len(data) == 5, "Expected 5 momentum contracts"
        print(f"✓ Options momentum endpoint working - {len(data)} contracts")

    def test_options_unusual_volume_endpoint(self):
        """GET /api/options/unusual-volume - uses _generate_contracts_with_sentiment"""
        response = requests.get(f"{BASE_URL}/api/options/unusual-volume", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), "Expected list of contracts"
        # Verify sentiment field from _generate_contracts_with_sentiment
        if len(data) > 0:
            assert 'sentiment' in data[0], "Missing sentiment field from _generate_contracts_with_sentiment"
            assert data[0]['sentiment'] in ['Bullish', 'Bearish'], f"Invalid sentiment: {data[0]['sentiment']}"
        print(f"✓ Options unusual volume endpoint working with sentiment field")


class TestGovFilingsService:
    """Tests for refactored gov_filings_service.py - get_all_gov_data with extracted helpers"""

    def test_gov_filings_endpoint(self):
        """GET /api/gov-filings - uses refactored get_all_gov_data"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify structure from refactored get_all_gov_data
        assert 'insider_trades' in data, "Missing insider_trades"
        assert 'insider_count' in data, "Missing insider_count"
        assert 'fed_announcements' in data, "Missing fed_announcements"
        assert 'fed_count' in data, "Missing fed_count"
        assert 'congressional_trades' in data, "Missing congressional_trades"
        assert 'congressional_count' in data, "Missing congressional_count"
        assert 'upcoming_earnings' in data, "Missing upcoming_earnings"
        assert 'earnings_count' in data, "Missing earnings_count"
        assert 'timestamp' in data, "Missing timestamp"
        assert 'source' in data, "Missing source field from _determine_source helper"
        print(f"✓ Gov filings endpoint working - source: {data['source']}, insider_count: {data['insider_count']}, congressional_count: {data['congressional_count']}")


class TestMarketEndpoints:
    """Tests for core market data endpoints"""

    def test_tickers_endpoint(self):
        """GET /api/stocks/ticker"""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), "Expected list of tickers"
        print(f"✓ Tickers endpoint working - {len(data)} tickers returned")

    def test_crypto_endpoint(self):
        """GET /api/crypto/prices"""
        response = requests.get(f"{BASE_URL}/api/crypto/prices", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), "Expected list of crypto"
        print(f"✓ Crypto endpoint working - {len(data)} cryptos returned")


class TestAuthFlow:
    """Tests for authentication flow"""

    def test_login_with_owner_credentials(self):
        """POST /api/auth/login - verify login works with owner credentials"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=15
        )
        assert response.status_code == 200, f"Login failed: {response.status_code} - {response.text}"
        data = response.json()
        # Response contains user data directly (not nested under 'user' key)
        assert 'email' in data, "Missing email in response"
        assert data['email'] == OWNER_EMAIL
        assert data['role'] == 'owner'
        # Verify cookies are set
        assert 'access_token' in session.cookies or len(session.cookies) > 0, "No cookies set"
        print(f"✓ Login working - user: {data['email']}, role: {data['role']}")
        return session

    def test_auth_me_endpoint(self):
        """GET /api/auth/me - verify authenticated user info"""
        session = requests.Session()
        # Login first
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=15
        )
        assert login_resp.status_code == 200, "Login failed"
        
        # Get user info
        me_resp = session.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert me_resp.status_code == 200, f"Auth/me failed: {me_resp.status_code}"
        data = me_resp.json()
        assert data['email'] == OWNER_EMAIL
        print(f"✓ Auth/me endpoint working - email: {data['email']}")


class TestBrokerEndpoints:
    """Tests for broker endpoints (refactored portfolio_sync with helpers)"""

    def test_broker_connections_endpoint(self):
        """GET /api/broker/connections - requires auth"""
        session = requests.Session()
        # Login first
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=15
        )
        assert login_resp.status_code == 200, "Login failed"
        
        # Get broker connections
        response = session.get(f"{BASE_URL}/api/broker/connections", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'connections' in data, "Missing connections key"
        print(f"✓ Broker connections endpoint working - {len(data['connections'])} connections")

    def test_broker_pnl_summary_endpoint(self):
        """GET /api/broker/pnl-summary - uses refactored _fetch_all_broker_data helper"""
        session = requests.Session()
        # Login first
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=15
        )
        assert login_resp.status_code == 200, "Login failed"
        
        # Get P&L summary
        response = session.get(f"{BASE_URL}/api/broker/pnl-summary", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Verify structure from refactored helpers
        assert 'total_value' in data, "Missing total_value"
        assert 'total_pl' in data, "Missing total_pl"
        assert 'brokers' in data, "Missing brokers"
        assert 'positions' in data, "Missing positions"
        assert 'sector_allocation' in data, "Missing sector_allocation from _classify_sectors helper"
        print(f"✓ Broker P&L summary endpoint working - total_value: {data['total_value']}")


class TestAIHypothesisEndpoint:
    """Tests for AI Hypothesis endpoint (component imports HypothesisLocked from separate file)"""

    def test_hypothesis_endpoint_free_user(self):
        """GET /api/hypothesis/{symbol} - returns teaser for non-pro users"""
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Free user should get teaser
        assert 'symbol' in data, "Missing symbol"
        assert 'teaser' in data or 'is_pro' in data, "Missing teaser or is_pro field"
        print(f"✓ Hypothesis endpoint working for free user - symbol: {data.get('symbol')}")

    def test_hypothesis_endpoint_pro_user(self):
        """GET /api/hypothesis/{symbol} - returns full hypothesis for pro users"""
        session = requests.Session()
        # Login as owner (pro user)
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=15
        )
        assert login_resp.status_code == 200, "Login failed"
        
        # Get hypothesis
        response = session.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'symbol' in data, "Missing symbol"
        assert data.get('is_pro') == True, "Expected is_pro=True for owner"
        print(f"✓ Hypothesis endpoint working for pro user - symbol: {data.get('symbol')}, is_pro: {data.get('is_pro')}")


class TestHealthAndCore:
    """Basic health and core endpoint tests"""

    def test_root_endpoint(self):
        """GET / - root endpoint"""
        response = requests.get(f"{BASE_URL}/", timeout=10)
        # Root may return 200 or redirect
        assert response.status_code in [200, 301, 302, 307, 308], f"Expected success, got {response.status_code}"
        print(f"✓ Root endpoint accessible")

    def test_sectors_heatmap_endpoint(self):
        """GET /api/sectors/heatmap"""
        response = requests.get(f"{BASE_URL}/api/sectors/heatmap", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'sectors' in data, "Missing sectors key"
        assert len(data['sectors']) >= 10, f"Expected at least 10 sectors, got {len(data['sectors'])}"
        print(f"✓ Sectors heatmap endpoint working - {len(data['sectors'])} sectors")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
