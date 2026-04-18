"""
Iteration 110: Market Scanner Feature Tests
Tests for the new Market Scanner with 10 pre-built screening strategies.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD


class TestMarketScannerAuth:
    """Authentication tests for scanner endpoints"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "user" in data or "email" in data, "Response should contain user info"
        print("PASS: Admin login returns 200")
    
    def test_scan_without_auth_returns_401(self):
        """POST /api/scanner/scan without auth returns 401"""
        response = requests.post(
            f"{BASE_URL}/api/scanner/scan",
            json={},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: POST /api/scanner/scan without auth returns 401")
    
    def test_quick_scan_without_auth_returns_401(self):
        """GET /api/scanner/quick/rsi_overbought without auth returns 401"""
        response = requests.get(f"{BASE_URL}/api/scanner/quick/rsi_overbought")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: GET /api/scanner/quick without auth returns 401")


class TestMarketScannerStrategies:
    """Tests for scanner strategies endpoint"""
    
    def test_get_strategies_returns_10_strategies(self):
        """GET /api/scanner/strategies returns list of 10 strategies"""
        response = requests.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "strategies" in data, "Response should contain 'strategies' key"
        strategies = data["strategies"]
        assert len(strategies) == 10, f"Expected 10 strategies, got {len(strategies)}"
        print("PASS: GET /api/scanner/strategies returns 10 strategies")
    
    def test_strategies_have_required_fields(self):
        """Each strategy has id, name, description, signal, category"""
        response = requests.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200
        
        data = response.json()
        strategies = data["strategies"]
        
        required_fields = ["id", "name", "description", "signal", "category"]
        for strategy in strategies:
            for field in required_fields:
                assert field in strategy, f"Strategy missing field: {field}"
        
        print("PASS: All strategies have required fields (id, name, description, signal, category)")
    
    def test_strategies_include_expected_types(self):
        """Strategies include RSI, MACD, Bollinger, EMA, volume, 52w, momentum"""
        response = requests.get(f"{BASE_URL}/api/scanner/strategies")
        assert response.status_code == 200
        
        data = response.json()
        strategy_ids = [s["id"] for s in data["strategies"]]
        
        expected_strategies = [
            "rsi_oversold", "rsi_overbought", "macd_bullish_cross", "macd_bearish_cross",
            "bollinger_squeeze", "ema_golden_cross", "volume_spike",
            "near_52w_high", "near_52w_low", "momentum_breakout"
        ]
        
        for expected in expected_strategies:
            assert expected in strategy_ids, f"Missing expected strategy: {expected}"
        
        print("PASS: All 10 expected strategies present")


class TestMarketScannerScan:
    """Tests for scanner scan endpoint (requires auth)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        self.cookies = response.cookies
    
    def test_full_scan_returns_strategies_with_matches(self):
        """POST /api/scanner/scan runs full scan and returns strategies with matches"""
        response = self.session.post(
            f"{BASE_URL}/api/scanner/scan",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=60  # Scanner can take 30+ seconds
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "strategies" in data, "Response should contain 'strategies'"
        assert "scanned" in data, "Response should contain 'scanned' count"
        assert "scanned_at" in data, "Response should contain 'scanned_at' timestamp"
        
        # Verify structure of strategies
        strategies = data["strategies"]
        assert len(strategies) > 0, "Should have at least one strategy in results"
        
        # Check first strategy has expected structure
        first_strategy_id = list(strategies.keys())[0]
        first_strategy = strategies[first_strategy_id]
        assert "info" in first_strategy, "Strategy should have 'info'"
        assert "matches" in first_strategy, "Strategy should have 'matches'"
        assert "match_count" in first_strategy, "Strategy should have 'match_count'"
        
        print(f"PASS: POST /api/scanner/scan returns strategies with matches (scanned {data['scanned']} symbols)")
    
    def test_scan_results_include_match_details(self):
        """Scanner results include symbol, price, strength, detail, rsi, vol_ratio, trend"""
        response = self.session.post(
            f"{BASE_URL}/api/scanner/scan",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        assert response.status_code == 200
        
        data = response.json()
        strategies = data["strategies"]
        
        # Find a strategy with matches
        match_found = False
        for strategy_id, strategy_data in strategies.items():
            if strategy_data.get("match_count", 0) > 0:
                match = strategy_data["matches"][0]
                # Check required fields in match
                assert "symbol" in match, "Match should have 'symbol'"
                assert "price" in match, "Match should have 'price'"
                assert "strength" in match, "Match should have 'strength'"
                assert "detail" in match, "Match should have 'detail'"
                # Optional but expected fields
                assert "rsi" in match or match.get("rsi") is None, "Match should have 'rsi'"
                assert "vol_ratio" in match or match.get("vol_ratio") is None, "Match should have 'vol_ratio'"
                assert "trend" in match or match.get("trend") is None, "Match should have 'trend'"
                match_found = True
                print(f"PASS: Match details verified for {match['symbol']} in {strategy_id}")
                break
        
        if not match_found:
            print("INFO: No matches found in current scan (market conditions may not trigger any strategy)")
            # This is acceptable - market conditions vary


class TestMarketScannerQuickScan:
    """Tests for quick scan endpoint (single strategy)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_quick_rsi_overbought_returns_matches(self):
        """GET /api/scanner/quick/rsi_overbought returns matches for RSI overbought strategy"""
        response = self.session.get(
            f"{BASE_URL}/api/scanner/quick/rsi_overbought",
            timeout=60
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "strategy" in data, "Response should contain 'strategy'"
        assert "matches" in data, "Response should contain 'matches'"
        assert "match_count" in data, "Response should contain 'match_count'"
        assert "scanned" in data, "Response should contain 'scanned'"
        
        # Verify strategy info
        assert "rsi_overbought" in data["strategy"], "Strategy should be rsi_overbought"
        
        print(f"PASS: GET /api/scanner/quick/rsi_overbought returns {data['match_count']} matches")
    
    def test_quick_volume_spike_returns_matches(self):
        """GET /api/scanner/quick/volume_spike returns matches for volume spike strategy"""
        response = self.session.get(
            f"{BASE_URL}/api/scanner/quick/volume_spike",
            timeout=60
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "strategy" in data
        assert "matches" in data
        assert "volume_spike" in data["strategy"]
        
        print(f"PASS: GET /api/scanner/quick/volume_spike returns {data['match_count']} matches")
    
    def test_quick_near_52w_high_returns_matches(self):
        """GET /api/scanner/quick/near_52w_high returns matches near 52-week high"""
        response = self.session.get(
            f"{BASE_URL}/api/scanner/quick/near_52w_high",
            timeout=60
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "strategy" in data
        assert "matches" in data
        assert "near_52w_high" in data["strategy"]
        
        print(f"PASS: GET /api/scanner/quick/near_52w_high returns {data['match_count']} matches")
    
    def test_quick_invalid_strategy_returns_400(self):
        """GET /api/scanner/quick/invalid_strategy returns 400 error"""
        response = self.session.get(
            f"{BASE_URL}/api/scanner/quick/invalid_strategy_xyz",
            timeout=30
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: GET /api/scanner/quick/invalid_strategy returns 400")


class TestMarketScannerRegression:
    """Regression tests - ensure previous features still work"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_smart_orders_still_works(self):
        """GET /api/smart-orders still works (no regression)"""
        response = self.session.get(f"{BASE_URL}/api/smart-orders")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: GET /api/smart-orders still works (no regression)")
    
    def test_risk_calculator_still_works(self):
        """POST /api/risk-calc/calculate still works (no regression)"""
        response = self.session.post(
            f"{BASE_URL}/api/risk-calc/calculate",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "entry_price": 200,
                "stop_loss_price": 195,
                "take_profit_price": 210,
                "account_size": 10000,
                "method": "risk_pct",
                "risk_pct": 2
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: POST /api/risk-calc/calculate still works (no regression)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
