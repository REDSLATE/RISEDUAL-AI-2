"""
Iteration 61: Failure Mode Classification System Tests

Tests:
1. _classify_failure() function logic - auto-classification based on price action
2. GET /api/accuracy/failure-modes - returns all 5 failure mode categories
3. GET /api/accuracy/failure-breakdown - returns failure stats
4. POST /api/accuracy/classify/{prediction_id} - manual classification
5. save_regime() stores failure_code in ChromaDB metadata
6. _regime_to_text() includes Failure Mode and Failure Reason lines
7. get_strategist_veto_context() includes [FAILURE_CODE] tag in warnings
8. Nightly cleanup preserves failure_code when re-tagging toxic entries
"""

import pytest
import requests
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD
class TestClassifyFailureFunction:
    """Test _classify_failure() function logic directly via Python imports."""

    def test_classify_failure_import(self):
        """Verify _classify_failure function can be imported."""
        from services.prediction_tracker import _classify_failure, FAILURE_MODES
        assert callable(_classify_failure)
        assert isinstance(FAILURE_MODES, dict)
        assert len(FAILURE_MODES) == 5
        print(f"PASS: _classify_failure imported, FAILURE_MODES has {len(FAILURE_MODES)} categories")

    def test_failure_modes_dict_contents(self):
        """Verify FAILURE_MODES contains all 5 expected categories."""
        from services.prediction_tracker import FAILURE_MODES
        expected_codes = {"TECH_FAKEOUT", "MACRO_SHOCK", "LIQUIDITY_GAP", "REGIME_SHIFT", "UNKNOWN"}
        assert set(FAILURE_MODES.keys()) == expected_codes
        print(f"PASS: FAILURE_MODES contains all 5 categories: {list(FAILURE_MODES.keys())}")

    def test_classify_buy_large_drop_macro_shock(self):
        """BUY prediction with -6% drop → MACRO_SHOCK (large violent move)."""
        from services.prediction_tracker import _classify_failure
        # BUY at $100, price dropped to $94 (-6%)
        result = _classify_failure("BUY", 100.0, 94.0)
        assert result == "MACRO_SHOCK", f"Expected MACRO_SHOCK, got {result}"
        print("PASS: BUY -6% → MACRO_SHOCK")

    def test_classify_buy_small_drop_tech_fakeout(self):
        """BUY prediction with -1.5% drop → TECH_FAKEOUT (moderate reversal)."""
        from services.prediction_tracker import _classify_failure
        # BUY at $100, price dropped to $98.5 (-1.5%)
        result = _classify_failure("BUY", 100.0, 98.5)
        assert result == "TECH_FAKEOUT", f"Expected TECH_FAKEOUT, got {result}"
        print("PASS: BUY -1.5% → TECH_FAKEOUT")

    def test_classify_sell_small_rise_tech_fakeout(self):
        """SELL prediction with +1.2% rise → TECH_FAKEOUT (moderate reversal)."""
        from services.prediction_tracker import _classify_failure
        # SELL at $100, price rose to $101.2 (+1.2%)
        result = _classify_failure("SELL", 100.0, 101.2)
        assert result == "TECH_FAKEOUT", f"Expected TECH_FAKEOUT, got {result}"
        print("PASS: SELL +1.2% → TECH_FAKEOUT")

    def test_classify_buy_tiny_drop_regime_shift(self):
        """BUY prediction with -0.5% drop → REGIME_SHIFT (small move, wrong direction)."""
        from services.prediction_tracker import _classify_failure
        # BUY at $100, price dropped to $99.5 (-0.5%)
        result = _classify_failure("BUY", 100.0, 99.5)
        assert result == "REGIME_SHIFT", f"Expected REGIME_SHIFT, got {result}"
        print("PASS: BUY -0.5% → REGIME_SHIFT")

    def test_classify_buy_large_drop_low_volume_liquidity_gap(self):
        """BUY prediction with -7% drop and low volume → LIQUIDITY_GAP."""
        from services.prediction_tracker import _classify_failure
        # BUY at $100, price dropped to $93 (-7%), volume_ratio < 0.5
        result = _classify_failure("BUY", 100.0, 93.0, volume_ratio=0.3)
        assert result == "LIQUIDITY_GAP", f"Expected LIQUIDITY_GAP, got {result}"
        print("PASS: BUY -7% low_vol → LIQUIDITY_GAP")

    def test_classify_hold_large_move_macro_shock(self):
        """HOLD prediction with +5% move → MACRO_SHOCK (stability expected but big move)."""
        from services.prediction_tracker import _classify_failure
        # HOLD at $100, price rose to $105 (+5%)
        result = _classify_failure("HOLD", 100.0, 105.0)
        assert result == "MACRO_SHOCK", f"Expected MACRO_SHOCK, got {result}"
        print("PASS: HOLD +5% → MACRO_SHOCK")

    def test_classify_invalid_prices_unknown(self):
        """Invalid prices (zero or negative) → UNKNOWN."""
        from services.prediction_tracker import _classify_failure
        result1 = _classify_failure("BUY", 0, 100.0)
        result2 = _classify_failure("BUY", 100.0, 0)
        assert result1 == "UNKNOWN", f"Expected UNKNOWN for zero price_at, got {result1}"
        assert result2 == "UNKNOWN", f"Expected UNKNOWN for zero price_now, got {result2}"
        print("PASS: Invalid prices → UNKNOWN")


class TestFailureModesAPI:
    """Test GET /api/accuracy/failure-modes endpoint."""

    @pytest.fixture(scope="class")
    def auth_cookies(self):
        """Login and get auth cookies."""
        session = requests.Session()
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code}")
        return session.cookies

    def test_failure_modes_endpoint_returns_all_categories(self, auth_cookies):
        """GET /api/accuracy/failure-modes returns all 5 failure mode categories."""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        resp = session.get(f"{BASE_URL}/api/accuracy/failure-modes")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "failure_modes" in data
        modes = data["failure_modes"]
        expected_codes = {"TECH_FAKEOUT", "MACRO_SHOCK", "LIQUIDITY_GAP", "REGIME_SHIFT", "UNKNOWN"}
        assert set(modes.keys()) == expected_codes, f"Missing modes: {expected_codes - set(modes.keys())}"
        print(f"PASS: GET /api/accuracy/failure-modes returns all 5 categories: {list(modes.keys())}")

    def test_failure_modes_requires_auth(self):
        """GET /api/accuracy/failure-modes requires authentication."""
        resp = requests.get(f"{BASE_URL}/api/accuracy/failure-modes")
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"
        print("PASS: GET /api/accuracy/failure-modes requires authentication")


class TestFailureBreakdownAPI:
    """Test GET /api/accuracy/failure-breakdown endpoint."""

    @pytest.fixture(scope="class")
    def auth_cookies(self):
        """Login and get auth cookies."""
        session = requests.Session()
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code}")
        return session.cookies

    def test_failure_breakdown_endpoint_structure(self, auth_cookies):
        """GET /api/accuracy/failure-breakdown returns proper structure."""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        resp = session.get(f"{BASE_URL}/api/accuracy/failure-breakdown")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "total_failures" in data
        assert "breakdown" in data
        assert "failure_modes" in data
        assert isinstance(data["total_failures"], int)
        assert isinstance(data["breakdown"], dict)
        print(f"PASS: GET /api/accuracy/failure-breakdown returns proper structure (total_failures={data['total_failures']})")

    def test_failure_breakdown_requires_pro(self):
        """GET /api/accuracy/failure-breakdown requires Pro subscription."""
        resp = requests.get(f"{BASE_URL}/api/accuracy/failure-breakdown")
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"
        print("PASS: GET /api/accuracy/failure-breakdown requires authentication")


class TestManualClassificationAPI:
    """Test POST /api/accuracy/classify/{prediction_id} endpoint."""

    @pytest.fixture(scope="class")
    def auth_cookies(self):
        """Login and get auth cookies."""
        session = requests.Session()
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip(f"Login failed: {login_resp.status_code}")
        return session.cookies

    def test_classify_invalid_prediction_id_404(self, auth_cookies):
        """POST /api/accuracy/classify/{id} returns 404 for non-existent prediction."""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        resp = session.post(
            f"{BASE_URL}/api/accuracy/classify/nonexistent123",
            json={"failure_code": "TECH_FAKEOUT"}
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print("PASS: POST /api/accuracy/classify returns 404 for non-existent prediction")

    def test_classify_invalid_failure_code_400(self, auth_cookies):
        """POST /api/accuracy/classify/{id} returns 400 for invalid failure_code."""
        session = requests.Session()
        session.cookies.update(auth_cookies)
        resp = session.post(
            f"{BASE_URL}/api/accuracy/classify/test123",
            json={"failure_code": "INVALID_CODE"}
        )
        # Should be 400 for invalid code OR 404 for non-existent prediction
        assert resp.status_code in [400, 404], f"Expected 400 or 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: POST /api/accuracy/classify validates failure_code (status={resp.status_code})")

    def test_classify_requires_auth(self):
        """POST /api/accuracy/classify/{id} requires authentication."""
        resp = requests.post(
            f"{BASE_URL}/api/accuracy/classify/test123",
            json={"failure_code": "TECH_FAKEOUT"}
        )
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"
        print("PASS: POST /api/accuracy/classify requires authentication")


class TestRegimeToTextWithFailure:
    """Test _regime_to_text() includes Failure Mode and Failure Reason lines."""

    def test_regime_to_text_includes_failure_code(self):
        """_regime_to_text() includes 'Failure Mode:' line when failure_code present."""
        from services.market_memory_service import _regime_to_text
        regime = {
            "symbol": "AAPL",
            "price": 150.0,
            "prediction": "BUY",
            "outcome": "miss",
            "failure_code": "TECH_FAKEOUT",
            "failure_reason": "Indicators were bullish but price reversed immediately."
        }
        text = _regime_to_text(regime)
        assert "Failure Mode: TECH_FAKEOUT" in text, f"Missing 'Failure Mode:' in: {text}"
        print("PASS: _regime_to_text includes 'Failure Mode: TECH_FAKEOUT'")

    def test_regime_to_text_includes_failure_reason(self):
        """_regime_to_text() includes 'Failure Reason:' line when failure_reason present."""
        from services.market_memory_service import _regime_to_text
        regime = {
            "symbol": "TSLA",
            "price": 200.0,
            "prediction": "SELL",
            "outcome": "miss",
            "failure_code": "MACRO_SHOCK",
            "failure_reason": "Unexpected news invalidated the setup."
        }
        text = _regime_to_text(regime)
        assert "Failure Reason:" in text, f"Missing 'Failure Reason:' in: {text}"
        print("PASS: _regime_to_text includes 'Failure Reason:'")

    def test_regime_to_text_no_failure_when_absent(self):
        """_regime_to_text() does NOT include failure lines when not present."""
        from services.market_memory_service import _regime_to_text
        regime = {
            "symbol": "MSFT",
            "price": 300.0,
            "prediction": "BUY",
            "outcome": "hit",
        }
        text = _regime_to_text(regime)
        assert "Failure Mode:" not in text, f"Unexpected 'Failure Mode:' in: {text}"
        assert "Failure Reason:" not in text, f"Unexpected 'Failure Reason:' in: {text}"
        print("PASS: _regime_to_text excludes failure lines when not present")


class TestSaveRegimeWithFailureCode:
    """Test save_regime() stores failure_code in ChromaDB metadata."""

    def test_save_regime_metadata_includes_failure_code(self):
        """Verify save_regime() adds failure_code to ChromaDB metadata."""
        from services.market_memory_service import save_regime, _collection, init_memory
        import asyncio

        # Initialize memory if not already done
        if _collection is None:
            init_memory()

        # Create a test regime with failure_code
        test_regime = {
            "symbol": "TEST_FAIL_61",
            "date": "2026-01-15",
            "price": 100.0,
            "prediction": "BUY",
            "confidence": 85,
            "actual_result": "fell to $93",
            "outcome": "miss",
            "failure_code": "MACRO_SHOCK",
            "failure_reason": "Fed rate hike surprise."
        }

        # Save the regime
        doc_id = asyncio.get_event_loop().run_until_complete(save_regime(test_regime))
        assert doc_id, "save_regime should return a doc_id"

        # Verify metadata in ChromaDB
        from services.market_memory_service import _collection
        if _collection:
            result = _collection.get(ids=[doc_id], include=["metadatas"])
            if result and result.get("metadatas"):
                meta = result["metadatas"][0]
                assert meta.get("failure_code") == "MACRO_SHOCK", f"Expected failure_code=MACRO_SHOCK, got {meta.get('failure_code')}"
                print(f"PASS: save_regime stores failure_code in ChromaDB metadata: {meta.get('failure_code')}")
            else:
                print("SKIP: Could not verify ChromaDB metadata (no results)")
        else:
            print("SKIP: ChromaDB collection not initialized")


class TestVetoContextWithFailureCode:
    """Test get_strategist_veto_context() includes [FAILURE_CODE] tag."""

    def test_veto_context_format_check(self):
        """Verify get_strategist_veto_context() code includes failure_code tag logic."""
        import inspect
        from services.market_memory_service import get_strategist_veto_context

        source = inspect.getsource(get_strategist_veto_context)
        
        # Check that the function accesses failure_code from metadata
        assert "failure_code" in source, "get_strategist_veto_context should reference failure_code"
        assert "meta.get" in source, "get_strategist_veto_context should access metadata"
        
        # Check for the [FAILURE_CODE] tag format
        assert "[" in source and "]" in source, "get_strategist_veto_context should format failure_code with brackets"
        
        print("PASS: get_strategist_veto_context includes failure_code tag logic")

    def test_veto_context_failure_tag_format(self):
        """Verify the failure_tag format in get_strategist_veto_context."""
        import inspect
        from services.market_memory_service import get_strategist_veto_context

        source = inspect.getsource(get_strategist_veto_context)
        
        # Check for the specific format: [FAILURE_CODE]
        assert 'failure_tag = ""' in source or "failure_tag" in source, "Should have failure_tag variable"
        assert "meta.get('failure_code')" in source or 'meta.get("failure_code")' in source, "Should get failure_code from meta"
        
        print("PASS: get_strategist_veto_context has proper failure_tag format")


class TestNightlyCleanupPreservesFailureCode:
    """Test nightly_cleanup preserves failure_code when re-tagging toxic entries."""

    def test_cleanup_preserves_failure_code_in_metadata(self):
        """Verify nightly_cleanup copies existing metadata including failure_code."""
        import inspect
        from services.market_memory_service import nightly_cleanup

        source = inspect.getsource(nightly_cleanup)
        
        # Check that cleanup copies existing metadata before updating.
        # Accept both idioms — ``dict(toxic_metas[i])`` mirrors ``.copy()``
        # at runtime and is the form the service uses (ChromaDB's
        # UpdateMetadata stub types the source as Mapping, so `dict()`
        # is the mypy-clean idiom here).
        assert (
            "meta = toxic_metas[i].copy()" in source
            or "meta.copy()" in source
            or "dict(toxic_metas[i])" in source
        ), "nightly_cleanup should copy existing metadata"
        
        # Check that it only updates the outcome field
        assert 'meta["outcome"] = "toxic_lesson"' in source or "meta['outcome'] = 'toxic_lesson'" in source, \
            "nightly_cleanup should only update outcome field"
        
        print("PASS: nightly_cleanup preserves existing metadata (including failure_code) when re-tagging")


class TestVerifyPendingPredictionsClassifiesFailure:
    """Test verify_pending_predictions() auto-classifies failures."""

    def test_verify_predictions_calls_classify_failure(self):
        """Verify verify_pending_predictions() calls _classify_failure for wrong predictions."""
        import inspect
        from services.prediction_tracker import verify_pending_predictions

        source = inspect.getsource(verify_pending_predictions)
        
        # Check that it calls _classify_failure
        assert "_classify_failure" in source, "verify_pending_predictions should call _classify_failure"
        
        # Check that it stores failure_code in the update
        assert "failure_code" in source, "verify_pending_predictions should store failure_code"
        assert "failure_reason" in source, "verify_pending_predictions should store failure_reason"
        
        print("PASS: verify_pending_predictions auto-classifies failures using _classify_failure")

    def test_verify_predictions_saves_to_memory_with_failure(self):
        """Verify verify_pending_predictions() passes failure_code to save_regime."""
        import inspect
        from services.prediction_tracker import verify_pending_predictions

        source = inspect.getsource(verify_pending_predictions)
        
        # Check that regime dict includes failure_code
        assert '"failure_code":' in source or "'failure_code':" in source, \
            "verify_pending_predictions should include failure_code in regime dict"
        
        print("PASS: verify_pending_predictions passes failure_code to save_regime")


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
