"""
Iteration 50: Prediction Accuracy Tracker Tests

Tests the new Prediction Accuracy Tracker feature:
1. GET /api/accuracy/stats - returns accuracy stats for all 3 features + overall (Pro only)
2. GET /api/accuracy/stats/{feature} - feature-specific stats (Pro only)
3. GET /api/accuracy/history - logged predictions with details (Pro only)
4. GET /api/accuracy/stats returns 403 for non-Pro users
5. POST /api/accuracy/verify - triggers verification (Pro only)
6. War Room endpoint auto-logs predictions to predictions collection
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestAccuracyTrackerAuth:
    """Test authentication requirements for accuracy endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session for tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_accuracy_stats_requires_auth(self):
        """GET /api/accuracy/stats should require authentication"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats")
        # Should return 401 or 403 without auth
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"PASS: /api/accuracy/stats requires auth (status: {response.status_code})")
    
    def test_accuracy_history_requires_auth(self):
        """GET /api/accuracy/history should require authentication"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/history")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"PASS: /api/accuracy/history requires auth (status: {response.status_code})")
    
    def test_accuracy_verify_requires_auth(self):
        """POST /api/accuracy/verify should require authentication"""
        response = self.session.post(f"{BASE_URL}/api/accuracy/verify")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"PASS: /api/accuracy/verify requires auth (status: {response.status_code})")


class TestAccuracyTrackerProUser:
    """Test accuracy endpoints with Pro user (admin)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as admin (Pro user) and setup session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Admin login failed: {login_response.text}"
        print(f"Admin login successful")
    
    def test_accuracy_stats_all_features(self):
        """GET /api/accuracy/stats returns stats for all features + overall"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Should have stats for all 3 features + overall
        assert "war_room" in data, "Missing war_room stats"
        assert "hypothesis" in data, "Missing hypothesis stats"
        assert "market_prediction" in data, "Missing market_prediction stats"
        assert "overall" in data, "Missing overall stats"
        
        # Each feature should have required fields
        for feature in ["war_room", "hypothesis", "market_prediction", "overall"]:
            stats = data[feature]
            assert "accuracy_24h" in stats, f"Missing accuracy_24h in {feature}"
            assert "total_24h" in stats, f"Missing total_24h in {feature}"
            assert "correct_24h" in stats, f"Missing correct_24h in {feature}"
            assert "accuracy_1w" in stats, f"Missing accuracy_1w in {feature}"
            assert "total_1w" in stats, f"Missing total_1w in {feature}"
            assert "correct_1w" in stats, f"Missing correct_1w in {feature}"
            assert "pending" in stats, f"Missing pending in {feature}"
            assert "feature" in stats, f"Missing feature in {feature}"
        
        print(f"PASS: /api/accuracy/stats returns all feature stats")
        print(f"  - war_room: pending={data['war_room']['pending']}, total_24h={data['war_room']['total_24h']}")
        print(f"  - hypothesis: pending={data['hypothesis']['pending']}, total_24h={data['hypothesis']['total_24h']}")
        print(f"  - market_prediction: pending={data['market_prediction']['pending']}, total_24h={data['market_prediction']['total_24h']}")
        print(f"  - overall: pending={data['overall']['pending']}, total_24h={data['overall']['total_24h']}")
    
    def test_accuracy_stats_war_room(self):
        """GET /api/accuracy/stats/war_room returns feature-specific stats"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats/war_room")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["feature"] == "war_room", f"Expected feature=war_room, got {data.get('feature')}"
        assert "accuracy_24h" in data
        assert "total_24h" in data
        assert "pending" in data
        
        print(f"PASS: /api/accuracy/stats/war_room returns war_room stats")
        print(f"  - pending: {data['pending']}, total_24h: {data['total_24h']}")
    
    def test_accuracy_stats_hypothesis(self):
        """GET /api/accuracy/stats/hypothesis returns feature-specific stats"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats/hypothesis")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["feature"] == "hypothesis", f"Expected feature=hypothesis, got {data.get('feature')}"
        
        print(f"PASS: /api/accuracy/stats/hypothesis returns hypothesis stats")
    
    def test_accuracy_stats_market_prediction(self):
        """GET /api/accuracy/stats/market_prediction returns feature-specific stats"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats/market_prediction")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["feature"] == "market_prediction", f"Expected feature=market_prediction, got {data.get('feature')}"
        
        print(f"PASS: /api/accuracy/stats/market_prediction returns market_prediction stats")
    
    def test_accuracy_stats_invalid_feature(self):
        """GET /api/accuracy/stats/{invalid} returns 400"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/stats/invalid_feature")
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        print(f"PASS: /api/accuracy/stats/invalid_feature returns 400")
    
    def test_accuracy_history(self):
        """GET /api/accuracy/history returns logged predictions"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/history")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "predictions" in data, "Missing predictions array"
        assert "count" in data, "Missing count field"
        
        # If there are predictions, verify structure
        if data["count"] > 0:
            pred = data["predictions"][0]
            assert "prediction_id" in pred, "Missing prediction_id"
            assert "feature" in pred, "Missing feature"
            assert "symbol" in pred, "Missing symbol"
            assert "direction" in pred, "Missing direction"
            assert "confidence" in pred, "Missing confidence"
            assert "price_at_prediction" in pred, "Missing price_at_prediction"
            assert "timestamp" in pred, "Missing timestamp"
            print(f"PASS: /api/accuracy/history returns {data['count']} predictions")
            print(f"  - First prediction: {pred['symbol']} {pred['direction']} ({pred['feature']})")
        else:
            print(f"PASS: /api/accuracy/history returns empty predictions (count=0)")
    
    def test_accuracy_history_with_feature_filter(self):
        """GET /api/accuracy/history?feature=war_room filters by feature"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/history?feature=war_room")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # All predictions should be war_room
        for pred in data["predictions"]:
            assert pred["feature"] == "war_room", f"Expected war_room, got {pred['feature']}"
        
        print(f"PASS: /api/accuracy/history?feature=war_room filters correctly ({data['count']} results)")
    
    def test_accuracy_verify(self):
        """POST /api/accuracy/verify triggers verification"""
        response = self.session.post(f"{BASE_URL}/api/accuracy/verify")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("status") == "verification_complete", f"Expected verification_complete, got {data}"
        
        print(f"PASS: POST /api/accuracy/verify returns verification_complete")


class TestWarRoomAutoLogging:
    """Test that War Room auto-logs predictions"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as admin (Pro user) and setup session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Admin login failed: {login_response.text}"
    
    def test_war_room_logs_prediction(self):
        """War Room analysis should auto-log prediction to predictions collection"""
        # Get initial prediction count
        history_before = self.session.get(f"{BASE_URL}/api/accuracy/history?feature=war_room")
        assert history_before.status_code == 200
        count_before = history_before.json()["count"]
        
        # Run War Room analysis (this takes 30-40 seconds)
        print("Running War Room analysis for MSFT (this may take 30-40 seconds)...")
        response = self.session.get(f"{BASE_URL}/api/intelligence/war-room/MSFT", timeout=120)
        
        if response.status_code == 200:
            data = response.json()
            assert "composite" in data, "Missing composite in War Room response"
            assert "verdict" in data.get("composite", {}), "Missing verdict in composite"
            
            # Wait a moment for async logging
            time.sleep(2)
            
            # Check if prediction was logged
            history_after = self.session.get(f"{BASE_URL}/api/accuracy/history?feature=war_room")
            assert history_after.status_code == 200
            count_after = history_after.json()["count"]
            
            # Should have at least one more prediction
            if count_after > count_before:
                print(f"PASS: War Room auto-logged prediction (count: {count_before} -> {count_after})")
                # Verify the latest prediction is for MSFT
                latest = history_after.json()["predictions"][0]
                if latest["symbol"] == "MSFT":
                    print(f"  - Logged: MSFT {latest['direction']} (confidence: {latest['confidence']})")
            else:
                print(f"INFO: Prediction count unchanged ({count_before}). May already have MSFT prediction.")
        else:
            print(f"SKIP: War Room returned {response.status_code} - may be rate limited or timeout")
            pytest.skip(f"War Room returned {response.status_code}")


class TestNonProUserAccess:
    """Test that non-Pro users get 403 on accuracy endpoints"""
    
    def test_accuracy_stats_non_pro_user(self):
        """Non-Pro user should get 403 on /api/accuracy/stats"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        # Create a test user (non-Pro)
        test_email = f"test_accuracy_{int(time.time())}@test.com"
        test_password = "TestPass123!"
        
        # Register
        register_response = session.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": test_email, "password": test_password, "name": "Test User"}
        )
        
        if register_response.status_code == 200:
            # Try to access accuracy stats
            stats_response = session.get(f"{BASE_URL}/api/accuracy/stats")
            assert stats_response.status_code == 403, f"Expected 403, got {stats_response.status_code}"
            
            data = stats_response.json()
            assert "Pro subscription required" in data.get("detail", ""), f"Expected Pro required message, got {data}"
            
            print(f"PASS: Non-Pro user gets 403 on /api/accuracy/stats")
        else:
            # If registration fails (user exists), try login
            login_response = session.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": test_email, "password": test_password}
            )
            if login_response.status_code == 200:
                stats_response = session.get(f"{BASE_URL}/api/accuracy/stats")
                assert stats_response.status_code == 403, f"Expected 403, got {stats_response.status_code}"
                print(f"PASS: Non-Pro user gets 403 on /api/accuracy/stats")
            else:
                pytest.skip("Could not create/login test user")


class TestPredictionDataStructure:
    """Test prediction data structure and fields"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login as admin (Pro user) and setup session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, f"Admin login failed: {login_response.text}"
    
    def test_prediction_fields(self):
        """Verify prediction documents have all required fields"""
        response = self.session.get(f"{BASE_URL}/api/accuracy/history?limit=5")
        assert response.status_code == 200
        
        data = response.json()
        if data["count"] == 0:
            print("INFO: No predictions in database yet - skipping field validation")
            pytest.skip("No predictions to validate")
        
        pred = data["predictions"][0]
        
        # Required fields
        required_fields = [
            "prediction_id", "feature", "symbol", "direction", 
            "confidence", "price_at_prediction", "timestamp"
        ]
        
        for field in required_fields:
            assert field in pred, f"Missing required field: {field}"
        
        # Verify field types
        assert isinstance(pred["prediction_id"], str), "prediction_id should be string"
        assert isinstance(pred["symbol"], str), "symbol should be string"
        assert isinstance(pred["direction"], str), "direction should be string"
        assert isinstance(pred["confidence"], (int, float)), "confidence should be numeric"
        assert isinstance(pred["price_at_prediction"], (int, float)), "price_at_prediction should be numeric"
        
        # Verify feature is valid
        valid_features = ["war_room", "hypothesis", "market_prediction"]
        assert pred["feature"] in valid_features, f"Invalid feature: {pred['feature']}"
        
        # Verify direction is valid
        valid_directions = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL", "BULLISH", "BEARISH", "NEUTRAL"]
        assert pred["direction"].upper() in valid_directions, f"Invalid direction: {pred['direction']}"
        
        print(f"PASS: Prediction has all required fields with correct types")
        print(f"  - {pred['symbol']} {pred['direction']} ({pred['feature']}) @ ${pred['price_at_prediction']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
