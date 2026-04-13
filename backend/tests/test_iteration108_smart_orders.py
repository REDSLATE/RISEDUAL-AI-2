"""
Iteration 108: Smart Orders Feature Tests
Tests for the new Smart Orders system with ladder entries, trailing SL/TP, break-even protection, multi-take-profit.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD


class TestSmartOrdersAuth:
    """Authentication tests for Smart Orders endpoints"""
    
    def test_admin_login_returns_200(self):
        """POST /api/auth/login with admin returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "user" in data or "email" in data, "Response should contain user info"
    
    def test_smart_orders_without_auth_returns_401(self):
        """POST /api/smart-orders without auth returns 401"""
        response = requests.post(
            f"{BASE_URL}/api/smart-orders",
            json={"symbol": "AAPL", "side": "buy", "qty": 10, "mode": "paper"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    
    def test_get_smart_orders_without_auth_returns_401(self):
        """GET /api/smart-orders without auth returns 401"""
        response = requests.get(f"{BASE_URL}/api/smart-orders")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestSmartOrdersSimulateMode:
    """Tests for simulate/preview mode"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_simulate_mode_returns_preview(self):
        """POST /api/smart-orders with simulate mode returns preview with risk_reward_ratio, projected_risk, projected_reward"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "mode": "simulate",
                "stop_loss": {"price": 180.0},
                "take_profits": [{"price": 220.0, "pct_of_qty": 100}]
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify simulate mode response fields
        assert data.get("mode") == "simulate", "Mode should be simulate"
        assert "risk_reward_ratio" in data, "Response should contain risk_reward_ratio"
        assert "projected_risk" in data, "Response should contain projected_risk"
        assert "projected_reward" in data, "Response should contain projected_reward"
        assert "avg_entry_price" in data, "Response should contain avg_entry_price"
        assert "current_price" in data, "Response should contain current_price"
    
    def test_simulate_mode_with_crypto(self):
        """POST /api/smart-orders simulate mode works with crypto symbols"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "BTC",
                "side": "buy",
                "qty": 0.1,
                "mode": "simulate",
                "stop_loss": {"price": 90000.0},
                "take_profits": [{"price": 110000.0, "pct_of_qty": 100}]
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data.get("mode") == "simulate"
        assert data.get("symbol") == "BTC"


class TestSmartOrdersLadder:
    """Tests for ladder entry orders"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_ladder_simulate_returns_multiple_legs(self):
        """POST /api/smart-orders with ladder simulate returns multiple legs with correct distribution"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "mode": "simulate",
                "ladder": {
                    "levels": 5,
                    "range_low": 190.0,
                    "range_high": 210.0,
                    "distribution": "equal"
                }
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify ladder legs
        assert "legs" in data, "Response should contain legs"
        assert data.get("leg_count") == 5, f"Expected 5 legs, got {data.get('leg_count')}"
        
        legs = data.get("legs", [])
        assert len(legs) == 5, f"Expected 5 legs, got {len(legs)}"
        
        # Verify price range
        prices = [leg["price"] for leg in legs]
        assert min(prices) >= 190.0, "Lowest price should be >= range_low"
        assert max(prices) <= 210.0, "Highest price should be <= range_high"
        
        # Verify total quantity
        total_qty = sum(leg["qty"] for leg in legs)
        assert abs(total_qty - 100) < 0.01, f"Total qty should be 100, got {total_qty}"
    
    def test_ladder_weighted_bottom_distribution(self):
        """Ladder with weighted_bottom distribution allocates more qty to lower prices"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "mode": "simulate",
                "ladder": {
                    "levels": 3,
                    "range_low": 190.0,
                    "range_high": 210.0,
                    "distribution": "weighted_bottom"
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        legs = data.get("legs", [])
        
        # First leg (lowest price) should have more qty than last leg
        assert legs[0]["qty"] > legs[-1]["qty"], "weighted_bottom should allocate more to lower prices"


class TestSmartOrdersPaperMode:
    """Tests for paper trading mode"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_paper_mode_market_order_creates_and_fills(self):
        """POST /api/smart-orders with paper mode and market order creates and fills order"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 5,
                "mode": "paper",
                "order_type": "market"
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Market orders should be filled immediately
        assert data.get("status") == "filled", f"Market order should be filled, got {data.get('status')}"
        assert data.get("mode") == "paper", "Mode should be paper"
        assert "order_id" in data, "Response should contain order_id"
        assert data.get("filled_qty") == 5, f"Filled qty should be 5, got {data.get('filled_qty')}"


class TestSmartOrdersStopLoss:
    """Tests for stop-loss configuration"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_stop_loss_trailing_config_stored(self):
        """POST /api/smart-orders with stop_loss trailing config is stored correctly"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "mode": "simulate",
                "stop_loss": {
                    "price": 180.0,
                    "trailing": True,
                    "trailing_pct": 3.5,
                    "emergency_price": 170.0
                }
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        sl = data.get("stop_loss")
        assert sl is not None, "Response should contain stop_loss"
        assert sl.get("price") == 180.0, f"SL price should be 180.0, got {sl.get('price')}"
        assert sl.get("trailing") == True, "SL trailing should be True"
        assert sl.get("trailing_pct") == 3.5, f"SL trailing_pct should be 3.5, got {sl.get('trailing_pct')}"
        assert sl.get("emergency_price") == 170.0, f"SL emergency_price should be 170.0"


class TestSmartOrdersBreakEven:
    """Tests for break-even protection"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_break_even_config_stored(self):
        """POST /api/smart-orders with break_even enabled is stored correctly"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "mode": "simulate",
                "stop_loss": {"price": 180.0},
                "take_profits": [{"price": 210.0, "pct_of_qty": 50}, {"price": 220.0, "pct_of_qty": 50}],
                "break_even": {
                    "enabled": True,
                    "trigger_tp_index": 0
                }
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        be = data.get("break_even")
        assert be is not None, "Response should contain break_even"
        assert be.get("enabled") == True, "break_even should be enabled"
        assert be.get("trigger_tp_index") == 0, "trigger_tp_index should be 0"


class TestSmartOrdersTakeProfits:
    """Tests for multi-take-profit configuration"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_multiple_take_profits_returns_correct_chain(self):
        """POST /api/smart-orders with multiple take_profits returns correct TP chain"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "mode": "simulate",
                "take_profits": [
                    {"price": 210.0, "pct_of_qty": 25, "trailing": False},
                    {"price": 220.0, "pct_of_qty": 25, "trailing": True, "trailing_pct": 1.5},
                    {"price": 230.0, "pct_of_qty": 50, "trailing": False}
                ]
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        tps = data.get("take_profits", [])
        assert len(tps) == 3, f"Expected 3 TPs, got {len(tps)}"
        assert data.get("tp_count") == 3, f"tp_count should be 3"
        
        # Verify TP chain structure
        assert tps[0]["price"] == 210.0
        assert tps[0]["pct_of_qty"] == 25
        assert tps[1]["trailing"] == True
        assert tps[1]["trailing_pct"] == 1.5
        assert tps[2]["pct_of_qty"] == 50


class TestSmartOrdersCRUD:
    """Tests for CRUD operations on smart orders"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_get_smart_orders_returns_list(self):
        """GET /api/smart-orders returns list of user orders"""
        response = self.session.get(f"{BASE_URL}/api/smart-orders")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Response should be a list"
    
    def test_create_and_cancel_order(self):
        """DELETE /api/smart-orders/{order_id} cancels an order"""
        # First create an order
        create_resp = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "MSFT",
                "side": "buy",
                "qty": 5,
                "mode": "paper",
                "order_type": "limit",
                "entry_price": 400.0
            }
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        order_data = create_resp.json()
        order_id = order_data.get("order_id")
        assert order_id, "Order should have an order_id"
        
        # Cancel the order
        cancel_resp = self.session.delete(f"{BASE_URL}/api/smart-orders/{order_id}")
        assert cancel_resp.status_code == 200, f"Cancel failed: {cancel_resp.text}"
        cancel_data = cancel_resp.json()
        assert cancel_data.get("status") == "cancelled", f"Status should be cancelled, got {cancel_data.get('status')}"


class TestSmartOrdersLiveMode:
    """Tests for live mode restrictions"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as admin (non-owner)"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_live_mode_restricted_for_non_owner(self):
        """Live mode should be restricted to owner accounts"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "mode": "live"
            }
        )
        # Should return 403 for non-owner
        assert response.status_code == 403, f"Expected 403 for non-owner live mode, got {response.status_code}"


class TestSmartOrdersValidation:
    """Tests for input validation"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_invalid_side_returns_error(self):
        """Invalid side should return error"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "invalid",
                "qty": 10,
                "mode": "simulate"
            }
        )
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"
    
    def test_negative_qty_returns_error(self):
        """Negative quantity should return error"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": -10,
                "mode": "simulate"
            }
        )
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"
    
    def test_invalid_mode_returns_error(self):
        """Invalid mode should return error"""
        response = self.session.post(
            f"{BASE_URL}/api/smart-orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "mode": "invalid_mode"
            }
        )
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
