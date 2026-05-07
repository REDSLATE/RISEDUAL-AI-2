"""Backend contract tests for the RoadGuard admin endpoint.

Locks the contract that powers the RoadGuard Health-panel tile:
* envelope shape + top-level keys are stable
* shadow / enforce / can_approve flags surface
* by_lane block always carries equity / crypto / unknown
* per-lane bucket shape matches aggregate so the UI can reuse
  components across both
* promotion checklist contains the seven required items
* operator gate (admin-only) is enforced
* limit param is validated
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


def test_roadguard_stats_requires_auth():
    """Unauthenticated callers must hit the admin gate, not leak."""
    r = requests.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"


def test_roadguard_stats_envelope_shape(admin_session):
    """Top-level keys are stable — the UI tile reads them by name."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats?limit=5")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    expected = {
        "shadow_enabled",
        "enforce_enabled",
        "can_approve",
        "config",
        "total",
        "decision_counts",
        "reason_counts",
        "by_lane",
        "rows",
        "promotion_checklist",
    }
    missing = expected - set(d.keys())
    assert not missing, f"missing top-level keys: {missing}"


def test_roadguard_stats_can_approve_is_false(admin_session):
    """Authority invariant — must surface as False at the API
    boundary too, not just inside the module."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    assert r.json()["can_approve"] is False


def test_roadguard_stats_decision_counts_shape(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    dc = r.json()["decision_counts"]
    assert {"ALLOW", "BLOCK", "PAUSE_LANE"} <= set(dc.keys())
    for k in ("ALLOW", "BLOCK", "PAUSE_LANE"):
        assert isinstance(dc[k], int)


def test_roadguard_stats_by_lane_buckets_present(admin_session):
    """Three buckets always present — equity, crypto, unknown.
    Locks the contract that the UI tile renders three lane summary
    cards regardless of whether any lane has decisions yet."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    by_lane = r.json()["by_lane"]
    assert {"equity", "crypto", "unknown"} <= set(by_lane.keys())


def test_roadguard_stats_by_lane_bucket_shape_matches_aggregate(admin_session):
    """Each lane bucket has the same key set as the aggregate
    summary — UI components render both with shared code."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    expected = {"total", "decision_counts", "reason_counts", "enforced_count"}
    by_lane = r.json()["by_lane"]
    for lane in ("equity", "crypto", "unknown"):
        bucket = by_lane[lane]
        missing = expected - set(bucket.keys())
        assert not missing, f"by_lane.{lane} missing keys: {missing}"
        # Decision counts always have all three keys
        assert {"ALLOW", "BLOCK", "PAUSE_LANE"} <= set(
            bucket["decision_counts"].keys()
        )


def test_roadguard_stats_config_block(admin_session):
    """Config readout exposes the runtime caps so the operator
    can confirm what's actually enforced (catches stale .env)."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    cfg = r.json()["config"]
    expected = {
        "max_total_exposure_usd",
        "max_equity_exposure_usd",
        "max_crypto_exposure_usd",
        "max_daily_loss_usd",
        "max_open_positions_total",
        "max_open_positions_per_lane",
        "broker_health_min",
    }
    assert expected <= set(cfg.keys())


def test_roadguard_stats_promotion_checklist_seven_items(admin_session):
    """All seven user-spec'd checklist items must be present.
    Each item carries pass/value (and optional target) so the UI
    renders without a second round-trip."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    pc = r.json()["promotion_checklist"]
    expected = {
        "shadow_enabled",
        "enforce_disabled",
        "samples_500_plus",
        "zero_false_blocks",
        "broker_health_rule_observed",
        "duplicate_symbol_rule_observed",
        "exposure_cap_rule_observed",
        "ready_to_enforce",
    }
    missing = expected - set(pc.keys())
    assert not missing, f"checklist missing items: {missing}"

    # Each non-final item carries pass + value
    for k in expected - {"ready_to_enforce"}:
        assert "pass" in pc[k], f"{k} missing 'pass'"
        assert isinstance(pc[k]["pass"], bool)
    assert isinstance(pc["ready_to_enforce"], bool)


def test_roadguard_stats_empty_state_renders_cleanly(admin_session):
    """When no decisions exist yet, every count is 0 and the UI
    can still render without crashing on missing keys."""
    r = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r.status_code == 200
    d = r.json()

    # In the freshly-deployed empty state the totals must be
    # well-formed integers, not None.
    assert isinstance(d["total"], int)
    assert d["decision_counts"]["ALLOW"] >= 0
    assert d["decision_counts"]["BLOCK"] >= 0
    assert d["decision_counts"]["PAUSE_LANE"] >= 0

    # ``rows`` must always be a list (possibly empty), never None.
    assert isinstance(d["rows"], list)


def test_roadguard_stats_limit_param_validation(admin_session):
    r = admin_session.get(
        f"{BASE_URL}/api/admin/roadguard/stats?limit=99999"
    )
    assert r.status_code == 422, r.text[:200]


def test_roadguard_stats_limit_normal_value(admin_session):
    r = admin_session.get(
        f"{BASE_URL}/api/admin/roadguard/stats?limit=10"
    )
    assert r.status_code == 200
    rows = r.json().get("rows", [])
    assert isinstance(rows, list)
    assert len(rows) <= 10


def test_roadguard_stats_no_mutation(admin_session):
    """Reading the stats endpoint twice must not change anything.
    Locks the read-only contract — the tile is observation only."""
    r1 = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    r2 = admin_session.get(f"{BASE_URL}/api/admin/roadguard/stats")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Total decision counts should not change between two reads
    # (RoadGuard might have observed new decisions in between, but
    # those are additive — the function never deletes rows).
    assert r2.json()["total"] >= r1.json()["total"]
