"""Auth matrix for GET /api/admin/alpaca-health.

Only covers the auth/plumbing layer — the actual broker call is
smoke-tested live (documented in DEPLOYMENT_NOTES.md) since mocking
`requests.get` deep inside `AlpacaTradingService` adds more fragility
than it catches. The expensive path is already exercised end-to-end
by the manual curl check on every session.
"""
import os
import pytest
import requests

from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/admin/alpaca-health"


@pytest.fixture(scope="module")
def owner_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def anon_session():
    return requests.Session()


def test_alpaca_health_anon_returns_401(anon_session):
    r = anon_session.get(ENDPOINT)
    assert r.status_code == 401


def test_alpaca_health_owner_returns_200(owner_session):
    r = owner_session.get(ENDPOINT)
    assert r.status_code == 200, r.text


def test_response_shape_has_all_sections(owner_session):
    data = owner_session.get(ENDPOINT).json()
    for key in ("mode", "account", "positions", "orders", "verdict"):
        assert key in data, f"missing {key}: {list(data.keys())}"


def test_positions_splits_into_longs_shorts(owner_session):
    data = owner_session.get(ENDPOINT).json()
    p = data["positions"]
    assert p["total"] == p["longs"] + p["shorts"], (
        f"positions split mismatch: total={p['total']} longs={p['longs']} shorts={p['shorts']}"
    )


def test_verdict_flags_are_booleans(owner_session):
    v = owner_session.get(ENDPOINT).json()["verdict"]
    for key in ("covers_clean", "no_orphan_orders", "trading_enabled"):
        assert isinstance(v[key], bool), f"{key} should be bool, got {type(v[key])}"


def test_covers_clean_matches_short_count(owner_session):
    """`covers_clean` must be True iff there are zero shorts."""
    data = owner_session.get(ENDPOINT).json()
    assert data["verdict"]["covers_clean"] == (data["positions"]["shorts"] == 0)


def test_no_orphan_orders_matches_orphan_count(owner_session):
    """`no_orphan_orders` must be True iff there are zero orphans."""
    data = owner_session.get(ENDPOINT).json()
    assert data["verdict"]["no_orphan_orders"] == (data["orders"]["orphan_count"] == 0)
