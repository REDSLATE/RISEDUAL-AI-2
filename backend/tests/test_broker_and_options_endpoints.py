"""HTTP endpoint tests for broker comparison + MooMoo options policy/preview."""
from __future__ import annotations

import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://risedual-trading.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"No token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ---------------- Broker comparison ----------------

def test_broker_comparison_1d(headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison?window=1d",
                     headers=headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    for k in ("window", "brokers", "winner", "weights", "low_sample_warning", "low_sample_threshold"):
        assert k in body, f"missing key {k} in {body.keys()}"
    assert body["window"] == "1d"
    assert "public" in body["brokers"] and "moomoo" in body["brokers"]
    w = body["weights"]
    assert w["slippage"] == 0.5
    assert w["fill_rate"] == 0.3
    assert w["ack_latency"] == 0.2


@pytest.mark.parametrize("window", ["1h", "7d", "30d", "foo"])
def test_broker_comparison_windows(headers, window):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison?window={window}",
                     headers=headers, timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "brokers" in body


def test_broker_comparison_symbol_filter(headers):
    r = requests.get(
        f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison?window=1d&symbol=AAPL",
        headers=headers, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body.get("symbol") == "AAPL" or "AAPL" in str(body)


def test_broker_comparison_recent(headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison/recent?limit=5",
                     headers=headers, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "rows" in body
    assert isinstance(body["rows"], list)


# ---------------- MooMoo options ----------------

def test_moomoo_options_policy(headers):
    r = requests.get(f"{BASE_URL}/api/admin/moomoo/options/policy",
                     headers=headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "policy" in body
    p = body["policy"]
    expected_keys = ["enabled", "delta_min", "delta_max", "dte_min", "dte_max",
                     "min_open_interest", "min_daily_volume", "max_spread_pct",
                     "order_type", "max_contracts", "stale_quote_max_sec",
                     "earnings_blackout", "notes"]
    for k in expected_keys:
        assert k in p, f"missing {k} in policy keys={list(p.keys())}"
    assert p["enabled"] is False
    assert p["delta_min"] == 0.3
    assert p["delta_max"] == 0.45
    assert p["dte_min"] == 7
    assert p["dte_max"] == 21
    assert p["min_open_interest"] == 1000
    assert p["min_daily_volume"] == 500
    assert p["max_spread_pct"] == 8.0
    assert p["order_type"] == "marketable_limit"
    assert p["max_contracts"] == 1
    assert p["earnings_blackout"] is True
    notes_blob = " ".join(p["notes"])
    assert "V1 read-only" in notes_blob
    assert "MARKET" in notes_blob


def test_moomoo_options_preview(headers):
    r = requests.get(f"{BASE_URL}/api/admin/moomoo/options/preview/AAPL",
                     headers=headers, timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "policy_used" in body, f"body keys: {body.keys()}"
    if body.get("available") is False:
        assert body.get("reason") == "opend_unreachable_or_no_option_entitlement"
    else:
        assert body.get("available") is True


# ---------------- Auth required ----------------

@pytest.mark.parametrize("path", [
    "/api/admin/alpha-daytrader/broker-comparison?window=1d",
    "/api/admin/alpha-daytrader/broker-comparison/recent?limit=5",
    "/api/admin/moomoo/options/policy",
    "/api/admin/moomoo/options/preview/AAPL",
])
def test_endpoints_require_auth(path):
    r = requests.get(f"{BASE_URL}{path}", timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code} for {path}"
