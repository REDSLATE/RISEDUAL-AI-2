"""
Test suite for Calibration Kanban endpoint (GET /api/admin/ml/v2/calibration/kanban).

Tests:
- Auth: requires admin role (401/403 without cookie)
- Response structure: version, generated_at, sample_window, promotion_thresholds, lanes
- Lane cards: equity and crypto with correct structure
- Lane isolation: equity uses roadguard_equity_decisions, crypto uses roadguard_crypto_decisions
- Checklist: 6 items in exact order with correct keys
- READ-ONLY guarantee: no writes to any collection after multiple calls
- Regression: existing endpoints still work
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"

# Expected checklist keys in exact order per user spec
EXPECTED_CHECKLIST_KEYS = [
    "min_receipts",
    "zero_contamination",
    "zero_false_blocks",
    "broker_health_stable",
    "correct_collection",
    "enforce_off",
]


@pytest.fixture(scope="module")
def admin_session():
    """Authenticate as admin and return session with cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login to get auth cookies
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
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


class TestCalibrationKanbanAuth:
    """Auth tests for /api/admin/ml/v2/calibration/kanban."""
    
    def test_kanban_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/calibration/kanban without auth -> 401/403."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestCalibrationKanbanResponseStructure:
    """Response structure tests for /api/admin/ml/v2/calibration/kanban."""
    
    def test_kanban_returns_200_with_admin_auth(self, admin_session):
        """GET /api/admin/ml/v2/calibration/kanban with admin auth -> 200."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    def test_kanban_top_level_structure(self, admin_session):
        """Response has version, generated_at, sample_window, promotion_thresholds, lanes."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        assert resp.status_code == 200
        
        data = resp.json()
        
        # Top-level keys
        assert "version" in data, "Response missing 'version'"
        assert data["version"] == 1, f"Expected version 1, got {data['version']}"
        
        assert "generated_at" in data, "Response missing 'generated_at'"
        assert isinstance(data["generated_at"], str), "generated_at should be ISO string"
        
        assert "sample_window" in data, "Response missing 'sample_window'"
        assert "promotion_thresholds" in data, "Response missing 'promotion_thresholds'"
        assert "lanes" in data, "Response missing 'lanes'"
    
    def test_sample_window_structure(self, admin_session):
        """sample_window has days, from, to."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        sw = data["sample_window"]
        assert "days" in sw, "sample_window missing 'days'"
        assert "from" in sw, "sample_window missing 'from'"
        assert "to" in sw, "sample_window missing 'to'"
        
        assert isinstance(sw["days"], int), "days should be int"
        assert isinstance(sw["from"], str), "from should be ISO string"
        assert isinstance(sw["to"], str), "to should be ISO string"
    
    def test_promotion_thresholds_structure(self, admin_session):
        """promotion_thresholds has min_receipts_per_lane, broker_health_block_ratio_max."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        pt = data["promotion_thresholds"]
        assert "min_receipts_per_lane" in pt, "promotion_thresholds missing 'min_receipts_per_lane'"
        assert "broker_health_block_ratio_max" in pt, "promotion_thresholds missing 'broker_health_block_ratio_max'"
        
        assert isinstance(pt["min_receipts_per_lane"], int), "min_receipts_per_lane should be int"
        assert isinstance(pt["broker_health_block_ratio_max"], float), "broker_health_block_ratio_max should be float"


class TestCalibrationKanbanLaneCards:
    """Lane card structure tests."""
    
    def test_lanes_has_equity_and_crypto(self, admin_session):
        """lanes contains equity and crypto keys."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        lanes = data["lanes"]
        assert "equity" in lanes, "lanes missing 'equity'"
        assert "crypto" in lanes, "lanes missing 'crypto'"
    
    def test_equity_card_structure(self, admin_session):
        """Equity card has all required fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity = data["lanes"]["equity"]
        
        # Required fields
        assert equity["lane"] == "equity", f"Expected lane 'equity', got {equity['lane']}"
        assert "phase" in equity, "equity missing 'phase'"
        assert equity["phase"] in ("Shadow", "Calibrate", "Enforce"), f"Invalid phase: {equity['phase']}"
        
        assert "enforce_flag" in equity, "equity missing 'enforce_flag'"
        assert isinstance(equity["enforce_flag"], bool), "enforce_flag should be bool"
        
        assert "promotion_state" in equity, "equity missing 'promotion_state'"
        assert equity["promotion_state"] in ("Eligible", "Blocked", "Ready for Review"), \
            f"Invalid promotion_state: {equity['promotion_state']}"
        
        assert "blockers" in equity, "equity missing 'blockers'"
        assert isinstance(equity["blockers"], list), "blockers should be list"
        
        assert "receipts_collection" in equity, "equity missing 'receipts_collection'"
        assert equity["receipts_collection"] == "alpha_decision_log", \
            f"Expected receipts_collection 'alpha_decision_log', got {equity['receipts_collection']}"
        
        assert "rg_collection" in equity, "equity missing 'rg_collection'"
        assert "sample_window" in equity, "equity missing 'sample_window'"
        assert "metrics" in equity, "equity missing 'metrics'"
        assert "checklist" in equity, "equity missing 'checklist'"
    
    def test_crypto_card_structure(self, admin_session):
        """Crypto card has all required fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        crypto = data["lanes"]["crypto"]
        
        assert crypto["lane"] == "crypto", f"Expected lane 'crypto', got {crypto['lane']}"
        assert "phase" in crypto, "crypto missing 'phase'"
        assert "enforce_flag" in crypto, "crypto missing 'enforce_flag'"
        assert "promotion_state" in crypto, "crypto missing 'promotion_state'"
        assert "blockers" in crypto, "crypto missing 'blockers'"
        assert "receipts_collection" in crypto, "crypto missing 'receipts_collection'"
        assert crypto["receipts_collection"] == "alpha_decision_log"
        assert "rg_collection" in crypto, "crypto missing 'rg_collection'"
        assert "sample_window" in crypto, "crypto missing 'sample_window'"
        assert "metrics" in crypto, "crypto missing 'metrics'"
        assert "checklist" in crypto, "crypto missing 'checklist'"


class TestCalibrationKanbanLaneIsolation:
    """Lane isolation tests — NO cross-contamination."""
    
    def test_equity_rg_collection_is_correct(self, admin_session):
        """Equity card rg_collection MUST be 'roadguard_equity_decisions'."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity = data["lanes"]["equity"]
        assert equity["rg_collection"] == "roadguard_equity_decisions", \
            f"CROSS-CONTAMINATION: equity rg_collection is {equity['rg_collection']}, expected roadguard_equity_decisions"
    
    def test_crypto_rg_collection_is_correct(self, admin_session):
        """Crypto card rg_collection MUST be 'roadguard_crypto_decisions'."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        crypto = data["lanes"]["crypto"]
        assert crypto["rg_collection"] == "roadguard_crypto_decisions", \
            f"CROSS-CONTAMINATION: crypto rg_collection is {crypto['rg_collection']}, expected roadguard_crypto_decisions"
    
    def test_no_cross_talk_between_lanes(self, admin_session):
        """Equity and crypto collections must be different."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity_coll = data["lanes"]["equity"]["rg_collection"]
        crypto_coll = data["lanes"]["crypto"]["rg_collection"]
        
        assert equity_coll != crypto_coll, \
            f"CROSS-CONTAMINATION: equity and crypto using same collection: {equity_coll}"


class TestCalibrationKanbanMetrics:
    """Metrics structure tests."""
    
    def test_equity_metrics_structure(self, admin_session):
        """Equity metrics has receipts and roadguard sub-objects."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        metrics = data["lanes"]["equity"]["metrics"]
        
        # Receipts metrics
        assert "receipts" in metrics, "metrics missing 'receipts'"
        receipts = metrics["receipts"]
        assert "total" in receipts, "receipts missing 'total'"
        assert "approved" in receipts, "receipts missing 'approved'"
        assert "no_trade" in receipts, "receipts missing 'no_trade'"
        assert "by_stage" in receipts, "receipts missing 'by_stage'"
        assert "false_blocks" in receipts, "receipts missing 'false_blocks'"
        
        # RoadGuard metrics
        assert "roadguard" in metrics, "metrics missing 'roadguard'"
        rg = metrics["roadguard"]
        assert "total" in rg, "roadguard missing 'total'"
        assert "by_decision" in rg, "roadguard missing 'by_decision'"
        assert "lane_mismatch" in rg, "roadguard missing 'lane_mismatch'"
        assert "broker_health_blocks" in rg, "roadguard missing 'broker_health_blocks'"
        assert "exposure_cap_blocks" in rg, "roadguard missing 'exposure_cap_blocks'"
        assert "duplicate_symbol_blocks" in rg, "roadguard missing 'duplicate_symbol_blocks'"
        assert "cross_contamination" in rg, "roadguard missing 'cross_contamination'"
        
        # by_decision structure
        by_decision = rg["by_decision"]
        assert "PASS" in by_decision, "by_decision missing 'PASS'"
        assert "REDUCE" in by_decision, "by_decision missing 'REDUCE'"
        assert "BLOCK" in by_decision, "by_decision missing 'BLOCK'"


class TestCalibrationKanbanChecklist:
    """Checklist structure and key order tests."""
    
    def test_checklist_has_6_items(self, admin_session):
        """Each lane checklist has exactly 6 items."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        for lane in ("equity", "crypto"):
            checklist = data["lanes"][lane]["checklist"]
            assert len(checklist) == 6, f"{lane} checklist has {len(checklist)} items, expected 6"
    
    def test_checklist_keys_in_exact_order(self, admin_session):
        """Checklist items have exactly these keys in this order: min_receipts, zero_contamination, zero_false_blocks, broker_health_stable, correct_collection, enforce_off."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        for lane in ("equity", "crypto"):
            checklist = data["lanes"][lane]["checklist"]
            actual_keys = [item["key"] for item in checklist]
            
            assert actual_keys == EXPECTED_CHECKLIST_KEYS, \
                f"{lane} checklist keys mismatch. Expected: {EXPECTED_CHECKLIST_KEYS}, Got: {actual_keys}"
    
    def test_checklist_item_shape(self, admin_session):
        """Each checklist item has shape {key, label, status, value, threshold, note}."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        for lane in ("equity", "crypto"):
            checklist = data["lanes"][lane]["checklist"]
            for item in checklist:
                assert "key" in item, f"{lane} checklist item missing 'key'"
                assert "label" in item, f"{lane} checklist item missing 'label'"
                assert "status" in item, f"{lane} checklist item missing 'status'"
                assert "value" in item, f"{lane} checklist item missing 'value'"
                assert "threshold" in item, f"{lane} checklist item missing 'threshold'"
                assert "note" in item, f"{lane} checklist item missing 'note'"
                
                # Status must be pass|fail|n/a
                assert item["status"] in ("pass", "fail", "n/a"), \
                    f"{lane} checklist item {item['key']} has invalid status: {item['status']}"


class TestCalibrationKanbanCurrentState:
    """Tests for expected current state (low/no receipts)."""
    
    def test_equity_phase_is_shadow_with_sparse_data(self, admin_session):
        """With current sparse data, equity should show phase='Shadow'."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity = data["lanes"]["equity"]
        # With low receipts, phase should be Shadow
        assert equity["phase"] == "Shadow", \
            f"Expected phase 'Shadow' with sparse data, got {equity['phase']}"
    
    def test_equity_promotion_state_is_blocked_with_sparse_data(self, admin_session):
        """With current sparse data, equity should show promotion_state='Blocked'."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity = data["lanes"]["equity"]
        assert equity["promotion_state"] == "Blocked", \
            f"Expected promotion_state 'Blocked' with sparse data, got {equity['promotion_state']}"
    
    def test_equity_blockers_contains_min_receipts(self, admin_session):
        """With current sparse data, equity blockers should contain 'min_receipts'."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        equity = data["lanes"]["equity"]
        assert "min_receipts" in equity["blockers"], \
            f"Expected 'min_receipts' in blockers, got {equity['blockers']}"
    
    def test_enforce_flag_is_false_on_both_lanes(self, admin_session):
        """enforce_flag should be false on both lanes (defaults from .env)."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        data = resp.json()
        
        for lane in ("equity", "crypto"):
            assert data["lanes"][lane]["enforce_flag"] is False, \
                f"{lane} enforce_flag should be False, got {data['lanes'][lane]['enforce_flag']}"


class TestCalibrationKanbanReadOnlyGuarantee:
    """READ-ONLY guarantee tests — endpoint produces ZERO writes."""
    
    def test_kanban_does_not_mutate_doc_counts(self, admin_session):
        """
        After hitting GET /api/admin/ml/v2/calibration/kanban N times,
        document counts in alpha_decision_log, roadguard_equity_decisions,
        roadguard_crypto_decisions must NOT change.
        """
        # Get initial counts via decisions/summary and roadguard/pair-status
        initial_summary = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/decisions/summary",
            params={"days": 30},
        ).json()
        initial_total_decisions = initial_summary.get("total", 0)
        
        initial_pair = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status"
        ).json()
        initial_equity_rg = initial_pair["pair"]["equity"]["total"]
        initial_crypto_rg = initial_pair["pair"]["crypto"]["total"]
        
        # Call kanban endpoint multiple times
        for _ in range(5):
            resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
            assert resp.status_code == 200
        
        # Get counts after kanban calls
        after_summary = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/decisions/summary",
            params={"days": 30},
        ).json()
        after_total_decisions = after_summary.get("total", 0)
        
        after_pair = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status"
        ).json()
        after_equity_rg = after_pair["pair"]["equity"]["total"]
        after_crypto_rg = after_pair["pair"]["crypto"]["total"]
        
        # Assert no changes
        assert after_total_decisions == initial_total_decisions, \
            f"alpha_decision_log count changed: {initial_total_decisions} -> {after_total_decisions}"
        assert after_equity_rg == initial_equity_rg, \
            f"roadguard_equity_decisions count changed: {initial_equity_rg} -> {after_equity_rg}"
        assert after_crypto_rg == initial_crypto_rg, \
            f"roadguard_crypto_decisions count changed: {initial_crypto_rg} -> {after_crypto_rg}"
    
    def test_enforce_flag_unchanged_after_kanban_access(self, admin_session):
        """
        enforce flag (verified by inspecting kanban response) must NOT change
        after kanban access.
        """
        # Get initial enforce flags
        initial_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        initial_data = initial_resp.json()
        initial_equity_enforce = initial_data["lanes"]["equity"]["enforce_flag"]
        initial_crypto_enforce = initial_data["lanes"]["crypto"]["enforce_flag"]
        
        # Call kanban multiple times
        for _ in range(3):
            admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        
        # Get enforce flags after
        after_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        after_data = after_resp.json()
        
        assert after_data["lanes"]["equity"]["enforce_flag"] == initial_equity_enforce, \
            "Equity enforce_flag changed after kanban access!"
        assert after_data["lanes"]["crypto"]["enforce_flag"] == initial_crypto_enforce, \
            "Crypto enforce_flag changed after kanban access!"


class TestRegressionExistingEndpoints:
    """Quick regression tests on existing endpoints to confirm nothing broke."""
    
    def test_boot_receipts_still_works(self, admin_session):
        """/boot-receipts still returns 7 ready receipts."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200, f"boot-receipts failed: {resp.status_code}"
        
        data = resp.json()
        assert data["ready"] == 7, f"Expected 7 ready receipts, got {data['ready']}"
    
    def test_roadguard_pair_status_still_works(self, admin_session):
        """/roadguard/pair-status still works."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert resp.status_code == 200, f"pair-status failed: {resp.status_code}"
        
        data = resp.json()
        assert "pair" in data
        assert "equity" in data["pair"]
        assert "crypto" in data["pair"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
