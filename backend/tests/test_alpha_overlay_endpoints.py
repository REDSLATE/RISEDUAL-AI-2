"""HTTP-level regression tests for endpoints that survived the
Account-Aware Overlay removal.

The overlay itself (``/api/admin/alpha-daytrader/overlay/*``) was
intentionally deleted in Feb 2026 after it auto-promoted to
HARD_GATE and halted production trading. See
``/app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md``.

The test file that used to hit those endpoints was renamed to
document the transition; the class below covers the endpoints that
were verified alongside the overlay removal and must continue to work.
"""
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
