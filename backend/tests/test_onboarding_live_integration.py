"""Live HTTP integration tests for onboarding endpoints against the public URL.

Verifies:
  1. GET /api/onboarding/status without auth -> 401/403
  2. Admin login -> user_response includes onboarding_completed
  3. GET /api/onboarding/status with Bearer -> 200, correct shape
  4. POST /api/onboarding/complete {stage:'skipped'} -> 200 ok:true
  5. POST /api/onboarding/complete {} defaults stage to 'completed'
  6. Idempotent second POST -> 200
  7. Subsequent GET /status -> completed:true
  8. Regressions: admin broker-comparison, broker-audit, slippage-alerts, google session missing session_id
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://risedual-trading.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    assert token, f"no token in login body: {list(data)}"
    # Regression: onboarding_completed on user_response (flat shape).
    user = data.get("user") or data.get("user_response") or data
    assert "onboarding_completed" in user, f"user_response missing onboarding_completed. Keys: {list(user)}"
    assert isinstance(user["onboarding_completed"], bool)
    return token


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def test_status_requires_auth():
    r = requests.get(f"{BASE_URL}/api/onboarding/status", timeout=20)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}: {r.text[:200]}"


def test_status_shape_with_auth(auth_headers):
    r = requests.get(f"{BASE_URL}/api/onboarding/status", headers=auth_headers, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("completed", "stage", "completed_at", "brokers"):
        assert k in data, f"missing key {k}: {data}"
    b = data["brokers"]
    for k in ("public_connected", "moomoo_connected", "any_connected", "connections"):
        assert k in b, f"brokers missing {k}: {b}"
    assert isinstance(b["public_connected"], bool)
    assert isinstance(b["moomoo_connected"], bool)
    assert isinstance(b["any_connected"], bool)
    assert isinstance(b["connections"], list)


def test_complete_skipped(auth_headers):
    r = requests.post(f"{BASE_URL}/api/onboarding/complete",
                      headers=auth_headers, json={"stage": "skipped"}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok") is True
    assert data.get("completed") is True
    assert data.get("stage") == "skipped"


def test_complete_empty_body_defaults(auth_headers):
    r = requests.post(f"{BASE_URL}/api/onboarding/complete",
                      headers=auth_headers, json={}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("completed") is True
    assert data.get("stage") == "completed"


def test_complete_is_idempotent(auth_headers):
    r1 = requests.post(f"{BASE_URL}/api/onboarding/complete",
                       headers=auth_headers, json={"stage": "completed"}, timeout=20)
    r2 = requests.post(f"{BASE_URL}/api/onboarding/complete",
                       headers=auth_headers, json={"stage": "completed"}, timeout=20)
    assert r1.status_code == 200 and r2.status_code == 200


def test_status_reflects_completion(auth_headers):
    # ensure completed first
    requests.post(f"{BASE_URL}/api/onboarding/complete",
                  headers=auth_headers, json={"stage": "completed"}, timeout=20)
    r = requests.get(f"{BASE_URL}/api/onboarding/status", headers=auth_headers, timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert data["completed"] is True
    assert data["stage"] in ("completed", "skipped", "brokers_connected")


# ── Regression on existing endpoints ──

def test_broker_comparison_ok(auth_headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-comparison",
                     headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"


def test_broker_audit_ok(auth_headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/broker-audit",
                     headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"


def test_slippage_alerts_ok(auth_headers):
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/slippage-alerts",
                     headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"


def test_google_session_missing_session_id():
    r = requests.post(f"{BASE_URL}/api/auth/google/session", json={}, timeout=20)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
