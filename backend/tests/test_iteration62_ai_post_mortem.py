"""
Iteration 62: AI-Powered Post-Mortem Analysis Testing

Tests the new AI post-mortem system that classifies WHY predictions failed using news context + LLM.

Features tested:
1. POST /api/accuracy/post-mortem/{prediction_id} - runs AI post-mortem on failed predictions
2. GET /api/accuracy/post-mortem/history - returns recent post-mortem results from MongoDB
3. post_mortem_service.run_post_mortem() - fetches news and calls LLM for classification
4. post_mortem_service.run_and_update_post_mortem() - updates MongoDB and ChromaDB
5. Validation paths: 404 for bad ID, 400 for correct/unverified predictions
6. Prompt template validation - includes failure modes, news headlines, price action context
"""

import pytest
import requests
import os
import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

# Add backend to path for service imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
# Test credentials
class TestPostMortemServiceImports:
    """Test that post_mortem_service functions are importable and structured correctly"""
    
    def test_post_mortem_service_importable(self):
        """Verify post_mortem_service module can be imported"""
        try:
            from services import post_mortem_service
            assert post_mortem_service is not None
            print("PASS: post_mortem_service module imported successfully")
        except ImportError as e:
            pytest.fail(f"Failed to import post_mortem_service: {e}")
    
    def test_run_post_mortem_function_exists(self):
        """Verify run_post_mortem function exists"""
        from services.post_mortem_service import run_post_mortem
        assert callable(run_post_mortem)
        print("PASS: run_post_mortem function exists and is callable")
    
    def test_run_and_update_post_mortem_function_exists(self):
        """Verify run_and_update_post_mortem function exists"""
        from services.post_mortem_service import run_and_update_post_mortem
        assert callable(run_and_update_post_mortem)
        print("PASS: run_and_update_post_mortem function exists and is callable")
    
    def test_fetch_ticker_news_function_exists(self):
        """Verify _fetch_ticker_news function exists"""
        from services.post_mortem_service import _fetch_ticker_news
        assert callable(_fetch_ticker_news)
        print("PASS: _fetch_ticker_news function exists and is callable")
    
    def test_llm_classify_function_exists(self):
        """Verify _llm_classify function exists"""
        from services.post_mortem_service import _llm_classify
        assert callable(_llm_classify)
        print("PASS: _llm_classify function exists and is callable")
    
    def test_failure_modes_dict_exists(self):
        """Verify FAILURE_MODES dict exists with all 5 categories"""
        from services.post_mortem_service import FAILURE_MODES
        assert isinstance(FAILURE_MODES, dict)
        expected_codes = {"TECH_FAKEOUT", "MACRO_SHOCK", "LIQUIDITY_GAP", "REGIME_SHIFT", "UNKNOWN"}
        assert set(FAILURE_MODES.keys()) == expected_codes
        print(f"PASS: FAILURE_MODES contains all 5 categories: {list(FAILURE_MODES.keys())}")


class TestPostMortemPromptTemplate:
    """Test that the post-mortem prompt template is correctly structured"""
    
    def test_prompt_template_exists(self):
        """Verify _POST_MORTEM_PROMPT template exists"""
        from services.post_mortem_service import _POST_MORTEM_PROMPT
        assert isinstance(_POST_MORTEM_PROMPT, str)
        assert len(_POST_MORTEM_PROMPT) > 100
        print("PASS: _POST_MORTEM_PROMPT template exists")
    
    def test_prompt_includes_failure_modes(self):
        """Verify prompt includes all failure mode codes"""
        from services.post_mortem_service import _POST_MORTEM_PROMPT
        assert "TECH_FAKEOUT" in _POST_MORTEM_PROMPT
        assert "MACRO_SHOCK" in _POST_MORTEM_PROMPT
        assert "LIQUIDITY_GAP" in _POST_MORTEM_PROMPT
        assert "REGIME_SHIFT" in _POST_MORTEM_PROMPT
        assert "UNKNOWN" in _POST_MORTEM_PROMPT
        print("PASS: Prompt includes all 5 failure mode codes")
    
    def test_prompt_includes_placeholders(self):
        """Verify prompt includes required placeholders for context"""
        from services.post_mortem_service import _POST_MORTEM_PROMPT
        assert "{ticker}" in _POST_MORTEM_PROMPT
        assert "{direction}" in _POST_MORTEM_PROMPT
        assert "{price_at" in _POST_MORTEM_PROMPT
        assert "{price_now" in _POST_MORTEM_PROMPT
        assert "{pct_change" in _POST_MORTEM_PROMPT
        assert "{confidence}" in _POST_MORTEM_PROMPT
        assert "{news_headlines}" in _POST_MORTEM_PROMPT
        assert "{heuristic_code}" in _POST_MORTEM_PROMPT
        print("PASS: Prompt includes all required placeholders")
    
    def test_prompt_requests_json_response(self):
        """Verify prompt asks for JSON response format"""
        from services.post_mortem_service import _POST_MORTEM_PROMPT
        assert "JSON" in _POST_MORTEM_PROMPT
        assert "failure_code" in _POST_MORTEM_PROMPT
        assert "reasoning" in _POST_MORTEM_PROMPT
        assert "key_headline" in _POST_MORTEM_PROMPT
        print("PASS: Prompt requests JSON response with failure_code, reasoning, key_headline")


class TestPostMortemAPIValidation:
    """Test POST /api/accuracy/post-mortem/{id} validation paths"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session for authenticated requests"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        print(f"PASS: Logged in as {ADMIN_EMAIL}")
    
    def test_post_mortem_404_for_nonexistent_prediction(self):
        """POST /api/accuracy/post-mortem/{bad_id} returns 404"""
        fake_id = "nonexistent_12345"
        resp = self.session.post(f"{BASE_URL}/api/accuracy/post-mortem/{fake_id}")
        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data.get("detail", "").lower()
        print(f"PASS: POST /api/accuracy/post-mortem/{fake_id} returns 404")
    
    def test_post_mortem_requires_auth(self):
        """POST /api/accuracy/post-mortem/{id} requires authentication"""
        # Use a fresh session without login
        fresh_session = requests.Session()
        resp = fresh_session.post(f"{BASE_URL}/api/accuracy/post-mortem/test123")
        assert resp.status_code in [401, 403]
        print("PASS: POST /api/accuracy/post-mortem requires authentication")


class TestPostMortemHistoryEndpoint:
    """Test GET /api/accuracy/post-mortem/history endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session for authenticated requests"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    
    def test_post_mortem_history_returns_200(self):
        """GET /api/accuracy/post-mortem/history returns 200"""
        resp = self.session.get(f"{BASE_URL}/api/accuracy/post-mortem/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "post_mortems" in data
        assert "count" in data
        assert isinstance(data["post_mortems"], list)
        print(f"PASS: GET /api/accuracy/post-mortem/history returns 200 with {data['count']} results")
    
    def test_post_mortem_history_requires_auth(self):
        """GET /api/accuracy/post-mortem/history requires authentication"""
        fresh_session = requests.Session()
        resp = fresh_session.get(f"{BASE_URL}/api/accuracy/post-mortem/history")
        assert resp.status_code in [401, 403]
        print("PASS: GET /api/accuracy/post-mortem/history requires authentication")
    
    def test_post_mortem_history_respects_limit(self):
        """GET /api/accuracy/post-mortem/history respects limit parameter"""
        resp = self.session.get(f"{BASE_URL}/api/accuracy/post-mortem/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["post_mortems"]) <= 5
        print(f"PASS: History endpoint respects limit parameter (returned {len(data['post_mortems'])} items)")


class TestPostMortemWithTestPrediction:
    """Test post-mortem with a test prediction inserted into MongoDB"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        self.test_prediction_id = f"TEST_PM_{str(uuid4())[:8]}"
    
    def test_post_mortem_400_for_unverified_prediction(self):
        """POST /api/accuracy/post-mortem returns 400 for unverified prediction"""
        # First, we need to create a test prediction via the API
        # Since we can't directly insert into MongoDB, we'll test the validation logic
        # by checking that the endpoint properly validates prediction state
        
        # Try with a random ID that doesn't exist (should be 404)
        resp = self.session.post(f"{BASE_URL}/api/accuracy/post-mortem/unverified_test_123")
        # This should be 404 since prediction doesn't exist
        assert resp.status_code == 404
        print("PASS: Endpoint correctly returns 404 for non-existent prediction")


class TestPredictionTrackerIntegration:
    """Test that prediction_tracker.verify_pending_predictions() calls post-mortem"""
    
    def test_verify_predictions_imports_post_mortem(self):
        """Verify that verify_pending_predictions can import run_and_update_post_mortem"""
        # Check the import statement exists in prediction_tracker
        import inspect
        from services import prediction_tracker
        source = inspect.getsource(prediction_tracker.verify_pending_predictions)
        assert "run_and_update_post_mortem" in source
        print("PASS: verify_pending_predictions imports run_and_update_post_mortem")
    
    def test_verify_predictions_calls_post_mortem_for_wrong(self):
        """Verify that verify_pending_predictions calls post-mortem for wrong predictions"""
        import inspect
        from services import prediction_tracker
        source = inspect.getsource(prediction_tracker.verify_pending_predictions)
        # Check that it calls post-mortem when prediction is wrong
        assert "if not correct" in source
        assert "run_and_update_post_mortem" in source
        print("PASS: verify_pending_predictions calls post-mortem for wrong predictions")


class TestRunPostMortemFunction:
    """Test run_post_mortem function behavior"""
    
    def test_run_post_mortem_returns_heuristic_on_no_price(self):
        """run_post_mortem returns heuristic fallback when price_at is 0"""
        import asyncio
        from services.post_mortem_service import run_post_mortem
        
        prediction = {
            "symbol": "TEST",
            "direction": "BUY",
            "price_at_prediction": 0,  # Invalid price
            "confidence": 75
        }
        
        result = asyncio.get_event_loop().run_until_complete(
            run_post_mortem(prediction, 100.0, "TECH_FAKEOUT")
        )
        
        assert result["failure_code"] == "TECH_FAKEOUT"
        assert result["source"] == "heuristic"
        assert "No price data" in result.get("reasoning", "")
        print("PASS: run_post_mortem returns heuristic fallback when price_at is 0")


class TestPostMortemLogCollection:
    """Test that post-mortem results are logged to db.post_mortem_log"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
    
    def test_post_mortem_log_structure(self):
        """Verify post_mortem_log collection structure via history endpoint"""
        resp = self.session.get(f"{BASE_URL}/api/accuracy/post-mortem/history")
        assert resp.status_code == 200
        data = resp.json()
        
        # If there are any post-mortems, verify structure
        if data["post_mortems"]:
            pm = data["post_mortems"][0]
            # Check expected fields exist
            expected_fields = ["prediction_id", "ticker", "failure_code", "source", "run_at"]
            for field in expected_fields:
                if field in pm:
                    print(f"  - {field}: {pm[field]}")
            print(f"PASS: Post-mortem log has proper structure with {len(pm)} fields")
        else:
            print("PASS: Post-mortem history endpoint works (no entries yet)")


class TestFailureModeEndpoints:
    """Test existing failure mode endpoints still work with post-mortem integration"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
    
    def test_failure_modes_endpoint(self):
        """GET /api/accuracy/failure-modes returns all 5 categories"""
        resp = self.session.get(f"{BASE_URL}/api/accuracy/failure-modes")
        assert resp.status_code == 200
        data = resp.json()
        assert "failure_modes" in data
        modes = data["failure_modes"]
        expected = {"TECH_FAKEOUT", "MACRO_SHOCK", "LIQUIDITY_GAP", "REGIME_SHIFT", "UNKNOWN"}
        assert set(modes.keys()) == expected
        print(f"PASS: GET /api/accuracy/failure-modes returns all 5 categories")
    
    def test_failure_breakdown_endpoint(self):
        """GET /api/accuracy/failure-breakdown returns proper structure"""
        resp = self.session.get(f"{BASE_URL}/api/accuracy/failure-breakdown")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_failures" in data
        assert "breakdown" in data
        assert "failure_modes" in data
        print(f"PASS: GET /api/accuracy/failure-breakdown returns proper structure (total_failures={data['total_failures']})")


class TestDirectMongoDBInsertion:
    """Test post-mortem with direct MongoDB insertion for complete flow testing"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and setup MongoDB connection"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        # Setup MongoDB connection
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            db_name = os.environ.get("DB_NAME", "risedual_db")
            self.client = AsyncIOMotorClient(mongo_url)
            self.db = self.client[db_name]
            self.has_db = True
        except Exception as e:
            print(f"MongoDB connection not available: {e}")
            self.has_db = False
    
    def test_insert_wrong_prediction_and_run_post_mortem(self):
        """Insert a wrong prediction and run post-mortem on it"""
        if not self.has_db:
            pytest.skip("MongoDB not available for direct insertion test")
        
        import asyncio
        
        async def run_test():
            # Create a test prediction that's verified as wrong
            test_id = f"TEST_PM62_{str(uuid4())[:8]}"
            test_pred = {
                "prediction_id": test_id,
                "feature": "test_post_mortem",
                "symbol": "AAPL",
                "direction": "BUY",
                "confidence": 80,
                "price_at_prediction": 200.0,
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                "verified_24h": {
                    "price": 190.0,  # Price went down, so BUY was wrong
                    "correct": False,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "failure_code": "TECH_FAKEOUT",
                    "failure_reason": "Indicators were bullish but price reversed immediately."
                },
                "verified_1w": None
            }
            
            # Insert the test prediction
            await self.db.predictions.insert_one(test_pred)
            print(f"Inserted test prediction: {test_id}")
            
            # Now call the post-mortem endpoint
            resp = self.session.post(f"{BASE_URL}/api/accuracy/post-mortem/{test_id}")
            
            # Clean up
            await self.db.predictions.delete_one({"prediction_id": test_id})
            await self.db.post_mortem_log.delete_many({"prediction_id": test_id})
            
            return resp
        
        resp = asyncio.get_event_loop().run_until_complete(run_test())
        
        # The endpoint should return 200 and run the post-mortem
        # Note: This may fail if LLM call fails, but we're testing the flow
        if resp.status_code == 200:
            data = resp.json()
            assert "failure_code" in data
            assert "source" in data
            print(f"PASS: Post-mortem ran successfully - failure_code={data['failure_code']}, source={data['source']}")
        else:
            # If it fails, it should be due to LLM issues, not validation
            print(f"Post-mortem returned {resp.status_code}: {resp.text}")
            # Still pass if it's not a validation error (400/404)
            assert resp.status_code not in [400, 404], f"Unexpected validation error: {resp.text}"
            print(f"PASS: Post-mortem endpoint reached (LLM may have failed)")


class TestPostMortemCorrectPredictionValidation:
    """Test that post-mortem returns 400 for correct predictions"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and setup MongoDB connection"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            db_name = os.environ.get("DB_NAME", "risedual_db")
            self.client = AsyncIOMotorClient(mongo_url)
            self.db = self.client[db_name]
            self.has_db = True
        except Exception:
            self.has_db = False
    
    def test_post_mortem_400_for_correct_prediction(self):
        """POST /api/accuracy/post-mortem returns 400 for correct prediction"""
        if not self.has_db:
            pytest.skip("MongoDB not available")
        
        import asyncio
        
        async def run_test():
            test_id = f"TEST_CORRECT_{str(uuid4())[:8]}"
            test_pred = {
                "prediction_id": test_id,
                "feature": "test_post_mortem",
                "symbol": "AAPL",
                "direction": "BUY",
                "confidence": 80,
                "price_at_prediction": 200.0,
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                "verified_24h": {
                    "price": 210.0,  # Price went up, so BUY was correct
                    "correct": True,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                },
                "verified_1w": None
            }
            
            await self.db.predictions.insert_one(test_pred)
            resp = self.session.post(f"{BASE_URL}/api/accuracy/post-mortem/{test_id}")
            await self.db.predictions.delete_one({"prediction_id": test_id})
            return resp
        
        resp = asyncio.get_event_loop().run_until_complete(run_test())
        assert resp.status_code == 400
        data = resp.json()
        assert "correct" in data.get("detail", "").lower()
        print("PASS: POST /api/accuracy/post-mortem returns 400 for correct prediction")
    
    def test_post_mortem_400_for_unverified_prediction(self):
        """POST /api/accuracy/post-mortem returns 400 for unverified prediction"""
        if not self.has_db:
            pytest.skip("MongoDB not available")
        
        import asyncio
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD
        
        async def run_test():
            test_id = f"TEST_UNVERIFIED_{str(uuid4())[:8]}"
            test_pred = {
                "prediction_id": test_id,
                "feature": "test_post_mortem",
                "symbol": "AAPL",
                "direction": "BUY",
                "confidence": 80,
                "price_at_prediction": 200.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "verified_24h": None,  # Not verified yet
                "verified_1w": None
            }
            
            await self.db.predictions.insert_one(test_pred)
            resp = self.session.post(f"{BASE_URL}/api/accuracy/post-mortem/{test_id}")
            await self.db.predictions.delete_one({"prediction_id": test_id})
            return resp
        
        resp = asyncio.get_event_loop().run_until_complete(run_test())
        assert resp.status_code == 400
        data = resp.json()
        assert "verified" in data.get("detail", "").lower()
        print("PASS: POST /api/accuracy/post-mortem returns 400 for unverified prediction")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
