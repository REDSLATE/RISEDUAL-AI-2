"""Integration tests for MooMoo bridge admin endpoints (Option 2)."""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Read from frontend .env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass
BASE_URL = (BASE_URL or "").rstrip("/")

ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="module")
def anon_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ── Unauth ─────────────────────────────────────────────────────────

def test_health_unauth_401_or_403(anon_session):
    r = anon_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/health")
    assert r.status_code in (401, 403), r.text[:200]


def test_smoke_unauth_401_or_403(anon_session):
    r = anon_session.post(f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/smoke-test")
    assert r.status_code in (401, 403), r.text[:200]


def test_endpoint_unauth_401_or_403(anon_session):
    r = anon_session.post(
        f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/endpoint",
        json={"host": "127.0.0.1", "port": 11111},
    )
    assert r.status_code in (401, 403), r.text[:200]


# ── Health ─────────────────────────────────────────────────────────

def test_health_admin_returns_disconnected(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/health")
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    # Keys shape
    for key in ("state", "opend", "market_data", "trade", "gates", "warnings", "errors"):
        assert key in body, f"missing key {key}: {body}"
    # No OpenD in pod → DISCONNECTED
    assert body["state"] == "DISCONNECTED", body
    assert body["opend"]["reachable"] is False


# ── Smoke ──────────────────────────────────────────────────────────

def test_smoke_admin_fails_at_tunnel(admin_session):
    r = admin_session.post(f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/smoke-test")
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    for stage in ("tunnel", "market_data", "account", "research_snapshot", "overall"):
        assert stage in body, f"missing stage {stage}: {body}"
    assert body["overall"]["ok"] is False
    assert body["overall"]["failed_at"] == "tunnel"


# ── Endpoint override (owner) ─────────────────────────────────────

def test_endpoint_owner_apply(admin_session):
    r = admin_session.post(
        f"{BASE_URL}/api/admin/alpha-daytrader/moomoo-bridge/endpoint",
        json={
            "host": "127.0.0.1",
            "port": 11111,
            "canary_symbol": "SPY",
            "execution_ready": False,
        },
    )
    # admin@risedual.ai is owner role per test_credentials.md
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert "applied" in body
    assert body["applied"].get("MOOMOO_OPEND_HOST") == "127.0.0.1"
    assert body["applied"].get("MOOMOO_OPEND_PORT") == "11111"
    assert body["applied"].get("MOOMOO_EXECUTION_READY") == "0"
