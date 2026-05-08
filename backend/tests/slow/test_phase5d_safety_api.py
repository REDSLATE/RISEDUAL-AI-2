"""Phase 5d Safety API Tests - Heartbeat, Pipeline Receipts, Boot Receipts

Tests the 3 architectural safety protections:
  a) Stale-Model Protection (model_age_hours, stale fields in boot receipts)
  b) Feature-Health Weighting (feature_health_score in pipeline diagnostics)
  c) Executor Heartbeat (frozen lanes, signals_1h, holds_1h)

All endpoints require admin role via _require_admin.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    """Authenticate as admin and return session with cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login as admin
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@risedual.ai", "password": "RiseDual2026!"},
    )
    if login_resp.status_code != 200:
        pytest.skip(f"Admin login failed: {login_resp.status_code} - {login_resp.text}")
    return session


@pytest.fixture(scope="module")
def unauthenticated_session():
    """Return session without auth cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


# ── Auth Gate Tests ──────────────────────────────────────────────


class TestAuthGate:
    """All admin endpoints require admin role."""

    def test_heartbeat_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/heartbeat returns 401/403 without auth."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"PASS: Heartbeat auth gate returns {resp.status_code}")

    def test_pipeline_receipts_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/pipeline/receipts returns 401/403 without auth."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/pipeline/receipts")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"PASS: Pipeline receipts auth gate returns {resp.status_code}")

    def test_boot_receipts_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/boot-receipts returns 401/403 without auth."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"PASS: Boot receipts auth gate returns {resp.status_code}")

    def test_pipeline_decide_requires_auth(self, unauthenticated_session):
        """POST /api/admin/ml/v2/pipeline/decide returns 401/403 without auth."""
        resp = unauthenticated_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"PASS: Pipeline decide auth gate returns {resp.status_code}")


# ── Heartbeat Endpoint Tests ─────────────────────────────────────


class TestHeartbeatEndpoint:
    """GET /api/admin/ml/heartbeat - per-lane state with frozen flag."""

    def test_heartbeat_returns_200(self, admin_session):
        """Heartbeat endpoint returns 200 with valid structure."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        print(f"PASS: Heartbeat returns 200")
        return data

    def test_heartbeat_has_lanes(self, admin_session):
        """Heartbeat response has equity and crypto lanes."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "lanes" in data, "Missing 'lanes' key"
        assert "equity" in data["lanes"], "Missing 'equity' lane"
        assert "crypto" in data["lanes"], "Missing 'crypto' lane"
        print(f"PASS: Heartbeat has equity and crypto lanes")

    def test_heartbeat_lane_structure(self, admin_session):
        """Each lane has required fields: frozen, signals_1h, holds_1h, model_age_hours."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        
        required_fields = [
            "lane", "last_pipeline_run_at", "last_signal_at", "last_decision",
            "signals_1h", "holds_1h", "feature_health_avg", "model_age_hours",
            "frozen", "freeze_after_min", "model_stale",
        ]
        
        for lane_name in ("equity", "crypto"):
            lane = data["lanes"][lane_name]
            for field in required_fields:
                assert field in lane, f"Missing '{field}' in {lane_name} lane"
            print(f"PASS: {lane_name} lane has all required fields")

    def test_heartbeat_frozen_flag_is_boolean(self, admin_session):
        """Frozen flag is a boolean."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        
        for lane_name in ("equity", "crypto"):
            frozen = data["lanes"][lane_name]["frozen"]
            assert isinstance(frozen, bool), f"frozen should be bool, got {type(frozen)}"
        print(f"PASS: frozen flags are booleans")

    def test_heartbeat_any_frozen_flag(self, admin_session):
        """Heartbeat has any_frozen top-level flag."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "any_frozen" in data, "Missing 'any_frozen' key"
        assert isinstance(data["any_frozen"], bool), "any_frozen should be bool"
        print(f"PASS: any_frozen flag present and is boolean")

    def test_heartbeat_model_stale_flag(self, admin_session):
        """Each lane has model_stale flag (from boot receipts)."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        
        for lane_name in ("equity", "crypto"):
            lane = data["lanes"][lane_name]
            assert "model_stale" in lane, f"Missing 'model_stale' in {lane_name}"
            assert isinstance(lane["model_stale"], bool), "model_stale should be bool"
        print(f"PASS: model_stale flags present in both lanes")


# ── Pipeline Receipts Endpoint Tests ─────────────────────────────


class TestPipelineReceiptsEndpoint:
    """GET /api/admin/ml/pipeline/receipts - recent alpha_decision_log items."""

    def test_pipeline_receipts_returns_200(self, admin_session):
        """Pipeline receipts endpoint returns 200."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/pipeline/receipts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "items" in data, "Missing 'items' key"
        assert "count" in data, "Missing 'count' key"
        print(f"PASS: Pipeline receipts returns 200 with {data['count']} items")

    def test_pipeline_receipts_lane_filter(self, admin_session):
        """Pipeline receipts supports lane filter."""
        for lane in ("equity", "crypto"):
            resp = admin_session.get(f"{BASE_URL}/api/admin/ml/pipeline/receipts?lane={lane}")
            assert resp.status_code == 200, f"Expected 200 for lane={lane}, got {resp.status_code}"
            data = resp.json()
            # All items should be for the specified lane (if any)
            for item in data["items"]:
                if "lane" in item:
                    assert item["lane"] == lane, f"Item lane mismatch: expected {lane}, got {item['lane']}"
            print(f"PASS: Pipeline receipts lane={lane} filter works")

    def test_pipeline_receipts_limit(self, admin_session):
        """Pipeline receipts respects limit parameter."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/pipeline/receipts?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 5, f"Expected <= 5 items, got {len(data['items'])}"
        print(f"PASS: Pipeline receipts limit=5 respected")


# ── Boot Receipts Endpoint Tests ─────────────────────────────────


class TestBootReceiptsEndpoint:
    """GET /api/admin/ml/v2/boot-receipts - model_age_hours and stale fields."""

    def test_boot_receipts_returns_200(self, admin_session):
        """Boot receipts endpoint returns 200."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "receipts" in data, "Missing 'receipts' key"
        assert "count" in data, "Missing 'count' key"
        print(f"PASS: Boot receipts returns 200 with {data['count']} receipts")

    def test_boot_receipts_have_stale_fields(self, admin_session):
        """Boot receipts include model_age_hours and stale fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200
        data = resp.json()
        
        for receipt in data["receipts"]:
            # model_age_hours can be null if no artifact is loaded
            assert "model_age_hours" in receipt, f"Missing 'model_age_hours' in receipt: {receipt}"
            assert "stale" in receipt, f"Missing 'stale' in receipt: {receipt}"
            assert isinstance(receipt["stale"], bool), "stale should be bool"
        print(f"PASS: All boot receipts have model_age_hours and stale fields")

    def test_boot_receipts_have_required_fields(self, admin_session):
        """Boot receipts have all required ModelBootReceipt fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200
        data = resp.json()
        
        required_fields = [
            "layer", "ready", "artifact_present", "reason",
            "can_approve", "shadow_only", "model_age_hours", "stale", "booted_at",
        ]
        
        for receipt in data["receipts"]:
            for field in required_fields:
                assert field in receipt, f"Missing '{field}' in receipt: {receipt.get('layer')}"
        print(f"PASS: All boot receipts have required fields")

    def test_boot_receipts_strategist_auditor_present(self, admin_session):
        """Strategist and Auditor boot receipts are present."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200
        data = resp.json()
        
        layers = [r["layer"] for r in data["receipts"]]
        assert "strategist" in layers, "Missing strategist boot receipt"
        assert "auditor" in layers, "Missing auditor boot receipt"
        print(f"PASS: Strategist and Auditor boot receipts present")


# ── Pipeline Decide Endpoint Tests ───────────────────────────────


class TestPipelineDecideEndpoint:
    """POST /api/admin/ml/v2/pipeline/decide - dry-run with diagnostics."""

    def test_pipeline_decide_returns_200(self, admin_session):
        """Pipeline decide endpoint returns 200."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "pipeline" in data, "Missing 'pipeline' key"
        print(f"PASS: Pipeline decide returns 200")

    def test_pipeline_decide_has_trail(self, admin_session):
        """Pipeline decide response has trail array."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert resp.status_code == 200
        data = resp.json()
        
        pipeline = data["pipeline"]
        assert "trail" in pipeline, "Missing 'trail' in pipeline"
        assert isinstance(pipeline["trail"], list), "trail should be a list"
        print(f"PASS: Pipeline decide has trail with {len(pipeline['trail'])} entries")

    def test_pipeline_decide_strategist_diagnostics(self, admin_session):
        """Strategist diagnostics include feature_health_score, raw_confidence, effective_confidence."""
        # Provide good market data so strategist actually runs
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={
                "symbol": "AAPL",
                "lane": "equity",
                "market": {
                    "broker_uptime": 1.0,
                    "data_lag_ms": 50.0,
                    "error_rate": 0.0,
                    "spread_bps": 5.0,
                    "vix": 18.0,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        
        pipeline = data["pipeline"]
        trail = pipeline.get("trail", [])
        
        # Find strategist in trail
        strategist_verdict = None
        for v in trail:
            if v.get("layer") == "strategist":
                strategist_verdict = v
                break
        
        if strategist_verdict:
            diag = strategist_verdict.get("diagnostics", {})
            # If model was skipped (upstream NO_TRADE), these fields won't be present
            if not diag.get("model_skipped"):
                assert "feature_health_score" in diag, "Missing feature_health_score in strategist diagnostics"
                assert "raw_confidence" in diag, "Missing raw_confidence in strategist diagnostics"
                assert "effective_confidence" in diag, "Missing effective_confidence in strategist diagnostics"
                assert "clamped" in diag, "Missing clamped in strategist diagnostics"
                
                # Verify effective_confidence = raw_confidence * feature_health_score
                raw = diag["raw_confidence"]
                health = diag["feature_health_score"]
                eff = diag["effective_confidence"]
                expected = raw * health
                assert abs(eff - expected) < 1e-6, f"effective_confidence mismatch: {eff} != {raw} * {health}"
                print(f"PASS: Strategist diagnostics have feature_health fields, effective_confidence = raw * health")
            else:
                print(f"INFO: Strategist model was skipped (upstream NO_TRADE), diagnostics check skipped")
        else:
            print(f"INFO: Strategist not in trail (blocked earlier)")

    def test_pipeline_decide_crypto_lane(self, admin_session):
        """Pipeline decide works for crypto lane."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "BTC", "lane": "crypto"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["pipeline"]["lane"] == "crypto", "Lane mismatch"
        print(f"PASS: Pipeline decide works for crypto lane")

    def test_pipeline_decide_invalid_lane_returns_400(self, admin_session):
        """Pipeline decide returns 400 for invalid lane."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "invalid"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
        print(f"PASS: Pipeline decide returns 400 for invalid lane")


# ── Heartbeat Update After Pipeline Run ──────────────────────────


class TestHeartbeatUpdatesAfterPipelineRun:
    """Triggering /pipeline/decide must update the heartbeat for the lane."""

    def test_pipeline_run_updates_heartbeat(self, admin_session):
        """After pipeline decide, heartbeat shows recent run."""
        # Get initial heartbeat
        initial_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert initial_resp.status_code == 200
        initial_data = initial_resp.json()
        initial_equity_run = initial_data["lanes"]["equity"].get("last_pipeline_run_at")
        
        # Run pipeline for equity
        decide_resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert decide_resp.status_code == 200
        
        # Get updated heartbeat
        updated_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert updated_resp.status_code == 200
        updated_data = updated_resp.json()
        updated_equity_run = updated_data["lanes"]["equity"].get("last_pipeline_run_at")
        
        # The last_pipeline_run_at should be updated (or set if it was None)
        if initial_equity_run is None:
            assert updated_equity_run is not None, "last_pipeline_run_at should be set after pipeline run"
        else:
            # It should be the same or newer
            assert updated_equity_run >= initial_equity_run, "last_pipeline_run_at should be updated"
        
        print(f"PASS: Pipeline run updates heartbeat last_pipeline_run_at")

    def test_equity_run_does_not_affect_crypto_heartbeat(self, admin_session):
        """Equity-only run leaves crypto frozen state unchanged (lane isolation)."""
        # Get initial crypto state
        initial_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert initial_resp.status_code == 200
        initial_crypto_run = initial_resp.json()["lanes"]["crypto"].get("last_pipeline_run_at")
        
        # Run pipeline for equity only
        decide_resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert decide_resp.status_code == 200
        
        # Get updated heartbeat
        updated_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/heartbeat")
        assert updated_resp.status_code == 200
        updated_crypto_run = updated_resp.json()["lanes"]["crypto"].get("last_pipeline_run_at")
        
        # Crypto last_pipeline_run_at should be unchanged
        assert updated_crypto_run == initial_crypto_run, "Crypto heartbeat should not change after equity run"
        print(f"PASS: Equity run does not affect crypto heartbeat (lane isolation)")


# ── No Live Broker Calls Verification ────────────────────────────


class TestNoLiveBrokerCalls:
    """Verify no live broker calls / no enforce flag changes."""

    def test_pipeline_decide_is_dry_run(self, admin_session):
        """Pipeline decide is a dry-run (recorded=false by default)."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # recorded should be false by default
        assert data.get("recorded") is False, "recorded should be False by default"
        print(f"PASS: Pipeline decide is dry-run (recorded=False)")

    def test_heartbeat_is_read_only(self, admin_session):
        """Heartbeat endpoint is read-only (GET only)."""
        # POST should not be allowed
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/heartbeat", json={})
        assert resp.status_code in (405, 422), f"Expected 405/422 for POST, got {resp.status_code}"
        print(f"PASS: Heartbeat is read-only (POST returns {resp.status_code})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
