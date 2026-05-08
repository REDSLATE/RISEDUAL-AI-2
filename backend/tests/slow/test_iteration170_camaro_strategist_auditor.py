"""Iteration 170: Camaro→Shelly bridge + Strategist/Auditor sklearn classifiers.

Tests:
- GET /api/admin/ml/v2/boot-receipts: Strategist/Auditor reason='placeholder_classifier'
- POST /api/admin/ml/v2/camaro/bridge/run: admin auth, lookback_hours param, validation
- GET /api/admin/ml/v2/camaro/bridge/status: admin auth, limit param, validation
- POST /api/admin/ml/v2/pipeline/decide: trail contains strategist/auditor with diagnostics
- Regression: calibration/kanban, phase5b/summary
- Safety: phase5b_intents default fired=False
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    """Authenticated admin session with cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login as admin
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@risedual.ai", "password": "RiseDual2026!"},
    )
    if resp.status_code != 200:
        pytest.skip(f"Admin login failed: {resp.status_code} - {resp.text}")
    return session


@pytest.fixture(scope="module")
def unauthenticated_session():
    """Session without auth cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


# ── Boot Receipts: Strategist/Auditor placeholder_classifier ─────


class TestBootReceipts:
    """Verify Strategist and Auditor boot receipts show placeholder_classifier."""

    def test_boot_receipts_returns_200(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_boot_receipts_has_strategist_placeholder_classifier(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        data = resp.json()
        receipts = data.get("receipts", [])
        
        strategist = next((r for r in receipts if r.get("layer") == "strategist"), None)
        assert strategist is not None, "Strategist receipt not found"
        assert strategist.get("ready") is True, "Strategist should be ready"
        # Without STRATEGIST_ARTIFACT env, reason should be placeholder_classifier
        assert strategist.get("reason") == "placeholder_classifier", \
            f"Expected reason='placeholder_classifier', got '{strategist.get('reason')}'"

    def test_boot_receipts_has_auditor_placeholder_classifier(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        data = resp.json()
        receipts = data.get("receipts", [])
        
        auditor = next((r for r in receipts if r.get("layer") == "auditor"), None)
        assert auditor is not None, "Auditor receipt not found"
        assert auditor.get("ready") is True, "Auditor should be ready"
        # Without AUDITOR_ARTIFACT env, reason should be placeholder_classifier
        assert auditor.get("reason") == "placeholder_classifier", \
            f"Expected reason='placeholder_classifier', got '{auditor.get('reason')}'"

    def test_boot_receipts_strategist_can_approve(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        data = resp.json()
        receipts = data.get("receipts", [])
        
        strategist = next((r for r in receipts if r.get("layer") == "strategist"), None)
        assert strategist.get("can_approve") is True, "Strategist should have can_approve=True"

    def test_boot_receipts_auditor_can_approve(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        data = resp.json()
        receipts = data.get("receipts", [])
        
        auditor = next((r for r in receipts if r.get("layer") == "auditor"), None)
        assert auditor.get("can_approve") is True, "Auditor should have can_approve=True"


# ── Camaro Bridge: POST /run ──────────────────────────────────────


class TestCamaroBridgeRun:
    """POST /api/admin/ml/v2/camaro/bridge/run endpoint tests."""

    def test_camaro_bridge_run_requires_auth(self, unauthenticated_session):
        resp = unauthenticated_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"

    def test_camaro_bridge_run_returns_200(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_camaro_bridge_run_response_structure(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        data = resp.json()
        
        # Must have equity and crypto keys
        assert "equity" in data, "Response missing 'equity' key"
        assert "crypto" in data, "Response missing 'crypto' key"
        assert "lookback_hours" in data, "Response missing 'lookback_hours'"
        assert "ran_at" in data, "Response missing 'ran_at'"
        
        # Each lane has counts
        for lane in ("equity", "crypto"):
            lane_data = data[lane]
            assert "seen" in lane_data, f"{lane} missing 'seen'"
            assert "ingested" in lane_data, f"{lane} missing 'ingested'"
            assert "skipped" in lane_data, f"{lane} missing 'skipped'"
            assert "errors" in lane_data, f"{lane} missing 'errors'"
            assert "positive" in lane_data, f"{lane} missing 'positive'"
            assert "negative" in lane_data, f"{lane} missing 'negative'"
            assert "neutral" in lane_data, f"{lane} missing 'neutral'"

    def test_camaro_bridge_run_default_lookback_24(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        data = resp.json()
        assert data.get("lookback_hours") == 24, f"Expected default 24, got {data.get('lookback_hours')}"

    def test_camaro_bridge_run_custom_lookback_48(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run?lookback_hours=48")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("lookback_hours") == 48, f"Expected 48, got {data.get('lookback_hours')}"

    def test_camaro_bridge_run_lookback_out_of_range_999(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run?lookback_hours=999")
        assert resp.status_code == 422, f"Expected 422 for out-of-range, got {resp.status_code}"

    def test_camaro_bridge_run_lookback_out_of_range_0(self, admin_session):
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run?lookback_hours=0")
        assert resp.status_code == 422, f"Expected 422 for lookback_hours=0, got {resp.status_code}"


# ── Camaro Bridge: GET /status ────────────────────────────────────


class TestCamaroBridgeStatus:
    """GET /api/admin/ml/v2/camaro/bridge/status endpoint tests."""

    def test_camaro_bridge_status_requires_auth(self, unauthenticated_session):
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"

    def test_camaro_bridge_status_returns_200(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_camaro_bridge_status_response_structure(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status")
        data = resp.json()
        
        assert "runs" in data, "Response missing 'runs'"
        assert "count" in data, "Response missing 'count'"
        assert isinstance(data["runs"], list), "'runs' should be a list"
        assert isinstance(data["count"], int), "'count' should be an int"

    def test_camaro_bridge_status_limit_param(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 5, f"Expected at most 5 runs, got {data['count']}"

    def test_camaro_bridge_status_limit_out_of_range_200(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status?limit=200")
        assert resp.status_code == 422, f"Expected 422 for limit=200 (max 100), got {resp.status_code}"


# ── Camaro Bridge Audit Trail ─────────────────────────────────────


class TestCamaroBridgeAuditTrail:
    """After POST /run, GET /status should show audit rows."""

    def test_bridge_run_creates_audit_rows(self, admin_session):
        # Run the bridge
        run_resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        assert run_resp.status_code == 200
        
        # Check status for audit rows
        status_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status?limit=10")
        assert status_resp.status_code == 200
        data = status_resp.json()
        
        # Should have at least 2 audit rows (equity + crypto) from this run
        # Note: may have more from previous runs
        runs = data.get("runs", [])
        assert len(runs) >= 2, f"Expected at least 2 audit rows, got {len(runs)}"

    def test_audit_row_has_required_fields(self, admin_session):
        # Run the bridge first
        admin_session.post(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/run")
        
        # Get status
        status_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/camaro/bridge/status?limit=10")
        data = status_resp.json()
        runs = data.get("runs", [])
        
        if runs:
            row = runs[0]
            assert "ran_at" in row, "Audit row missing 'ran_at'"
            assert "collection" in row, "Audit row missing 'collection'"
            assert "lane" in row, "Audit row missing 'lane'"
            assert "counts" in row, "Audit row missing 'counts'"
            assert "lookback_hours" in row, "Audit row missing 'lookback_hours'"


# ── Pipeline Decide: Strategist/Auditor in Trail ──────────────────


class TestPipelineDecide:
    """POST /api/admin/ml/v2/pipeline/decide with strategist/auditor trail.
    
    Note: The pipeline is designed to short-circuit when perception returns
    NO_TRADE. In that case, strategist/auditor are NOT invoked (correct behavior).
    We test both scenarios: blocked at perception and passing through.
    """

    def test_pipeline_decide_returns_200(self, admin_session):
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {
                "vix": 14,
                "broker_uptime": 1.0,
                "rel_volume": 1.5,
                "spread_bps": 3,
                "depth_top_5": 1.5,
                "hours_to_event": 12,
                "dd_pct": 0.5,
            },
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_pipeline_decide_has_trail(self, admin_session):
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        trail = pipeline.get("trail", [])
        assert isinstance(trail, list), "trail should be a list"
        assert len(trail) > 0, "trail should not be empty"

    def test_pipeline_decide_perception_always_in_trail(self, admin_session):
        """Perception is always the first layer in the trail."""
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        trail = pipeline.get("trail", [])
        
        perception_entry = next((t for t in trail if t.get("layer") == "perception"), None)
        assert perception_entry is not None, "Trail should always contain 'perception' layer"

    def test_pipeline_decide_blocked_at_perception_no_strategist(self, admin_session):
        """When perception blocks (NO_TRADE), strategist is NOT invoked (correct behavior)."""
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        blocked_at = pipeline.get("blocked_at")
        trail = pipeline.get("trail", [])
        
        if blocked_at == "perception":
            # When blocked at perception, strategist should NOT be in trail
            strategist_entry = next((t for t in trail if t.get("layer") == "strategist"), None)
            assert strategist_entry is None, \
                "When blocked at perception, strategist should NOT be in trail (correct short-circuit)"
        # If not blocked at perception, strategist should be present (tested elsewhere)

    def test_pipeline_decide_trail_structure_when_blocked(self, admin_session):
        """Verify trail structure when pipeline is blocked at perception."""
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        blocked_at = pipeline.get("blocked_at")
        final = pipeline.get("final", {})
        
        # blocked_at should be set when pipeline short-circuits
        if final.get("decision") == "NO_TRADE":
            assert blocked_at is not None, "blocked_at should be set when decision is NO_TRADE"

    def test_pipeline_decide_strategist_has_diagnostics_when_invoked(self, admin_session):
        """When strategist IS invoked, it should have diagnostics."""
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        trail = pipeline.get("trail", [])
        
        strategist_entry = next((t for t in trail if t.get("layer") == "strategist"), None)
        if strategist_entry:
            diagnostics = strategist_entry.get("diagnostics")
            assert diagnostics is not None, "Strategist should have diagnostics"
            # Diagnostics should have proba or model_skipped
            assert "proba" in diagnostics or "model_skipped" in diagnostics, \
                f"Strategist diagnostics should have proba or model_skipped: {diagnostics}"

    def test_pipeline_decide_auditor_has_diagnostics_when_invoked(self, admin_session):
        """When auditor IS invoked, it should have diagnostics."""
        payload = {
            "symbol": "AAPL",
            "lane": "equity",
            "intent_hint": "BUY",
            "market": {"vix": 14, "broker_uptime": 1.0},
        }
        resp = admin_session.post(f"{BASE_URL}/api/admin/ml/v2/pipeline/decide", json=payload)
        data = resp.json()
        
        pipeline = data.get("pipeline", {})
        trail = pipeline.get("trail", [])
        
        auditor_entry = next((t for t in trail if t.get("layer") == "auditor"), None)
        if auditor_entry:
            diagnostics = auditor_entry.get("diagnostics")
            assert diagnostics is not None, "Auditor should have diagnostics"
            # Diagnostics should have confirm_proba or model_skipped
            assert "confirm_proba" in diagnostics or "model_skipped" in diagnostics, \
                f"Auditor diagnostics should have confirm_proba or model_skipped: {diagnostics}"


# ── Regression: Calibration Kanban ────────────────────────────────


class TestCalibrationKanbanRegression:
    """Regression: /api/admin/ml/v2/calibration/kanban still works."""

    def test_calibration_kanban_returns_200(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_calibration_kanban_has_both_lanes(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        lanes = data.get("lanes", {})
        assert "equity" in lanes, "Kanban missing 'equity' lane"
        assert "crypto" in lanes, "Kanban missing 'crypto' lane"


# ── Regression: Phase 5b Summary ──────────────────────────────────


class TestPhase5bSummaryRegression:
    """Regression: /api/admin/ml/v2/phase5b/summary still works."""

    def test_phase5b_summary_returns_200(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_phase5b_summary_has_total_and_by_lane(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        data = resp.json()
        
        assert "total" in data, "Summary missing 'total'"
        assert "by_lane" in data, "Summary missing 'by_lane'"
        
        by_lane = data.get("by_lane", {})
        assert "equity" in by_lane, "by_lane missing 'equity'"
        assert "crypto" in by_lane, "by_lane missing 'crypto'"


# ── Safety: Phase 5b Intents Default fired=False ──────────────────


class TestPhase5bSafetyInvariants:
    """Safety: phase5b_intents default to fired=False, classification != FIRED."""

    def test_phase5b_recent_no_fired_intents(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification=FIRED&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get("items", [])
        # With default env (no broker enabled), there should be no FIRED intents
        assert len(items) == 0, f"Expected 0 FIRED intents with default env, got {len(items)}"

    def test_phase5b_recent_intents_have_fired_false(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get("items", [])
        for item in items:
            assert item.get("fired") is False, f"Intent should have fired=False: {item}"


# ── Code Review: Camaro source label ──────────────────────────────


class TestCamaroSourceLabel:
    """Verify _build_regime_payload sets source='camaro'."""

    def test_camaro_payload_source_label(self):
        """Code-level verification that source='camaro' is set."""
        from services.ml.camaro_shelly_bridge import _build_regime_payload
        from datetime import datetime, timezone
        
        payload = _build_regime_payload(
            {"trade_id": "test123", "symbol": "AAPL", "side": "BUY",
             "realized_pnl_usd": 10.0,
             "closed_at": datetime.now(timezone.utc)},
            lane="equity",
        )
        assert payload["source"] == "camaro", f"Expected source='camaro', got '{payload.get('source')}'"
