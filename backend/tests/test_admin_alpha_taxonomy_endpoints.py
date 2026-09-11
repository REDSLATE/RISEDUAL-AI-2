"""Live integration tests for rejection-taxonomy and orphan-reaper admin endpoints."""
import os
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE:
    # fallback to frontend env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE = line.split("=", 1)[1].strip().rstrip("/")

ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PW = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PW},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    return s


def test_rejection_taxonomy_unauth_denied():
    r = requests.get(
        f"{BASE}/api/admin/alpha-daytrader/rejection-taxonomy?since_seconds=86400",
        timeout=15,
    )
    assert r.status_code in (401, 403), f"expected auth failure, got {r.status_code}"


def test_rejection_taxonomy_admin_ok(admin_session):
    r = admin_session.get(
        f"{BASE}/api/admin/alpha-daytrader/rejection-taxonomy?since_seconds=86400",
        timeout=20,
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text[:300]}"
    body = r.json()
    assert "since_seconds" in body
    assert "total" in body
    assert "taxonomy" in body
    tax = body["taxonomy"]
    for k in ("protection", "infrastructure", "session", "concurrency", "unknown"):
        assert k in tax, f"missing class {k} in taxonomy"
    assert "health_hint" in body
    hh = body["health_hint"]
    assert "tone" in hh and "message" in hh
    assert hh["tone"] in ("ok", "neutral", "warn", "alert")


def test_orphan_reaper_unauth_denied():
    r = requests.post(
        f"{BASE}/api/admin/alpha-daytrader/orphan-reaper/sweep", timeout=15
    )
    assert r.status_code in (401, 403), f"expected auth failure, got {r.status_code}"


def test_orphan_reaper_admin_ok(admin_session):
    r = admin_session.post(
        f"{BASE}/api/admin/alpha-daytrader/orphan-reaper/sweep",
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text[:300]}"
    body = r.json()
    assert "reaped" in body
    assert "window_seconds" in body
    assert "sample" in body
    assert isinstance(body["sample"], list)
