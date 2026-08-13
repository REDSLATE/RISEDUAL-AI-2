"""HTTP-level regression tests for the Alpha Overlay admin endpoints
plus regressions requested by review: broker-comparison, broker-audit,
slippage-alerts, /api/onboarding/status, /api/auth/google/session."""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://risedual-trading.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=20)
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="module")
def anon_session():
    return requests.Session()


# ── /overlay/state ─────────────────────────────────────────────────
class TestOverlayState:
    def test_requires_admin(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/overlay/state", timeout=45)
        assert r.status_code in (401, 403), f"unauthenticated got {r.status_code}"

    def test_returns_expected_shape(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/overlay/state", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        # required keys
        for k in ("mode", "faulted", "fault_reason", "fault_since",
                  "overlay_started_at", "trading_days_completed",
                  "trading_days_required", "days_remaining",
                  "hard_gate_at", "last_trading_day_counted"):
            assert k in d, f"missing key {k}: {d}"
        assert d["trading_days_required"] == 7
        assert d["mode"] in ("SHADOW", "HARD_GATE", "FAULTED")
        assert isinstance(d["trading_days_completed"], int)
        assert isinstance(d["days_remaining"], int)
        assert d["days_remaining"] == max(0, 7 - d["trading_days_completed"])


# ── /overlay/preview ────────────────────────────────────────────────
class TestOverlayPreview:
    def test_public_preview_returns_200(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "public", "symbol": "AAPL", "side": "BUY", "notional": 500},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "available" in d
        if d["available"]:
            for k in ("verdict", "size_multiplier", "reasons",
                      "proposed_notional", "account_context"):
                assert k in d
            assert d["verdict"] in ("PASS", "REDUCE", "BLOCK", "HOLD")
        else:
            assert d["reason"] == "broker_account_unreachable"

    def test_moomoo_preview_returns_unavailable_gracefully(self, admin_session):
        # MooMoo OpenD not running in preview — must not 500.
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "moomoo", "symbol": "AAPL", "side": "BUY", "notional": 500},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        # Either available OR available:false with broker_account_unreachable — no 500.
        assert "available" in d
        if not d["available"]:
            assert d["reason"] == "broker_account_unreachable"

    def test_invalid_broker_returns_422(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "alpaca", "symbol": "AAPL", "side": "BUY", "notional": 500},
            timeout=15,
        )
        assert r.status_code == 422, r.text

    def test_invalid_side_returns_422(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "public", "symbol": "AAPL", "side": "yolo", "notional": 500},
            timeout=15,
        )
        assert r.status_code == 422, r.text

    def test_zero_notional_returns_422(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "public", "symbol": "AAPL", "side": "BUY", "notional": 0.0},
            timeout=15,
        )
        assert r.status_code == 422, r.text

    def test_requires_admin(self, anon_session):
        r = anon_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/preview",
            params={"broker": "public", "symbol": "AAPL", "side": "BUY", "notional": 500},
            timeout=15,
        )
        assert r.status_code in (401, 403)


# ── /overlay/transition-report ─────────────────────────────────────
class TestOverlayTransitionReport:
    def test_returns_expected_shape(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/overlay/transition-report", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("started_at", "trading_days_completed", "mode", "faulted",
                  "counts", "total_sizing_delta_usd", "avoided_losses_usd",
                  "blocked_winners_usd", "broker_read_failures",
                  "total_records", "records"):
            assert k in d, f"missing {k}"
        assert isinstance(d["counts"], dict)
        for v in ("PASS", "REDUCE", "BLOCK", "HOLD", "FAULTED_READ"):
            assert v in d["counts"]
        assert isinstance(d["records"], list)


# ── Regressions ─────────────────────────────────────────────────────
class TestExistingEndpointRegressions:
    def test_broker_comparison(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison",
            params={"window": "1d"}, timeout=20)
        assert r.status_code == 200, r.text

    def test_broker_audit(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
            params={"limit": 10}, timeout=20)
        assert r.status_code == 200, r.text
        assert "switches" in r.json()

    def test_slippage_alerts(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/alpha-daytrader/slippage-alerts",
            timeout=25)
        assert r.status_code == 200, r.text

    def test_onboarding_status(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/onboarding/status", timeout=15)
        assert r.status_code == 200, r.text

    def test_google_session_missing_session_id_returns_400(self):
        r = requests.post(f"{BASE_URL}/api/auth/google/session",
                          json={}, timeout=15)
        assert r.status_code == 400, r.text
