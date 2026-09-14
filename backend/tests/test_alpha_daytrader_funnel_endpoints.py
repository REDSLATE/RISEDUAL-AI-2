"""Integration tests for the new observability endpoints.

Covers:
  * GET /api/admin/alpha-daytrader/counters — `funnel` block with all
    required keys and reconciliation math.
  * GET /api/admin/alpha-daytrader/why-not-trade — `rejections` block
    including the new `market_data_degraded` gate.
  * Admin auth is enforced (401/403 without credentials).
"""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fall back to frontend/.env for pytest runners that don't inherit env
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except FileNotFoundError:
        pass

ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"

FUNNEL_KEYS = {
    "symbols_scanned",
    "candidates_seen",
    "market_data_degraded",
    "rank_culled",
    "opportunity_score_rejected",
    "wave_danger_pause",
    "no_pattern_match",
    "setups_created",
    "candidates_accounted",
    "candidates_unaccounted",
}


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = None
    try:
        data = r.json()
        tok = data.get("access_token") or data.get("token") or (data.get("user") or {}).get("token")
    except Exception:
        pass
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ---- Auth enforcement -----------------------------------------------------

def test_counters_requires_auth():
    r = requests.get(f"{BASE_URL}/api/admin/alpha-daytrader/counters", timeout=15)
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}: {r.text[:200]}"


def test_why_not_trade_requires_auth():
    r = requests.get(
        f"{BASE_URL}/api/admin/alpha-daytrader/why-not-trade?since_seconds=86400",
        timeout=15,
    )
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}: {r.text[:200]}"


# ---- /counters funnel block ----------------------------------------------

def test_counters_funnel_shape_and_reconciliation(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/alpha-daytrader/counters", timeout=30)
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    assert "funnel" in body, f"Missing 'funnel' key. Keys: {list(body.keys())}"
    funnel = body["funnel"]
    missing = FUNNEL_KEYS - set(funnel.keys())
    assert not missing, f"Funnel missing keys: {missing}. Got: {list(funnel.keys())}"
    for k in FUNNEL_KEYS:
        assert isinstance(funnel[k], int), f"{k} not int: {type(funnel[k])}"
        assert funnel[k] >= 0, f"{k} negative: {funnel[k]}"

    # Reconciliation: accounted == sum of the 5 terminal buckets.
    expected_accounted = (
        funnel["rank_culled"]
        + funnel["opportunity_score_rejected"]
        + funnel["wave_danger_pause"]
        + funnel["no_pattern_match"]
        + funnel["setups_created"]
    )
    assert funnel["candidates_accounted"] == expected_accounted, (
        f"Reconciliation failed: accounted={funnel['candidates_accounted']} "
        f"expected={expected_accounted} funnel={funnel}"
    )


# ---- /why-not-trade rejections -------------------------------------------

def test_why_not_trade_includes_market_data_degraded_gate(admin_session):
    r = admin_session.get(
        f"{BASE_URL}/api/admin/alpha-daytrader/why-not-trade?since_seconds=86400",
        timeout=30,
    )
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    assert "rejections" in body, f"Missing 'rejections'. Keys: {list(body.keys())}"
    rej = body["rejections"]
    for gate in (
        "market_data_degraded",
        "opportunity_score_rejected",
        "wave_danger_pause",
        "no_pattern_match",
    ):
        assert gate in rej, f"Missing gate '{gate}'. Got: {list(rej.keys())}"
        entry = rej[gate]
        for field in ("description", "count", "symbols", "sub_reasons", "sample"):
            assert field in entry, f"Gate {gate} missing field '{field}'. Entry: {entry}"
