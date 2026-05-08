"""
Test suite for Admin ML v2 endpoints (Phase 0-5a + closed-loop RoadGuard pair).

Tests:
- GET /api/admin/ml/v2/boot-receipts — 7 ready receipts
- GET /api/admin/ml/v2/decisions/summary — decision summary by stage
- GET /api/admin/ml/v2/decisions/recent — recent decisions list
- POST /api/admin/ml/v2/pipeline/decide — dry-run pipeline
- GET /api/admin/ml/v2/roadguard/pair-status — closed-loop RG pair status
- GET /api/admin/ml/v2/roadguard/decisions/recent — lane-specific RG decisions
- Auth: all endpoints require admin role
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


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


class TestAuthRequired:
    """All /api/admin/ml/v2/* endpoints require admin role."""
    
    def test_boot_receipts_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/boot-receipts without auth -> 401/403."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    
    def test_decisions_summary_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/decisions/summary without auth -> 401/403."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/decisions/summary")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    
    def test_decisions_recent_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/decisions/recent without auth -> 401/403."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/decisions/recent")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    
    def test_pipeline_decide_requires_auth(self, unauthenticated_session):
        """POST /api/admin/ml/v2/pipeline/decide without auth -> 401/403."""
        resp = unauthenticated_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity"},
        )
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    
    def test_roadguard_pair_status_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/roadguard/pair-status without auth -> 401/403."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    
    def test_roadguard_decisions_recent_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/roadguard/decisions/recent without auth -> 401/403."""
        resp = unauthenticated_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/decisions/recent",
            params={"lane": "equity"},
        )
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestBootReceipts:
    """GET /api/admin/ml/v2/boot-receipts — 7 ready receipts."""
    
    def test_boot_receipts_returns_7_ready(self, admin_session):
        """Should return 7 ready receipts (perception, strategist, auditor, fast_veto, shadow, equity executor, crypto executor)."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "count" in data, "Response missing 'count'"
        assert "ready" in data, "Response missing 'ready'"
        assert "disabled" in data, "Response missing 'disabled'"
        assert "receipts" in data, "Response missing 'receipts'"
        
        # Verify 7 ready receipts
        assert data["ready"] == 7, f"Expected 7 ready receipts, got {data['ready']}"
        assert data["disabled"] == 0, f"Expected 0 disabled receipts, got {data['disabled']}"
        
        # Verify receipt structure
        receipts = data["receipts"]
        assert len(receipts) == 7, f"Expected 7 receipts, got {len(receipts)}"
        
        # Check expected layer names are present
        # Note: Both executors report as "executor" layer, so we check for 6 unique layer names
        layer_names = [r.get("layer") for r in receipts]
        expected_layers = {"perception", "strategist", "auditor", "fast_veto", "shadow", "executor"}
        unique_layers = set(layer_names)
        assert expected_layers.issubset(unique_layers), f"Missing layers: {expected_layers - unique_layers}"
        
        # Verify we have 2 executor receipts (equity + crypto)
        executor_count = layer_names.count("executor")
        assert executor_count == 2, f"Expected 2 executor receipts, got {executor_count}"


class TestDecisionsSummary:
    """GET /api/admin/ml/v2/decisions/summary — decision summary by stage."""
    
    def test_decisions_summary_structure(self, admin_session):
        """Should return {approved, by_stage:{...}, total}."""
        resp = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/decisions/summary",
            params={"days": 7},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "approved" in data, "Response missing 'approved'"
        assert "by_stage" in data, "Response missing 'by_stage'"
        assert "total" in data, "Response missing 'total'"
        
        # Verify by_stage contains expected stages
        by_stage = data["by_stage"]
        expected_stages = ["shelly_recall", "perception", "strategist", "auditor", "fast_veto", "shadow", "executor", "roadguard"]
        for stage in expected_stages:
            assert stage in by_stage, f"by_stage missing '{stage}'"
        
        # Verify counts are integers
        assert isinstance(data["approved"], int), "approved should be int"
        assert isinstance(data["total"], int), "total should be int"


class TestDecisionsRecent:
    """GET /api/admin/ml/v2/decisions/recent — recent decisions list."""
    
    def test_decisions_recent_returns_list(self, admin_session):
        """Should return {items, count} — may be empty initially."""
        resp = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/decisions/recent",
            params={"limit": 10},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "items" in data, "Response missing 'items'"
        assert "count" in data, "Response missing 'count'"
        assert isinstance(data["items"], list), "items should be a list"
        assert isinstance(data["count"], int), "count should be int"


class TestPipelineDecide:
    """POST /api/admin/ml/v2/pipeline/decide — dry-run pipeline."""
    
    def test_pipeline_decide_equity_aapl(self, admin_session):
        """Equity lane AAPL should return pipeline envelope with non-empty trail."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={
                "symbol": "AAPL",
                "lane": "equity",
                "intent_hint": "BUY",
                "market": {},
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "pipeline" in data, "Response missing 'pipeline'"
        assert "roadguard_v2" in data, "Response missing 'roadguard_v2'"
        assert "recorded" in data, "Response missing 'recorded'"
        
        pipeline = data["pipeline"]
        assert pipeline["symbol"] == "AAPL", f"Expected symbol AAPL, got {pipeline['symbol']}"
        assert pipeline["lane"] == "equity", f"Expected lane equity, got {pipeline['lane']}"
        assert "final" in pipeline, "pipeline missing 'final'"
        assert "trail" in pipeline, "pipeline missing 'trail'"
        assert "blocked_at" in pipeline, "pipeline missing 'blocked_at'"
        assert "timestamp" in pipeline, "pipeline missing 'timestamp'"
        
        # Trail should be non-empty (at least perception ran)
        trail = pipeline["trail"]
        assert isinstance(trail, list), "trail should be a list"
        assert len(trail) > 0, "trail should be non-empty"
        
        # Each trail entry should have layer, decision, reason, confidence
        for entry in trail:
            assert "layer" in entry, "trail entry missing 'layer'"
            assert "decision" in entry, "trail entry missing 'decision'"
            assert "reason" in entry, "trail entry missing 'reason'"
            assert "confidence" in entry, "trail entry missing 'confidence'"
        
        # roadguard_v2 should be null (not exercised in dry-run without roadguard payload)
        assert data["roadguard_v2"] is None, "roadguard_v2 should be null without roadguard payload"
        assert data["recorded"] is False, "recorded should be false by default"
    
    def test_pipeline_decide_crypto_btc(self, admin_session):
        """Crypto lane BTC-USD should return pipeline envelope."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={
                "symbol": "BTC-USD",
                "lane": "crypto",
                "intent_hint": "BUY",
                "market": {},
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        pipeline = data["pipeline"]
        assert pipeline["symbol"] == "BTC-USD", f"Expected symbol BTC-USD, got {pipeline['symbol']}"
        assert pipeline["lane"] == "crypto", f"Expected lane crypto, got {pipeline['lane']}"
        assert len(pipeline["trail"]) > 0, "trail should be non-empty"
    
    def test_pipeline_decide_bad_lane_returns_400(self, admin_session):
        """Invalid lane (options) should return 400."""
        resp = admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={
                "symbol": "AAPL",
                "lane": "options",
                "intent_hint": "BUY",
                "market": {},
            },
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


class TestRoadGuardPairStatus:
    """GET /api/admin/ml/v2/roadguard/pair-status — closed-loop RG pair status."""
    
    def test_pair_status_structure(self, admin_session):
        """Should return {pair:{equity:{...}, crypto:{...}}}."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "pair" in data, "Response missing 'pair'"
        
        pair = data["pair"]
        assert "equity" in pair, "pair missing 'equity'"
        assert "crypto" in pair, "pair missing 'crypto'"
        
        # Verify equity entry
        equity = pair["equity"]
        assert equity["lane"] == "equity", f"Expected equity lane, got {equity['lane']}"
        assert equity["collection"] == "roadguard_equity_decisions", \
            f"Expected roadguard_equity_decisions, got {equity['collection']}"
        assert "total" in equity, "equity missing 'total'"
        assert "by_decision" in equity, "equity missing 'by_decision'"
        assert "last_verdict" in equity, "equity missing 'last_verdict'"
        
        # Verify by_decision structure
        by_decision = equity["by_decision"]
        assert "PASS" in by_decision, "by_decision missing 'PASS'"
        assert "REDUCE" in by_decision, "by_decision missing 'REDUCE'"
        assert "BLOCK" in by_decision, "by_decision missing 'BLOCK'"
        
        # Verify crypto entry
        crypto = pair["crypto"]
        assert crypto["lane"] == "crypto", f"Expected crypto lane, got {crypto['lane']}"
        assert crypto["collection"] == "roadguard_crypto_decisions", \
            f"Expected roadguard_crypto_decisions, got {crypto['collection']}"
        assert "total" in crypto, "crypto missing 'total'"
        assert "by_decision" in crypto, "crypto missing 'by_decision'"
        assert "last_verdict" in crypto, "crypto missing 'last_verdict'"
    
    def test_pair_status_no_cross_contamination(self, admin_session):
        """Equity collection MUST be roadguard_equity_decisions, crypto MUST be roadguard_crypto_decisions."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert resp.status_code == 200
        
        data = resp.json()
        pair = data["pair"]
        
        # Critical: NO cross-contamination
        assert pair["equity"]["collection"] == "roadguard_equity_decisions", \
            "CROSS-CONTAMINATION: equity pointing at wrong collection!"
        assert pair["crypto"]["collection"] == "roadguard_crypto_decisions", \
            "CROSS-CONTAMINATION: crypto pointing at wrong collection!"
        
        # Collections must be different
        assert pair["equity"]["collection"] != pair["crypto"]["collection"], \
            "CROSS-CONTAMINATION: equity and crypto using same collection!"


class TestRoadGuardDecisionsRecent:
    """GET /api/admin/ml/v2/roadguard/decisions/recent — lane-specific RG decisions."""
    
    def test_equity_lane_decisions(self, admin_session):
        """lane=equity should return {items, count, lane:'equity', collection:'roadguard_equity_decisions'}."""
        resp = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/decisions/recent",
            params={"lane": "equity", "limit": 10},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "items" in data, "Response missing 'items'"
        assert "count" in data, "Response missing 'count'"
        assert "lane" in data, "Response missing 'lane'"
        assert "collection" in data, "Response missing 'collection'"
        
        assert data["lane"] == "equity", f"Expected lane equity, got {data['lane']}"
        assert data["collection"] == "roadguard_equity_decisions", \
            f"Expected roadguard_equity_decisions, got {data['collection']}"
        
        # If items exist, verify they all have verdict.lane === 'equity'
        for item in data["items"]:
            if "verdict" in item and item["verdict"]:
                assert item["verdict"].get("lane") == "equity", \
                    f"Item has wrong lane: {item['verdict'].get('lane')}"
    
    def test_crypto_lane_decisions(self, admin_session):
        """lane=crypto should return {items, count, lane:'crypto', collection:'roadguard_crypto_decisions'}."""
        resp = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/decisions/recent",
            params={"lane": "crypto", "limit": 10},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data["lane"] == "crypto", f"Expected lane crypto, got {data['lane']}"
        assert data["collection"] == "roadguard_crypto_decisions", \
            f"Expected roadguard_crypto_decisions, got {data['collection']}"
        
        # If items exist, verify they all have verdict.lane === 'crypto'
        for item in data["items"]:
            if "verdict" in item and item["verdict"]:
                assert item["verdict"].get("lane") == "crypto", \
                    f"Item has wrong lane: {item['verdict'].get('lane')}"
    
    def test_invalid_lane_returns_422(self, admin_session):
        """lane=options should return 422 (invalid lane regex)."""
        resp = admin_session.get(
            f"{BASE_URL}/api/admin/ml/v2/roadguard/decisions/recent",
            params={"lane": "options", "limit": 10},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


class TestEndToEndClosedLoop:
    """End-to-end verification of closed-loop RG pair."""
    
    def test_pipeline_decide_does_not_write_to_roadguard_collections(self, admin_session):
        """
        POST /api/admin/ml/v2/pipeline/decide does NOT exercise shadow_wiring.
        The pair-status totals should remain unchanged after dry-run calls.
        """
        # Get initial pair status
        initial_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert initial_resp.status_code == 200
        initial_data = initial_resp.json()
        initial_equity_total = initial_data["pair"]["equity"]["total"]
        initial_crypto_total = initial_data["pair"]["crypto"]["total"]
        
        # Run equity dry-run
        admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "AAPL", "lane": "equity", "intent_hint": "BUY", "market": {}},
        )
        
        # Run crypto dry-run
        admin_session.post(
            f"{BASE_URL}/api/admin/ml/v2/pipeline/decide",
            json={"symbol": "BTC-USD", "lane": "crypto", "intent_hint": "BUY", "market": {}},
        )
        
        # Get pair status after dry-runs
        after_resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/pair-status")
        assert after_resp.status_code == 200
        after_data = after_resp.json()
        
        # Totals should be unchanged (dry-run doesn't write to RG collections)
        assert after_data["pair"]["equity"]["total"] == initial_equity_total, \
            "Equity total changed after dry-run — should NOT write to RG collection"
        assert after_data["pair"]["crypto"]["total"] == initial_crypto_total, \
            "Crypto total changed after dry-run — should NOT write to RG collection"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
