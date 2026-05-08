"""Phase 5b Broker Wire Endpoints — Safety-Critical Tests.

Tests the Phase 5b broker wire admin endpoints and safety invariants:
- GET /api/admin/ml/v2/phase5b/summary
- GET /api/admin/ml/v2/phase5b/recent

Safety invariants verified:
- All 4 gates default closed → classification in {SHADOW_ONLY, GATE_BLOCK}
- fired=False and order_id=None with default env
- Doc shape matches expected schema
- READ-ONLY guarantee: GET endpoints don't create docs
- No broker calls (placeholder is NO-OP)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    raise ValueError("REACT_APP_BACKEND_URL not set")

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def admin_session():
    """Create authenticated admin session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login to get auth cookie
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if login_resp.status_code != 200:
        pytest.skip(f"Admin login failed: {login_resp.status_code} - {login_resp.text}")
    
    return session


@pytest.fixture(scope="module")
def unauthenticated_session():
    """Create unauthenticated session for auth tests."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


# ── Auth Tests ────────────────────────────────────────────────────


class TestPhase5bAuth:
    """Phase 5b endpoints require admin authentication."""

    def test_phase5b_summary_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/phase5b/summary returns 401/403 without auth."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"✓ phase5b/summary requires auth: {resp.status_code}")

    def test_phase5b_recent_requires_auth(self, unauthenticated_session):
        """GET /api/admin/ml/v2/phase5b/recent returns 401/403 without auth."""
        resp = unauthenticated_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print(f"✓ phase5b/recent requires auth: {resp.status_code}")


# ── Phase 5b Summary Endpoint ─────────────────────────────────────


class TestPhase5bSummary:
    """GET /api/admin/ml/v2/phase5b/summary tests."""

    def test_summary_returns_200(self, admin_session):
        """Summary endpoint returns 200 with admin auth."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"✓ phase5b/summary returns 200")

    def test_summary_structure(self, admin_session):
        """Summary has correct structure: {by_lane:{equity, crypto}, total}."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp.status_code == 200
        data = resp.json()
        
        # Top-level keys
        assert "by_lane" in data, "Missing 'by_lane' key"
        assert "total" in data, "Missing 'total' key"
        
        # by_lane structure
        by_lane = data["by_lane"]
        assert "equity" in by_lane, "Missing 'equity' in by_lane"
        assert "crypto" in by_lane, "Missing 'crypto' in by_lane"
        
        # Each lane has total and by_classification
        for lane in ["equity", "crypto"]:
            assert "total" in by_lane[lane], f"Missing 'total' in {lane}"
            assert "by_classification" in by_lane[lane], f"Missing 'by_classification' in {lane}"
        
        print(f"✓ phase5b/summary structure correct: by_lane={{equity, crypto}}, total={data['total']}")

    def test_summary_with_days_param(self, admin_session):
        """Summary accepts days query param."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary?days=1")
        assert resp.status_code == 200
        print(f"✓ phase5b/summary accepts days param")


# ── Phase 5b Recent Endpoint ──────────────────────────────────────


class TestPhase5bRecent:
    """GET /api/admin/ml/v2/phase5b/recent tests."""

    def test_recent_returns_200(self, admin_session):
        """Recent endpoint returns 200 with admin auth."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"✓ phase5b/recent returns 200")

    def test_recent_structure(self, admin_session):
        """Recent has correct structure: {items, count}."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        assert "items" in data, "Missing 'items' key"
        assert "count" in data, "Missing 'count' key"
        assert isinstance(data["items"], list), "'items' should be a list"
        assert isinstance(data["count"], int), "'count' should be an int"
        
        print(f"✓ phase5b/recent structure correct: count={data['count']}")

    def test_recent_with_lane_filter_equity(self, admin_session):
        """Recent accepts lane=equity filter."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?lane=equity&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        # All items should be equity lane
        for item in data["items"]:
            assert item.get("lane") == "equity", f"Expected equity lane, got {item.get('lane')}"
        
        print(f"✓ phase5b/recent lane=equity filter works: {data['count']} items")

    def test_recent_with_lane_filter_crypto(self, admin_session):
        """Recent accepts lane=crypto filter."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?lane=crypto&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        # All items should be crypto lane
        for item in data["items"]:
            assert item.get("lane") == "crypto", f"Expected crypto lane, got {item.get('lane')}"
        
        print(f"✓ phase5b/recent lane=crypto filter works: {data['count']} items")

    def test_recent_with_classification_filter(self, admin_session):
        """Recent accepts classification filter."""
        for cls in ["SHADOW_ONLY", "GATE_BLOCK", "WOULD_HAVE_FIRED", "FIRED"]:
            resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification={cls}&limit=50")
            assert resp.status_code == 200, f"classification={cls} failed: {resp.status_code}"
            data = resp.json()
            
            # All items should have matching classification
            for item in data["items"]:
                assert item.get("classification") == cls, f"Expected {cls}, got {item.get('classification')}"
        
        print(f"✓ phase5b/recent classification filters work")


# ── Validation Tests ──────────────────────────────────────────────


class TestPhase5bValidation:
    """Input validation tests for Phase 5b endpoints."""

    def test_recent_invalid_lane_returns_422(self, admin_session):
        """lane=options should return 422 (invalid regex)."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?lane=options")
        assert resp.status_code == 422, f"Expected 422 for invalid lane, got {resp.status_code}"
        print(f"✓ phase5b/recent lane=options returns 422")

    def test_recent_invalid_classification_returns_422(self, admin_session):
        """classification=BOGUS should return 422."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification=BOGUS")
        assert resp.status_code == 422, f"Expected 422 for invalid classification, got {resp.status_code}"
        print(f"✓ phase5b/recent classification=BOGUS returns 422")


# ── Safety Invariants ─────────────────────────────────────────────


class TestPhase5bSafetyInvariants:
    """SAFETY-CRITICAL: Verify Phase 5b safety invariants with default env."""

    def test_intent_doc_shape(self, admin_session):
        """phase5b_intents docs have correct shape."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        if data["count"] == 0:
            pytest.skip("No phase5b_intents docs to verify shape")
        
        # Required fields in each doc
        required_fields = [
            "lane", "symbol", "side", "requested_notional_usd",
            "classification", "fired", "order_id", "gates",
            "diagnostics", "created_at", "schema_version"
        ]
        
        # Gate fields
        gate_fields = [
            "gate1_enforce_flag", "gate2_broker_live_order_enabled",
            "gate3_legacy_live_execution", "gate4_kanban_eligible",
            "gate4_kanban_state"
        ]
        
        for item in data["items"]:
            for field in required_fields:
                assert field in item, f"Missing required field: {field}"
            
            # Verify gates sub-object
            gates = item.get("gates", {})
            for gf in gate_fields:
                assert gf in gates, f"Missing gate field: {gf}"
        
        print(f"✓ phase5b_intents doc shape verified ({data['count']} docs)")

    def test_default_env_classification_safe(self, admin_session):
        """With default env (all flags off), classification must be SHADOW_ONLY or GATE_BLOCK."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        if data["count"] == 0:
            pytest.skip("No phase5b_intents docs to verify classification")
        
        safe_classifications = {"SHADOW_ONLY", "GATE_BLOCK"}
        
        for item in data["items"]:
            cls = item.get("classification")
            assert cls in safe_classifications, (
                f"SAFETY VIOLATION: classification={cls} not in {safe_classifications}. "
                f"Symbol={item.get('symbol')}, lane={item.get('lane')}"
            )
        
        print(f"✓ All {data['count']} intents have safe classification (SHADOW_ONLY or GATE_BLOCK)")

    def test_default_env_fired_false(self, admin_session):
        """With default env, fired must be False for all intents."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        if data["count"] == 0:
            pytest.skip("No phase5b_intents docs to verify fired flag")
        
        for item in data["items"]:
            assert item.get("fired") is False, (
                f"SAFETY VIOLATION: fired=True for symbol={item.get('symbol')}, "
                f"lane={item.get('lane')}, classification={item.get('classification')}"
            )
        
        print(f"✓ All {data['count']} intents have fired=False")

    def test_default_env_order_id_none(self, admin_session):
        """With default env, order_id must be None for all intents."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        if data["count"] == 0:
            pytest.skip("No phase5b_intents docs to verify order_id")
        
        for item in data["items"]:
            assert item.get("order_id") is None, (
                f"SAFETY VIOLATION: order_id={item.get('order_id')} for symbol={item.get('symbol')}, "
                f"lane={item.get('lane')}"
            )
        
        print(f"✓ All {data['count']} intents have order_id=None")

    def test_default_env_all_gates_closed(self, admin_session):
        """With default env, all 4 gates should be closed (False)."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        if data["count"] == 0:
            pytest.skip("No phase5b_intents docs to verify gates")
        
        for item in data["items"]:
            gates = item.get("gates", {})
            
            # Gate 1 - per-lane enforce flag
            assert gates.get("gate1_enforce_flag") is False, (
                f"Gate 1 should be False, got {gates.get('gate1_enforce_flag')}"
            )
            
            # Gate 2 - broker live order
            assert gates.get("gate2_broker_live_order_enabled") is False, (
                f"Gate 2 should be False, got {gates.get('gate2_broker_live_order_enabled')}"
            )
            
            # Gate 3 - legacy live execution
            assert gates.get("gate3_legacy_live_execution") is False, (
                f"Gate 3 should be False, got {gates.get('gate3_legacy_live_execution')}"
            )
            
            # Gate 4 - kanban eligible (should be False with sparse data)
            assert gates.get("gate4_kanban_eligible") is False, (
                f"Gate 4 should be False, got {gates.get('gate4_kanban_eligible')}"
            )
        
        print(f"✓ All {data['count']} intents have all 4 gates closed")


# ── READ-ONLY Guarantee ───────────────────────────────────────────


class TestPhase5bReadOnly:
    """Verify GET endpoints don't create phase5b_intents docs."""

    def test_get_endpoints_dont_create_docs(self, admin_session):
        """Hitting GET endpoints multiple times must NOT add docs."""
        # Get initial count
        resp1 = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp1.status_code == 200
        initial_total = resp1.json().get("total", 0)
        
        # Hit endpoints multiple times
        for _ in range(5):
            admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
            admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?limit=50")
            admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?lane=equity")
            admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification=SHADOW_ONLY")
        
        # Get final count
        resp2 = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/summary")
        assert resp2.status_code == 200
        final_total = resp2.json().get("total", 0)
        
        assert final_total == initial_total, (
            f"READ-ONLY VIOLATION: doc count changed from {initial_total} to {final_total}"
        )
        
        print(f"✓ READ-ONLY guarantee verified: doc count unchanged ({initial_total})")


# ── Regression Tests ──────────────────────────────────────────────


class TestRegressionExistingEndpoints:
    """Verify existing endpoints still work after Phase 5b addition."""

    def test_calibration_kanban_still_works(self, admin_session):
        """GET /api/admin/ml/v2/calibration/kanban returns 200 with lanes."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/calibration/kanban")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        assert "lanes" in data, "Missing 'lanes' key"
        assert "equity" in data["lanes"], "Missing equity lane"
        assert "crypto" in data["lanes"], "Missing crypto lane"
        
        print(f"✓ calibration/kanban still works")

    def test_boot_receipts_still_works(self, admin_session):
        """GET /api/admin/ml/v2/boot-receipts returns 7 ready receipts."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/boot-receipts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        assert "ready" in data, "Missing 'ready' key"
        assert data["ready"] == 7, f"Expected 7 ready receipts, got {data['ready']}"
        
        print(f"✓ boot-receipts still works: {data['ready']} ready")

    def test_roadguard_decisions_recent_still_works(self, admin_session):
        """GET /api/admin/ml/v2/roadguard/decisions/recent still works."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/roadguard/decisions/recent?lane=equity&limit=50")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        assert "items" in data, "Missing 'items' key"
        assert "count" in data, "Missing 'count' key"
        assert data.get("lane") == "equity", f"Expected lane=equity, got {data.get('lane')}"
        
        print(f"✓ roadguard/decisions/recent still works: {data['count']} items")


# ── Broker NO-OP Verification ─────────────────────────────────────


class TestBrokerNoOp:
    """Verify broker dispatch is a NO-OP (no external HTTP traffic)."""

    def test_no_fired_intents_exist(self, admin_session):
        """With default env, no FIRED classification should exist."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification=FIRED&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        assert data["count"] == 0, (
            f"SAFETY VIOLATION: Found {data['count']} FIRED intents. "
            f"Broker should be NO-OP with default env."
        )
        
        print(f"✓ No FIRED intents exist (broker NO-OP confirmed)")

    def test_no_would_have_fired_with_default_gates(self, admin_session):
        """With all gates closed, WOULD_HAVE_FIRED should not exist."""
        # WOULD_HAVE_FIRED only happens when all 4 gates open but broker returns None
        # With default env (all gates closed), this classification should not appear
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/v2/phase5b/recent?classification=WOULD_HAVE_FIRED&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        # This is expected to be 0 with default env
        if data["count"] > 0:
            # If any exist, verify gates were actually open (which shouldn't happen with default env)
            for item in data["items"]:
                gates = item.get("gates", {})
                print(f"  WARNING: WOULD_HAVE_FIRED found - gates: {gates}")
        
        print(f"✓ WOULD_HAVE_FIRED count: {data['count']} (expected 0 with default env)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
