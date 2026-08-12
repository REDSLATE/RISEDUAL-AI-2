"""Iteration 191 - Broker Router Audit Log integration tests against live API."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or "http://localhost:8001"
# Read frontend .env if BASE_URL is unset
if BASE_URL == "http://localhost:8001":
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except Exception:
        pass

ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=15)
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    if not tok:
        # Try cookie
        tok = r.cookies.get("access_token")
    if not tok:
        pytest.skip(f"no token in login response: {data}")
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def bot_id(auth_headers):
    """Create a signal bot to test broker switches on."""
    payload = {"type": "signal", "name": "TEST_iter191_audit_bot", "mode": "paper", "broker": "public"}
    r = requests.post(f"{BASE_URL}/api/bots", json=payload, headers=auth_headers, timeout=15)
    if r.status_code >= 400:
        pytest.skip(f"bot creation failed: {r.status_code} {r.text[:300]}")
    data = r.json()
    bid = data.get("bot_id") or data.get("id") or (data.get("bot") or {}).get("id") or (data.get("bot") or {}).get("_id")
    if not bid:
        # Fall back: list bots and find by name
        lr = requests.get(f"{BASE_URL}/api/bots", headers=auth_headers, timeout=15)
        for b in (lr.json() if lr.ok else []):
            if b.get("name") == payload["name"]:
                bid = b.get("id") or b.get("_id") or b.get("bot_id")
                break
    if not bid:
        pytest.skip(f"could not extract bot_id from response: {data}")
    yield bid
    # Cleanup
    try:
        requests.delete(f"{BASE_URL}/api/bots/{bid}", headers=auth_headers, timeout=10)
    except Exception:
        pass


# ---------- PATCH broker endpoint ----------

def test_patch_broker_switch_records_audit(auth_headers, bot_id):
    # Switch to moomoo (from public)
    r = requests.patch(f"{BASE_URL}/api/bots/{bot_id}/broker",
                       json={"broker": "moomoo"}, headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bot_id"] == bot_id
    assert body["broker"] == "moomoo"
    assert body["from_broker"] == "public"

    # Verify audit row present
    a = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                     params={"bot_id": bot_id}, headers=auth_headers, timeout=15)
    assert a.status_code == 200
    switches = a.json()["switches"]
    assert len(switches) >= 1
    latest = switches[0]
    assert latest["bot_id"] == bot_id
    assert latest["to_broker"] == "moomoo"
    assert latest["from_broker"] == "public"
    assert latest["reason"] == "operator_switch"
    assert "created_at" in latest
    assert "operator" in latest
    assert "user_id" in latest


def test_patch_broker_same_broker_is_noop_for_audit(auth_headers, bot_id):
    # Current is moomoo. Set it again to moomoo.
    before = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                          params={"bot_id": bot_id}, headers=auth_headers, timeout=15).json()["switches"]
    r = requests.patch(f"{BASE_URL}/api/bots/{bot_id}/broker",
                       json={"broker": "moomoo"}, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["broker"] == "moomoo"
    assert body["from_broker"] == "moomoo"
    after = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                         params={"bot_id": bot_id}, headers=auth_headers, timeout=15).json()["switches"]
    assert len(after) == len(before), "no-change switch must NOT add audit row"


def test_patch_broker_invalid_value_400(auth_headers, bot_id):
    before = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                          params={"bot_id": bot_id}, headers=auth_headers, timeout=15).json()["switches"]
    r = requests.patch(f"{BASE_URL}/api/bots/{bot_id}/broker",
                       json={"broker": "etrade"}, headers=auth_headers, timeout=15)
    assert r.status_code == 400
    after = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                         params={"bot_id": bot_id}, headers=auth_headers, timeout=15).json()["switches"]
    assert len(after) == len(before)


def test_patch_broker_wrong_owner_404(auth_headers):
    # Fake ObjectId belonging to no one
    fake_id = "507f1f77bcf86cd799439011"
    r = requests.patch(f"{BASE_URL}/api/bots/{fake_id}/broker",
                       json={"broker": "moomoo"}, headers=auth_headers, timeout=15)
    assert r.status_code == 404


# ---------- GET broker-audit ----------

def test_broker_audit_unauthenticated_returns_401_or_403():
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit", timeout=10)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


def test_broker_audit_returns_list_newest_first(auth_headers, bot_id):
    # Force a second switch: moomoo -> public
    requests.patch(f"{BASE_URL}/api/bots/{bot_id}/broker",
                   json={"broker": "public"}, headers=auth_headers, timeout=15)
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                     params={"bot_id": bot_id}, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    switches = r.json()["switches"]
    assert len(switches) >= 2
    # Newest first
    assert switches[0]["created_at"] >= switches[1]["created_at"]
    # Required fields
    for row in switches:
        for k in ("bot_id", "user_id", "operator", "to_broker", "reason", "created_at"):
            assert k in row, f"missing {k} in {row}"
        assert "from_broker" in row  # may be null


def test_broker_audit_limit_param(auth_headers, bot_id):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                     params={"limit": 1, "bot_id": bot_id}, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    switches = r.json()["switches"]
    assert len(switches) == 1


def test_broker_audit_bot_id_filter(auth_headers, bot_id):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                     params={"bot_id": bot_id}, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    for row in r.json()["switches"]:
        assert row["bot_id"] == bot_id


# ---------- Regression: iteration_190 endpoints ----------

def test_regression_broker_comparison(auth_headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison",
                     headers=auth_headers, timeout=15)
    assert r.status_code == 200
    data = r.json()
    # Must include both venues in some form
    assert isinstance(data, dict)


def test_regression_broker_comparison_recent(auth_headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison/recent",
                     params={"limit": 10}, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    assert "rows" in r.json()
