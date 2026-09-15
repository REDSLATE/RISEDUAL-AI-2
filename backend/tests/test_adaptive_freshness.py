"""Adaptive, source-relative execution-freshness tests (2026-06)."""
import importlib

import services.broker_freshness_profile as bfp
import services.provider_policy as pp


def _reset_profile():
    importlib.reload(bfp)
    return bfp


def test_profile_untrusted_until_min_samples():
    b = _reset_profile()
    for _ in range(10):
        b.record("public-primary", "CORE", 1.0)
    p = b.profile("public", "CORE")
    assert p["count"] == 10
    assert p["trusted"] is False
    assert b.p95_if_trusted("public", "CORE") is None  # not enough samples


def test_profile_p95_when_trusted():
    b = _reset_profile()
    for v in range(100):            # 0..99
        b.record("public", "CORE", float(v))
    p = b.profile("public", "CORE")
    assert p["trusted"] is True
    assert p["count"] == 100
    assert 90 <= p["p95"] <= 99     # high percentile
    assert b.p95_if_trusted("public", "CORE") == p["p95"]


def test_route_key_normalisation():
    b = _reset_profile()
    assert b.route_key("public-primary") == "public"
    assert b.route_key("moomoo-opend") == "moomoo"
    assert b.route_key("kraken-x") == "kraken"


def test_assess_route_health_flags_limited():
    b = _reset_profile()
    for _ in range(40):
        b.record("public", "CORE", 1.0)   # trusted p95 ~1s
    obs = [
        {"age_seconds": 1.0, "has_book": True},
        {"age_seconds": 1.0, "has_book": False},   # missing book -> bad
        {"age_seconds": 100.0, "has_book": True},  # >5x p95 -> bad
        {"age_seconds": 100.0, "has_book": True},  # bad
    ]
    r = b.assess_route_health("public", "CORE", obs)
    assert r["broker_data_limited"] is True
    assert r["symbols_bad"] == 3
    assert r["missing_book"] == 1


def test_assess_route_health_healthy():
    b = _reset_profile()
    for _ in range(40):
        b.record("public", "CORE", 1.0)
    obs = [{"age_seconds": 1.0, "has_book": True} for _ in range(5)]
    r = b.assess_route_health("public", "CORE", obs)
    assert r["broker_data_limited"] is False


def test_compute_freshness_limit_legacy_when_off(monkeypatch):
    monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", False)
    assert pp.compute_freshness_limit("public", "CORE") == float(pp.EXECUTION_FRESHNESS_SECS)
    assert pp.compute_freshness_limit("public", "OVERNIGHT") == float(pp.EXECUTION_FRESHNESS_SECS)


def test_compute_freshness_limit_core_adaptive(monkeypatch):
    _reset_profile()
    monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", True)
    # No samples -> floor (configured minimum, default 2s).
    assert pp.compute_freshness_limit("public", "CORE") == float(pp.EXECUTION_FRESHNESS_MIN_SECS)
    # With a trusted p95 of ~10s, limit = max(2, 10*2) = 20.
    for _ in range(40):
        bfp.record("public", "CORE", 10.0)
    assert pp.compute_freshness_limit("public", "CORE") == 20.0


def test_compute_freshness_limit_session_ceilings(monkeypatch):
    monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", True)
    assert pp.compute_freshness_limit("public", "PREMARKET") == 120.0
    assert pp.compute_freshness_limit("public", "AFTER_HOURS") == 120.0
    assert pp.compute_freshness_limit("public", "OVERNIGHT") == 600.0
    assert pp.compute_freshness_limit("public", "CRYPTO") == 3.0


def test_execution_session_maps_phase(monkeypatch):
    import services.alpha_session_state as ss
    monkeypatch.setattr(ss, "get_session_state", lambda now=None: {"phase": "regular"})
    assert pp.execution_session() == "CORE"
    monkeypatch.setattr(ss, "get_session_state", lambda now=None: {"phase": "closed_overnight"})
    assert pp.execution_session() == "OVERNIGHT"
    monkeypatch.setattr(ss, "get_session_state", lambda now=None: {"phase": "pre_market"})
    assert pp.execution_session() == "PREMARKET"


def test_broker_tick_age_prefers_timestamp():
    import time
    # ISO broker timestamp ~120s old -> tick age ~120, not ~0.
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    q = {"timestamp": old, "fetched_at": time.time()}  # fetched_at is fresh
    age = pp._broker_tick_age(q)
    assert age is not None and 110 <= age <= 140
    # No timestamp -> falls back to fetched_at (MooMoo wire time).
    q2 = {"fetched_at": time.time() - 50}
    assert 45 <= pp._broker_tick_age(q2) <= 60
