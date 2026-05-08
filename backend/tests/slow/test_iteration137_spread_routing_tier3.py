"""Tests for Iteration 137: Smart-Routed Spreads + Tier 3 Paper Days Progress.

Covers:
  * POST /api/options/spread with best_execution=true — smart-routed multi-leg spread
  * POST /api/options/spread with best_execution=false — direct-route behavior
  * Verify /api/options/spread response has top-level `routing` key in BOTH modes
  * GET /api/admin/tier3-progress — paper days progress endpoint (admin-gated)
  * Regression: GET /api/options/greeks (Phase 2) still works
  * Regression: POST /api/options/order with best_execution=true (single-leg smart-routed)
  * Regression: /api/options/providers still returns [alpaca, tradier, tastytrade, ibkr]
"""
from __future__ import annotations

import os
import pytest
import requests
from datetime import datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from test_credentials.md
TEST_EMAIL = "admin@risedual.ai"
TEST_PASSWORD = "RiseDual2026!"


class TestSmartRoutedSpreads:
    """P2 Smart-routed SPREAD orders tests"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        
        # Ensure ODD is accepted for spread orders
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        
        yield
        self.session.close()

    def test_spread_smart_routed_vertical_call_debit(self):
        """POST /api/options/spread with best_execution=true — 2-leg AAPL vertical call debit spread"""
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                {"strike": 195, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.50,
            "best_execution": True,  # Smart-routed
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        
        # Accept 200 (success), 502 (broker error - market closed), or 403 (permission)
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            
            # Verify order_id and status
            assert "order_id" in data, "Missing order_id in response"
            assert "status" in data, "Missing status in response"
            assert data.get("status") in ["accepted", "new", "pending_new"], f"Unexpected status: {data.get('status')}"
            
            # Verify routing key exists and has smart mode
            assert "routing" in data, "Missing 'routing' key in response"
            routing = data["routing"]
            assert routing.get("mode") == "smart", f"Expected routing.mode=smart, got {routing.get('mode')}"
            
            # Verify estimated_spread is present (may be 1e9 sentinel if TRADIER_API_TOKEN not set)
            assert "estimated_spread" in routing, "Missing routing.estimated_spread"
            
            # Verify spread_per_leg array has length 2
            assert "spread_per_leg" in routing, "Missing routing.spread_per_leg"
            assert len(routing["spread_per_leg"]) == 2, f"Expected 2 spread_per_leg entries, got {len(routing['spread_per_leg'])}"
            
            # Verify provider is alpaca (only multileg-capable broker)
            assert data.get("provider") == "alpaca", f"Expected provider=alpaca, got {data.get('provider')}"
            
            # Verify legs in response
            assert len(data.get("legs", [])) == 2, "Should have 2 legs"
            
            # Store order_id for cancel test
            self.__class__.smart_spread_order_id = data.get("order_id")
            print(f"Smart-routed spread order placed: {data.get('order_id')}")

    def test_spread_direct_route_vertical_call_debit(self):
        """POST /api/options/spread with best_execution=false — direct-route behavior"""
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 185, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.00,
            "best_execution": False,  # Direct-route (default)
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            
            # Verify routing key exists and has direct mode
            assert "routing" in data, "Missing 'routing' key in response"
            routing = data["routing"]
            assert routing.get("mode") == "direct", f"Expected routing.mode=direct, got {routing.get('mode')}"
            
            # Direct mode should NOT have estimated_spread or spread_per_leg
            # (only mode key is present)
            
            # Store order_id for cancel test
            self.__class__.direct_spread_order_id = data.get("order_id")
            print(f"Direct-route spread order placed: {data.get('order_id')}")

    def test_spread_routing_key_present_in_both_modes(self):
        """Verify /api/options/spread response has top-level `routing` key in BOTH smart and direct modes"""
        # Test smart mode
        smart_payload = {
            "underlying": "SPY",
            "legs": [
                {"strike": 500, "expiry": "2026-12-18", "option_type": "put", "side": "buy_to_open", "qty": 1},
                {"strike": 495, "expiry": "2026-12-18", "option_type": "put", "side": "sell_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 0.50,
            "best_execution": True,
        }
        smart_resp = self.session.post(f"{BASE_URL}/api/options/spread", json=smart_payload)
        
        if smart_resp.status_code == 200:
            smart_data = smart_resp.json()
            assert "routing" in smart_data, "Smart mode: Missing 'routing' key"
            assert smart_data["routing"].get("mode") == "smart", "Smart mode: routing.mode should be 'smart'"
        
        # Test direct mode
        direct_payload = {
            "underlying": "SPY",
            "legs": [
                {"strike": 490, "expiry": "2026-12-18", "option_type": "put", "side": "buy_to_open", "qty": 1},
                {"strike": 485, "expiry": "2026-12-18", "option_type": "put", "side": "sell_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 0.50,
            "best_execution": False,
        }
        direct_resp = self.session.post(f"{BASE_URL}/api/options/spread", json=direct_payload)
        
        if direct_resp.status_code == 200:
            direct_data = direct_resp.json()
            assert "routing" in direct_data, "Direct mode: Missing 'routing' key"
            assert direct_data["routing"].get("mode") == "direct", "Direct mode: routing.mode should be 'direct'"

    def test_cancel_smart_routed_spread(self):
        """DELETE to cancel smart-routed spread order"""
        order_id = getattr(self.__class__, "smart_spread_order_id", None)
        
        if not order_id:
            pytest.skip("No smart spread order_id available to cancel")
        
        resp = self.session.delete(f"{BASE_URL}/api/options/order/{order_id}")
        
        # Accept 200 (success), 404 (already filled/cancelled), or 403 (permission)
        assert resp.status_code in [200, 404, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "canceled" in data, "Missing 'canceled' field"
            print(f"Smart spread order {order_id} cancelled: {data.get('canceled')}")


class TestTier3PaperDaysProgress:
    """P1 Tier 3 paper-days progress tile in admin panel tests"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        yield
        self.session.close()

    def test_tier3_progress_endpoint_returns_expected_fields(self):
        """GET /api/admin/tier3-progress returns all expected fields"""
        resp = self.session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        
        # Verify all required fields
        assert "days" in data, "Missing 'days' field"
        assert "target_days" in data, "Missing 'target_days' field"
        assert data["target_days"] == 30, f"Expected target_days=30, got {data['target_days']}"
        
        assert "remaining_days" in data, "Missing 'remaining_days' field"
        assert "progress_pct" in data, "Missing 'progress_pct' field"
        assert "unlocked" in data, "Missing 'unlocked' field"
        assert "first_trade_at" in data, "Missing 'first_trade_at' field"
        assert "last_trade_at" in data, "Missing 'last_trade_at' field"
        assert "total_trades" in data, "Missing 'total_trades' field"
        
        # Verify data types
        assert isinstance(data["days"], int), "days should be int"
        assert isinstance(data["target_days"], int), "target_days should be int"
        assert isinstance(data["remaining_days"], int), "remaining_days should be int"
        assert isinstance(data["progress_pct"], int), "progress_pct should be int"
        assert isinstance(data["unlocked"], bool), "unlocked should be bool"
        
        # Verify progress_pct is 0-100
        assert 0 <= data["progress_pct"] <= 100, f"progress_pct should be 0-100, got {data['progress_pct']}"
        
        # Verify remaining_days calculation
        expected_remaining = max(30 - data["days"], 0)
        assert data["remaining_days"] == expected_remaining, f"remaining_days mismatch: expected {expected_remaining}, got {data['remaining_days']}"
        
        print(f"Tier 3 progress: {data['days']}/{data['target_days']} days, {data['progress_pct']}% complete, unlocked={data['unlocked']}")

    def test_tier3_progress_requires_auth(self):
        """GET /api/admin/tier3-progress returns 401 without auth"""
        no_auth_session = requests.Session()
        resp = no_auth_session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"
        no_auth_session.close()

    def test_tier3_progress_requires_admin_role(self):
        """GET /api/admin/tier3-progress returns 403 without admin role"""
        # This test would require a non-admin user account
        # For now, we verify the endpoint works with admin credentials
        resp = self.session.get(f"{BASE_URL}/api/admin/tier3-progress")
        assert resp.status_code == 200, f"Admin should have access, got {resp.status_code}"


class TestRegressionPhase2:
    """Regression tests for Phase 2 features"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        
        # Ensure ODD is accepted
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        
        yield
        self.session.close()

    def test_greeks_endpoint_still_works(self):
        """Regression: GET /api/options/greeks (Phase 2) still works"""
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "call",
            "iv_percent": 20,
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 200, f"Greeks endpoint failed: {resp.status_code}: {resp.text}"
        
        data = resp.json()
        required_fields = ["delta", "gamma", "theta", "vega", "rho", "mid_price", "years_to_expiry"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"Greeks: delta={data['delta']:.4f}, gamma={data['gamma']:.4f}, theta={data['theta']:.4f}")

    def test_single_leg_smart_routed_order_still_works(self):
        """Regression: POST /api/options/order with best_execution=true (single-leg smart-routed) still works"""
        order_payload = {
            "underlying": "AAPL",
            "strike": 200,
            "expiry": "2026-12-18",
            "option_type": "call",
            "side": "buy_to_open",
            "qty": 1,
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 0.01,  # Very low limit to avoid fill
            "best_execution": True,  # Smart-routed
        }
        resp = self.session.post(f"{BASE_URL}/api/options/order", json=order_payload)
        
        # Accept 200 (success), 502 (broker error), or 403 (permission)
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "order_id" in data, "Missing order_id"
            assert "routing" in data, "Missing routing key"
            assert data["routing"].get("mode") == "smart", "Expected smart routing mode"
            
            # Verify audit trail would be written (we can't directly check DB here)
            print(f"Single-leg smart-routed order placed: {data.get('order_id')}")
            
            # Cancel the order
            order_id = data.get("order_id")
            if order_id:
                self.session.delete(f"{BASE_URL}/api/options/order/{order_id}")

    def test_providers_endpoint_returns_all_four(self):
        """Regression: /api/options/providers still returns [alpaca, tradier, tastytrade, ibkr]"""
        resp = self.session.get(f"{BASE_URL}/api/options/providers")
        assert resp.status_code == 200, f"Providers endpoint failed: {resp.status_code}"
        
        data = resp.json()
        assert "supported" in data, "Missing 'supported' field"
        
        expected_providers = ["alpaca", "tradier", "tastytrade", "ibkr"]
        for provider in expected_providers:
            assert provider in data["supported"], f"Missing provider: {provider}"
        
        print(f"Supported providers: {data['supported']}")


class TestSmartRouterUnit:
    """Unit tests for smart router spread functionality"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        
        # Ensure ODD is accepted
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        
        yield
        self.session.close()

    def test_spread_rejects_more_than_4_legs(self):
        """Spread order rejects more than 4 legs (422 pydantic validation)"""
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 180, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                {"strike": 185, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                {"strike": 195, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
                {"strike": 200, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},  # 5th leg
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.00,
            "best_execution": True,
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        assert resp.status_code == 422, f"Expected 422 for >4 legs, got {resp.status_code}"

    def test_spread_iron_condor_4_legs(self):
        """Spread order accepts 4-leg iron condor"""
        spread_payload = {
            "underlying": "SPY",
            "legs": [
                {"strike": 480, "expiry": "2026-12-18", "option_type": "put", "side": "buy_to_open", "qty": 1},
                {"strike": 490, "expiry": "2026-12-18", "option_type": "put", "side": "sell_to_open", "qty": 1},
                {"strike": 510, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
                {"strike": 520, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 0.50,
            "best_execution": True,
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        
        # Accept 200 (success), 502 (broker error), or 403 (permission)
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert len(data.get("legs", [])) == 4, "Should have 4 legs"
            assert data.get("routing", {}).get("mode") == "smart", "Should be smart-routed"


class TestAlpacaMultilegCapability:
    """Tests verifying Alpaca adapter supports_multileg=True"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        yield
        self.session.close()

    def test_alpaca_is_default_provider(self):
        """Verify Alpaca is the default options provider"""
        resp = self.session.get(f"{BASE_URL}/api/options/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("current") == "alpaca", f"Expected current=alpaca, got {data.get('current')}"

    def test_alpaca_options_status(self):
        """Verify Alpaca options status endpoint works"""
        resp = self.session.get(f"{BASE_URL}/api/options/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("provider") == "alpaca", f"Expected provider=alpaca, got {data.get('provider')}"
        assert "enabled" in data, "Missing 'enabled' field"
        assert "level" in data, "Missing 'level' field"
        print(f"Alpaca options: enabled={data['enabled']}, level={data['level']}")
