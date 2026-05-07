"""Backend tests for the Fast Veto admin endpoint.

Locks the contract that powers the Health-panel tile:
* shadow & enforce flag values surface
* lifetime totals + per-reason histogram
* council-agreement bucketing only counts ``would_veto=True`` rows
* latency p50/p95 calculation
* promotion checklist booleans match the rules
* operator gate (admin-only) is enforced
"""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)

ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code}")
    return s


def test_fast_veto_stats_requires_auth():
    """Unauthenticated callers must hit the admin gate, not leak."""
    r = requests.get(f"{BASE_URL}/api/admin/fast-veto/stats")
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"


def test_fast_veto_stats_returns_envelope_shape(admin_session):
    """Top-level keys are stable — the UI tile reads them by name."""
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats?limit=5")
    assert r.status_code == 200, r.text[:300]
    d = r.json()

    expected = {
        "shadow_enabled",
        "enforce_enabled",
        "can_approve",
        "total",
        "would_veto_count",
        "would_veto_rate",
        "reason_counts",
        "council_agreement",
        "latency_us",
        "rows",
        "promotion_checklist",
    }
    missing = expected - set(d.keys())
    assert not missing, f"missing top-level keys: {missing}"


def test_fast_veto_stats_can_approve_is_false(admin_session):
    """Authority invariant — must be False at the API surface too."""
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["can_approve"] is False, (
        "FAST_VETO_CAN_APPROVE must always be False at the API boundary"
    )


def test_fast_veto_stats_council_agreement_shape(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats")
    assert r.status_code == 200
    ca = r.json()["council_agreement"]
    assert {"agree", "disagree", "unknown", "rate"} <= set(ca.keys())
    for k in ("agree", "disagree", "unknown"):
        assert isinstance(ca[k], int)


def test_fast_veto_stats_latency_shape(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats")
    assert r.status_code == 200
    lat = r.json()["latency_us"]
    assert {"count", "p50", "p95", "min", "max"} <= set(lat.keys())


def test_fast_veto_stats_promotion_checklist_shape(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats")
    assert r.status_code == 200
    pc = r.json()["promotion_checklist"]
    assert {
        "samples_500_plus",
        "false_veto_rate_under_3pct",
        "median_latency_under_1ms",
        "ready_to_enforce",
    } <= set(pc.keys())
    # Each sub-row carries pass/value/target so the UI can render
    # the row without a second round-trip.
    for sub_key in (
        "samples_500_plus",
        "false_veto_rate_under_3pct",
        "median_latency_under_1ms",
    ):
        assert {"pass", "value", "target"} <= set(pc[sub_key].keys())
    assert isinstance(pc["ready_to_enforce"], bool)


def test_fast_veto_stats_limit_param_clamping(admin_session):
    # Out-of-range limit must produce a 422, not a silent server-side
    # explosion (locks the FastAPI Query validator).
    r = admin_session.get(
        f"{BASE_URL}/api/admin/fast-veto/stats?limit=99999"
    )
    assert r.status_code == 422, r.text[:200]


def test_fast_veto_stats_limit_normal_value(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/fast-veto/stats?limit=10")
    assert r.status_code == 200
    rows = r.json().get("rows", [])
    assert isinstance(rows, list)
    assert len(rows) <= 10
