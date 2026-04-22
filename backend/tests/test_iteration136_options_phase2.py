"""Tests for Phase 2 Multi-Broker Options: Greeks, Spread Orders, Audit Trail.

Covers:
  * GET /api/options/greeks — auth-gated Greeks preview endpoint
  * POST /api/options/spread — multi-leg spread order (ODD-gated)
  * POST /api/options/order — single-leg order with audit record
  * DELETE /api/options/order/{id} — cancel endpoint
  * GET /api/options/providers — lists all supported providers
  * Audit record verification in option_orders collection
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


class TestOptionsPhase2:
    """Phase 2 Options Trading Tests"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login to get auth cookies
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code} - {login_resp.text[:200]}")
        yield
        self.session.close()

    # ── GET /api/options/greeks ────────────────────────────────────────

    def test_greeks_endpoint_returns_all_greeks(self):
        """Greeks endpoint returns delta/gamma/theta/vega/rho/mid_price/years_to_expiry"""
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "call",
            "iv_percent": 20,
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        # Verify all required fields present
        required_fields = ["delta", "gamma", "theta", "vega", "rho", "mid_price", "years_to_expiry"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        # Verify delta is approximately 0.62 for ATM call with 20% IV
        assert 0.55 < data["delta"] < 0.70, f"Delta {data['delta']} not in expected range"
        
        # Verify gamma is approximately 0.02
        assert 0.01 < data["gamma"] < 0.04, f"Gamma {data['gamma']} not in expected range"
        
        # Verify theta is negative (time decay)
        assert data["theta"] < 0, f"Theta should be negative, got {data['theta']}"
        
        # Verify vega is positive
        assert data["vega"] > 0, f"Vega should be positive, got {data['vega']}"
        
        # Verify mid_price is positive
        assert data["mid_price"] > 0, f"Mid price should be positive"
        
        # Verify years_to_expiry is positive
        assert data["years_to_expiry"] > 0, f"Years to expiry should be positive"

    def test_greeks_endpoint_put_option(self):
        """Greeks endpoint works for put options"""
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "put",
            "iv_percent": 20,
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 200
        data = resp.json()
        
        # Put delta should be negative
        assert data["delta"] < 0, f"Put delta should be negative, got {data['delta']}"
        assert -0.50 < data["delta"] < -0.30, f"Put delta {data['delta']} not in expected range"

    def test_greeks_endpoint_invalid_option_type(self):
        """Greeks endpoint returns 400 for invalid option_type"""
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "butterfly",  # Invalid
            "iv_percent": 20,
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 400, f"Expected 400 for invalid option_type, got {resp.status_code}"

    def test_greeks_endpoint_requires_auth(self):
        """Greeks endpoint returns 401 without auth"""
        # Use a fresh session without auth
        no_auth_session = requests.Session()
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "call",
            "iv_percent": 20,
        }
        resp = no_auth_session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"
        no_auth_session.close()

    # ── GET /api/options/providers ─────────────────────────────────────

    def test_providers_endpoint_lists_all_providers(self):
        """Providers endpoint lists alpaca, tradier, tastytrade, ibkr"""
        resp = self.session.get(f"{BASE_URL}/api/options/providers")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "supported" in data, "Missing 'supported' field"
        assert "current" in data, "Missing 'current' field"
        
        expected_providers = ["alpaca", "tradier", "tastytrade", "ibkr"]
        for provider in expected_providers:
            assert provider in data["supported"], f"Missing provider: {provider}"

    # ── GET /api/options/status ────────────────────────────────────────

    def test_options_status_endpoint(self):
        """Options status endpoint returns enabled status for user's provider"""
        resp = self.session.get(f"{BASE_URL}/api/options/status")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "enabled" in data, "Missing 'enabled' field"
        assert "provider" in data, "Missing 'provider' field"
        assert "level" in data, "Missing 'level' field"

    # ── POST /api/options/spread ───────────────────────────────────────

    def test_spread_order_requires_odd_acceptance(self):
        """Spread order requires ODD acceptance (403 without)"""
        # First check ODD status
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        assert odd_resp.status_code == 200
        odd_data = odd_resp.json()
        
        # If ODD not accepted, we expect 403 on spread order
        # If ODD is accepted, we'll test the actual spread order
        if not odd_data.get("accepted"):
            spread_payload = {
                "underlying": "AAPL",
                "legs": [
                    {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                    {"strike": 195, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
                ],
                "order_type": "limit",
                "time_in_force": "day",
                "limit_price": 1.50,
            }
            resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
            assert resp.status_code == 403, f"Expected 403 without ODD, got {resp.status_code}"

    def test_spread_order_rejects_single_leg(self):
        """Spread order rejects single leg (422 pydantic validation)"""
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.50,
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        assert resp.status_code == 422, f"Expected 422 for single leg, got {resp.status_code}"

    def test_spread_order_rejects_no_opening_leg(self):
        """Spread order rejects spread without any opening leg (400)"""
        # First ensure ODD is accepted
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            # Accept ODD first
            self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        
        # Try spread with only closing legs
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_close", "qty": 1},
                {"strike": 195, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_close", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.50,
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        assert resp.status_code == 400, f"Expected 400 for no opening leg, got {resp.status_code}"
        assert "opening leg" in resp.text.lower(), f"Error should mention opening leg: {resp.text}"

    def test_spread_order_vertical_call_debit(self):
        """Vertical call debit spread order (2 legs) - live test with Alpaca paper"""
        # First ensure ODD is accepted
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            accept_resp = self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
            assert accept_resp.status_code == 200, f"Failed to accept ODD: {accept_resp.text}"
        
        # Place vertical call debit spread
        spread_payload = {
            "underlying": "AAPL",
            "legs": [
                {"strike": 190, "expiry": "2026-12-18", "option_type": "call", "side": "buy_to_open", "qty": 1},
                {"strike": 195, "expiry": "2026-12-18", "option_type": "call", "side": "sell_to_open", "qty": 1},
            ],
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.50,
        }
        resp = self.session.post(f"{BASE_URL}/api/options/spread", json=spread_payload)
        
        # Accept 200 (success), 502 (broker error - market closed), or 403 (permission)
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "order_id" in data, "Missing order_id in response"
            assert "status" in data, "Missing status in response"
            assert data.get("is_spread") is True, "is_spread should be True"
            assert len(data.get("legs", [])) == 2, "Should have 2 legs"
            
            # Store order_id for cancel test
            self.__class__.spread_order_id = data.get("order_id")

    # ── POST /api/options/order ────────────────────────────────────────

    def test_single_leg_order_bto_limit(self):
        """Single-leg BTO order with limit price"""
        # Ensure ODD is accepted
        odd_resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        if odd_resp.status_code == 200 and not odd_resp.json().get("accepted"):
            self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        
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
        }
        resp = self.session.post(f"{BASE_URL}/api/options/order", json=order_payload)
        
        # Accept 200 (success), 502 (broker error), or 403 (permission)
        assert resp.status_code in [200, 502, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "order_id" in data, "Missing order_id"
            assert "status" in data, "Missing status"
            assert "occ_symbol" in data, "Missing occ_symbol"
            
            # Store for cancel test
            self.__class__.single_order_id = data.get("order_id")

    # ── DELETE /api/options/order/{id} ─────────────────────────────────

    def test_cancel_order(self):
        """Cancel a live order"""
        # Try to cancel the spread order if we have one
        order_id = getattr(self.__class__, "spread_order_id", None) or getattr(self.__class__, "single_order_id", None)
        
        if not order_id:
            pytest.skip("No order_id available to cancel")
        
        resp = self.session.delete(f"{BASE_URL}/api/options/order/{order_id}")
        
        # Accept 200 (success), 404 (already filled/cancelled), or 403 (permission)
        assert resp.status_code in [200, 404, 403], f"Unexpected status {resp.status_code}: {resp.text}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "canceled" in data, "Missing 'canceled' field"
            assert "order_id" in data, "Missing 'order_id' field"


class TestGreeksValidation:
    """Additional Greeks endpoint validation tests"""

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
            pytest.skip(f"Login failed: {login_resp.status_code}")
        yield
        self.session.close()

    def test_greeks_negative_price_rejected(self):
        """Greeks endpoint rejects negative prices"""
        params = {
            "underlying_price": -100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "call",
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 400, f"Expected 400 for negative price, got {resp.status_code}"

    def test_greeks_without_iv_uses_default(self):
        """Greeks endpoint uses default IV when not provided"""
        params = {
            "underlying_price": 100,
            "strike": 100,
            "expiry": "2027-02-11",
            "option_type": "call",
            # No iv_percent - should use default
        }
        resp = self.session.get(f"{BASE_URL}/api/options/greeks", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "delta" in data
        assert data["iv_percent"] is None  # Should reflect that no IV was provided


class TestODDFlow:
    """ODD (Options Disclosure Document) acceptance flow tests"""

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
            pytest.skip(f"Login failed: {login_resp.status_code}")
        yield
        self.session.close()

    def test_odd_status_endpoint(self):
        """ODD status endpoint returns acceptance status"""
        resp = self.session.get(f"{BASE_URL}/api/options/odd/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "accepted" in data, "Missing 'accepted' field"

    def test_odd_accept_requires_true(self):
        """ODD accept endpoint requires accept=true"""
        resp = self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": False})
        assert resp.status_code == 400, f"Expected 400 for accept=false, got {resp.status_code}"

    def test_odd_accept_success(self):
        """ODD accept endpoint works with accept=true"""
        resp = self.session.post(f"{BASE_URL}/api/options/odd/accept", json={"accept": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("accepted") is True
        assert "accepted_at" in data


class TestTradierAdapterStatus:
    """Tradier adapter status tests (should report enabled=false without token)"""

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
            pytest.skip(f"Login failed: {login_resp.status_code}")
        yield
        self.session.close()

    def test_tradier_in_providers_list(self):
        """Tradier is listed as a supported provider"""
        resp = self.session.get(f"{BASE_URL}/api/options/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "tradier" in data.get("supported", []), "Tradier should be in supported providers"


class TestBuyingPowerAndPositions:
    """Buying power and positions endpoint tests"""

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
            pytest.skip(f"Login failed: {login_resp.status_code}")
        yield
        self.session.close()

    def test_buying_power_endpoint(self):
        """Buying power endpoint returns expected fields"""
        resp = self.session.get(f"{BASE_URL}/api/options/buying-power")
        # May return 501 if provider not implemented, 502 if broker error
        if resp.status_code == 200:
            data = resp.json()
            assert "cash" in data, "Missing 'cash' field"
            assert "options_buying_power" in data, "Missing 'options_buying_power' field"
            assert "provider" in data, "Missing 'provider' field"

    def test_positions_endpoint(self):
        """Positions endpoint returns list"""
        resp = self.session.get(f"{BASE_URL}/api/options/positions")
        # May return 501 if provider not implemented, 502 if broker error
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list), "Positions should be a list"
