"""
Iteration 109: Risk Calculator Feature Tests
Tests for /api/risk-calc/calculate and /api/risk-calc/multi-tp endpoints
Position sizing methods: risk_pct, fixed_dollar, kelly
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestRiskCalculatorAuth:
    """Authentication tests for Risk Calculator endpoints"""
    
    def test_calculate_without_auth_returns_401(self):
        """POST /api/risk-calc/calculate without auth returns 401"""
        response = requests.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_price": 510.0,
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("PASS: POST /api/risk-calc/calculate without auth returns 401")
    
    def test_multi_tp_without_auth_returns_401(self):
        """POST /api/risk-calc/multi-tp without auth returns 401"""
        response = requests.post(f"{BASE_URL}/api/risk-calc/multi-tp", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_prices": [505.0, 510.0],
            "take_profit_pcts": [50, 50],
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        print("PASS: POST /api/risk-calc/multi-tp without auth returns 401")


class TestAdminLogin:
    """Admin login test"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin returns 200"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        # Response contains user data directly (email, role, etc.) or nested under "user"
        email = data.get("email") or data.get("user", {}).get("email")
        assert email == ADMIN_EMAIL, f"Expected email {ADMIN_EMAIL}, got {email}"
        print("PASS: POST /api/auth/login with admin returns 200")


class TestRiskCalculatorRiskPct:
    """Tests for risk_pct position sizing method"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_calculate_risk_pct_buy_returns_position_size(self):
        """POST /api/risk-calc/calculate with risk_pct method returns position size, R:R ratio, max loss/profit"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_price": 510.0,
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify all required fields
        assert "position_size" in data, "Response should contain position_size"
        assert "risk_reward_ratio" in data, "Response should contain risk_reward_ratio"
        assert "max_loss" in data, "Response should contain max_loss"
        assert "max_profit" in data, "Response should contain max_profit"
        assert "entry_price" in data, "Response should contain entry_price"
        assert "stop_loss_price" in data, "Response should contain stop_loss_price"
        assert "take_profit_price" in data, "Response should contain take_profit_price"
        assert "account_value" in data, "Response should contain account_value"
        assert "can_afford" in data, "Response should contain can_afford"
        
        # Verify R:R calculation (TP - Entry) / (Entry - SL) = (510 - 500) / (500 - 495) = 10/5 = 2
        assert data["risk_reward_ratio"] == 2.0, f"Expected R:R 2.0, got {data['risk_reward_ratio']}"
        assert data["sizing_method"] == "risk_pct"
        assert data["position_size"] > 0, "Position size should be positive"
        assert data["max_loss"] > 0, "Max loss should be positive"
        assert data["max_profit"] > 0, "Max profit should be positive"
        
        print(f"PASS: risk_pct method returns position_size={data['position_size']}, R:R={data['risk_reward_ratio']}, max_loss=${data['max_loss']}, max_profit=${data['max_profit']}")
    
    def test_calculate_validates_sl_below_entry_for_buy(self):
        """POST /api/risk-calc/calculate validates SL below entry for buy orders"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 505.0,  # SL above entry - invalid for buy
            "take_profit_price": 510.0,
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        data = response.json()
        assert "stop loss must be below entry" in data.get("detail", "").lower(), f"Expected SL validation error, got: {data}"
        print("PASS: Validates SL must be below entry for buy orders")
    
    def test_calculate_validates_sl_above_entry_for_sell(self):
        """POST /api/risk-calc/calculate validates SL above entry for sell orders"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "sell",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,  # SL below entry - invalid for sell
            "take_profit_price": 490.0,
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        data = response.json()
        assert "stop loss must be above entry" in data.get("detail", "").lower(), f"Expected SL validation error, got: {data}"
        print("PASS: Validates SL must be above entry for sell orders")
    
    def test_calculate_validates_tp_above_entry_for_buy(self):
        """POST /api/risk-calc/calculate validates TP above entry for buy orders"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_price": 498.0,  # TP below entry - invalid for buy
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        data = response.json()
        assert "take profit must be above entry" in data.get("detail", "").lower(), f"Expected TP validation error, got: {data}"
        print("PASS: Validates TP must be above entry for buy orders")


class TestRiskCalculatorFixedDollar:
    """Tests for fixed_dollar position sizing method"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_calculate_fixed_dollar_returns_correct_sizing(self):
        """POST /api/risk-calc/calculate with fixed_dollar method returns correct sizing"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,  # $5 risk per share
            "take_profit_price": 510.0,
            "sizing_method": "fixed_dollar",
            "fixed_dollar_risk": 500.0  # $500 max risk
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert data["sizing_method"] == "fixed_dollar"
        # With $500 risk and $5 risk per share, position size should be ~100 shares
        # (may be floored to 2 decimals)
        assert data["position_size"] > 0, "Position size should be positive"
        assert data["dollar_risk"] == 500.0, f"Expected dollar_risk 500, got {data['dollar_risk']}"
        
        # Verify max_loss is approximately the fixed dollar risk
        assert abs(data["max_loss"] - 500.0) < 10, f"Max loss should be ~$500, got ${data['max_loss']}"
        
        print(f"PASS: fixed_dollar method returns position_size={data['position_size']}, dollar_risk=${data['dollar_risk']}")


class TestRiskCalculatorKelly:
    """Tests for Kelly criterion position sizing method"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_calculate_kelly_returns_kelly_optimal_size(self):
        """POST /api/risk-calc/calculate with kelly method returns Kelly-optimal size"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_price": 510.0,
            "sizing_method": "kelly",
            "win_rate": 60,  # 60% win rate
            "avg_win_loss_ratio": 2.0  # 2:1 avg win/loss
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert data["sizing_method"] == "kelly"
        assert data["position_size"] > 0, "Position size should be positive"
        
        # Kelly formula: (win_rate * wl_ratio - (1 - win_rate)) / wl_ratio
        # = (0.6 * 2 - 0.4) / 2 = (1.2 - 0.4) / 2 = 0.8 / 2 = 0.4 = 40%
        # But capped at 25%, so should use 25%
        # Verify the position is reasonable (not exceeding 25% of account)
        risk_pct = data["risk_pct_of_account"]
        assert risk_pct <= 25.0, f"Kelly should cap at 25%, got {risk_pct}%"
        
        print(f"PASS: kelly method returns position_size={data['position_size']}, risk_pct_of_account={risk_pct}%")
    
    def test_kelly_caps_at_25_percent(self):
        """Kelly criterion caps at 25% max position"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 499.0,  # Very tight SL = high position size
            "take_profit_price": 510.0,
            "sizing_method": "kelly",
            "win_rate": 90,  # Very high win rate
            "avg_win_loss_ratio": 5.0  # Very high ratio
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Even with extreme parameters, Kelly should cap at 25%
        # The dollar_risk should not exceed 25% of account_value
        max_allowed_risk = data["account_value"] * 0.25
        assert data["dollar_risk"] <= max_allowed_risk + 1, f"Kelly should cap at 25% of account, dollar_risk=${data['dollar_risk']}, max_allowed=${max_allowed_risk}"
        
        print(f"PASS: Kelly caps at 25% - dollar_risk=${data['dollar_risk']}, account=${data['account_value']}")


class TestRiskCalculatorAutoFetch:
    """Tests for auto-fetching entry price"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_calculate_auto_fetches_entry_price(self):
        """POST /api/risk-calc/calculate auto-fetches entry price when not provided"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/calculate", json={
            "symbol": "SPY",
            "side": "buy",
            # entry_price not provided - should auto-fetch
            "stop_loss_price": 400.0,  # Low enough to work with any SPY price
            "take_profit_price": 700.0,  # High enough to work with any SPY price
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Entry price should be auto-fetched and present in response
        assert "entry_price" in data, "Response should contain entry_price"
        assert data["entry_price"] > 0, "Auto-fetched entry price should be positive"
        assert data["entry_price"] > data["stop_loss_price"], "Entry should be above SL for buy"
        assert data["entry_price"] < data["take_profit_price"], "Entry should be below TP for buy"
        
        print(f"PASS: Auto-fetched entry_price=${data['entry_price']} for SPY")


class TestRiskCalculatorMultiTP:
    """Tests for multi take-profit endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_multi_tp_returns_weighted_rr(self):
        """POST /api/risk-calc/multi-tp returns weighted R:R across multiple take-profits"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/multi-tp", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 495.0,
            "take_profit_prices": [505.0, 510.0, 520.0],
            "take_profit_pcts": [40, 30, 30],  # 40% at TP1, 30% at TP2, 30% at TP3
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "risk_reward_ratio" in data, "Response should contain risk_reward_ratio"
        assert "take_profit_details" in data, "Response should contain take_profit_details"
        assert "total_projected_profit" in data, "Response should contain total_projected_profit"
        assert "max_loss" in data, "Response should contain max_loss"
        assert "position_size" in data, "Response should contain position_size"
        
        # Verify TP details
        tp_details = data["take_profit_details"]
        assert len(tp_details) == 3, f"Expected 3 TP levels, got {len(tp_details)}"
        
        # Verify each TP has required fields
        for tp in tp_details:
            assert "price" in tp, "TP should have price"
            assert "pct_of_position" in tp, "TP should have pct_of_position"
            assert "qty" in tp, "TP should have qty"
            assert "projected_profit" in tp, "TP should have projected_profit"
        
        # Verify weighted R:R is calculated
        assert data["risk_reward_ratio"] > 0, "Weighted R:R should be positive"
        
        print(f"PASS: multi-tp returns weighted R:R={data['risk_reward_ratio']}, total_profit=${data['total_projected_profit']}, TP levels={len(tp_details)}")
    
    def test_multi_tp_validates_sl_placement(self):
        """POST /api/risk-calc/multi-tp validates SL placement"""
        response = self.session.post(f"{BASE_URL}/api/risk-calc/multi-tp", json={
            "symbol": "SPY",
            "side": "buy",
            "entry_price": 500.0,
            "stop_loss_price": 505.0,  # Invalid - SL above entry for buy
            "take_profit_prices": [510.0, 520.0],
            "take_profit_pcts": [50, 50],
            "sizing_method": "risk_pct",
            "risk_pct": 2.0
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        print("PASS: multi-tp validates SL placement")


class TestSmartOrdersRegression:
    """Regression tests to ensure Smart Orders still works"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
    
    def test_smart_orders_simulate_still_works(self):
        """Smart Orders simulate mode still works (no regression)"""
        response = self.session.post(f"{BASE_URL}/api/smart-orders", json={
            "symbol": "SPY",
            "side": "buy",
            "qty": 10,
            "mode": "simulate",
            "entry_type": "market",
            "stop_loss": {"enabled": True, "price": 495.0},
            "take_profits": [{"price": 510.0, "pct": 100}]
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "preview" in data or "risk_reward_ratio" in data, "Smart Orders simulate should return preview"
        print("PASS: Smart Orders simulate mode still works (no regression)")
    
    def test_get_smart_orders_still_works(self):
        """GET /api/smart-orders still works (no regression)"""
        response = self.session.get(f"{BASE_URL}/api/smart-orders")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, list), "GET /api/smart-orders should return a list"
        print("PASS: GET /api/smart-orders still works (no regression)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
