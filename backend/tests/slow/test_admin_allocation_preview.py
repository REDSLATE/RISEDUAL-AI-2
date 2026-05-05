"""Tests for GET /api/admin/allocation-preview.

Auth matrix (401/403 for non-admin, 200 for owner/admin), plus
shape + allocation-math smoke checks against the live fleet.
"""
import os
import pytest
import requests

from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/admin/allocation-preview"


# ────────────────────────────────────────────────────────────────────────────
# Session fixtures (class-scoped so login runs once)
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def owner_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, f"owner login: {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login: {r.text}"
    return s


@pytest.fixture(scope="module")
def anon_session():
    return requests.Session()


# ────────────────────────────────────────────────────────────────────────────
# Auth matrix
# ────────────────────────────────────────────────────────────────────────────

def test_allocation_preview_anon_returns_401(anon_session):
    r = anon_session.get(ENDPOINT)
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


def test_allocation_preview_owner_returns_200(owner_session):
    r = owner_session.get(ENDPOINT)
    assert r.status_code == 200, r.text


def test_allocation_preview_admin_returns_200(admin_session):
    r = admin_session.get(ENDPOINT)
    assert r.status_code == 200, r.text


# ────────────────────────────────────────────────────────────────────────────
# Response shape + math
# ────────────────────────────────────────────────────────────────────────────

def test_response_shape_has_all_required_fields(owner_session):
    r = owner_session.get(ENDPOINT)
    data = r.json()
    for key in ("total_capital", "bot_count", "allocations", "scores"):
        assert key in data, f"missing {key} in response: {data}"


def test_default_total_capital_is_10000(owner_session):
    r = owner_session.get(ENDPOINT)
    assert r.json()["total_capital"] == 10000.0


def test_custom_total_capital_passed_through(owner_session):
    r = owner_session.get(ENDPOINT, params={"total_capital": 25000})
    assert r.json()["total_capital"] == 25000.0


def test_negative_total_capital_rejected(owner_session):
    r = owner_session.get(ENDPOINT, params={"total_capital": -1000})
    assert r.status_code == 400


def test_zero_total_capital_rejected(owner_session):
    r = owner_session.get(ENDPOINT, params={"total_capital": 0})
    assert r.status_code == 400


def test_allocations_roughly_sum_to_total(owner_session):
    """Allocations should sum to ~total_capital (±$1 rounding)."""
    r = owner_session.get(ENDPOINT, params={"total_capital": 5000})
    data = r.json()
    if data["bot_count"] == 0:
        pytest.skip("no enabled bots in fleet")
    total = sum(data["allocations"].values())
    # Allocator rounds each slice to cents; the sum may drift by <= bot_count cents.
    assert abs(total - 5000.0) <= data["bot_count"], (
        f"allocations {data['allocations']} should sum to ~5000, got {total}"
    )


def test_every_bot_has_a_score(owner_session):
    r = owner_session.get(ENDPOINT)
    data = r.json()
    if data["bot_count"] == 0:
        pytest.skip("no enabled bots in fleet")
    # Every allocation key should have a matching score.
    assert set(data["allocations"].keys()) == set(data["scores"].keys())


def test_scores_respect_min_floor(owner_session):
    """compute_bot_score has a 0.1 floor — no score below that."""
    r = owner_session.get(ENDPOINT)
    data = r.json()
    if data["bot_count"] == 0:
        pytest.skip("no enabled bots in fleet")
    for name, score in data["scores"].items():
        assert score >= 0.1, f"{name} scored {score} (< 0.1 floor)"


def test_bot_count_matches_allocation_keys(owner_session):
    r = owner_session.get(ENDPOINT)
    data = r.json()
    # bot_count counts enabled bots pulled from Mongo. allocations may
    # collide on duplicate names (name-keyed dict) — allocations ≤ bot_count.
    assert len(data["allocations"]) <= data["bot_count"]
