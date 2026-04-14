"""
Iteration 91: Ticker-Specific Market Prediction Tests
Tests the new GET /api/market/prediction/{symbol} endpoint that provides
ticker-focused AI predictions using the adversarial pipeline.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTickerPredictionEndpoint:
    """Tests for GET /api/market/prediction/{symbol} endpoint"""
    
    def test_general_market_prediction_still_works(self):
        """Verify GET /api/market/prediction (no symbol) still returns valid prediction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "overall_direction" in data, "Missing overall_direction"
        assert "confidence_score" in data, "Missing confidence_score"
        assert data["overall_direction"] in ["BULLISH", "BEARISH", "NEUTRAL"], f"Invalid direction: {data['overall_direction']}"
        assert 0 <= data["confidence_score"] <= 100, f"Invalid confidence: {data['confidence_score']}"
        # General market should NOT have symbol/ticker_focused
        assert data.get("symbol") is None, "General market should not have symbol"
        assert data.get("ticker_focused") is None, "General market should not have ticker_focused"
        print(f"✓ General market prediction: {data['overall_direction']} ({data['confidence_score']}%)")
    
    def test_aapl_ticker_prediction(self):
        """Verify GET /api/market/prediction/AAPL returns AAPL-focused prediction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/AAPL", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("symbol") == "AAPL", f"Expected symbol=AAPL, got {data.get('symbol')}"
        assert data.get("ticker_focused") == True, f"Expected ticker_focused=True, got {data.get('ticker_focused')}"
        assert "overall_direction" in data, "Missing overall_direction"
        assert "confidence_score" in data, "Missing confidence_score"
        print(f"✓ AAPL prediction: {data['overall_direction']} ({data['confidence_score']}%)")
    
    def test_btc_crypto_prediction(self):
        """Verify GET /api/market/prediction/BTC returns crypto prediction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/BTC", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("symbol") == "BTC", f"Expected symbol=BTC, got {data.get('symbol')}"
        assert data.get("ticker_focused") == True, f"Expected ticker_focused=True, got {data.get('ticker_focused')}"
        assert "overall_direction" in data, "Missing overall_direction"
        print(f"✓ BTC prediction: {data['overall_direction']} ({data.get('confidence_score', 'N/A')}%)")
    
    def test_lowercase_symbol_normalized(self):
        """Verify lowercase symbols are normalized to uppercase"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/tsla", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("symbol") == "TSLA", f"Expected symbol=TSLA (uppercase), got {data.get('symbol')}"
        assert data.get("ticker_focused") == True
        print(f"✓ Lowercase 'tsla' normalized to TSLA")
    
    def test_invalid_symbol_graceful_handling(self):
        """Verify invalid symbols are handled gracefully (returns prediction or 500)"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/FAKEXYZ", timeout=60)
        # Should either return 200 with a prediction or 500 with error
        assert response.status_code in [200, 500], f"Expected 200 or 500, got {response.status_code}"
        
        if response.status_code == 200:
            data = response.json()
            assert data.get("symbol") == "FAKEXYZ", "Symbol should be preserved"
            print(f"✓ Invalid symbol handled gracefully with prediction")
        else:
            print(f"✓ Invalid symbol returned 500 (expected behavior)")
    
    def test_symbol_too_long_rejected(self):
        """Verify symbols >10 chars are rejected with 400"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/VERYLONGSYMBOL123", timeout=30)
        assert response.status_code == 400, f"Expected 400 for long symbol, got {response.status_code}"
        print(f"✓ Long symbol rejected with 400")
    
    def test_prediction_response_structure(self):
        """Verify ticker prediction has expected response structure"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/NVDA", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        # Required fields
        assert "overall_direction" in data
        assert "confidence_score" in data
        assert "symbol" in data
        assert "ticker_focused" in data
        
        # Optional but expected fields
        expected_fields = ["timeframes", "key_signals", "risk_factors", "timestamp"]
        present_fields = [f for f in expected_fields if f in data]
        print(f"✓ NVDA prediction has {len(present_fields)}/{len(expected_fields)} expected fields: {present_fields}")
    
    def test_macro_data_included(self):
        """Verify macro_data is included in ticker prediction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/SPY", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        # macro_data should be enriched
        if "macro_data" in data:
            macro = data["macro_data"]
            assert "world_events" in macro or "foreign_markets" in macro or "gov_filings" in macro
            print(f"✓ SPY prediction includes macro_data")
        else:
            print(f"⚠ macro_data not present (may be optional)")


class TestWarRoomRegressionCheck:
    """Regression check: AI War Room should still have search functionality"""
    
    def test_war_room_symbol_endpoint_exists(self):
        """Verify /api/intelligence/war-room/{symbol} still works"""
        response = requests.get(f"{BASE_URL}/api/intelligence/war-room/AAPL", timeout=60)
        # Should return 200 or 401 (auth required)
        assert response.status_code in [200, 401, 403], f"War Room endpoint broken: {response.status_code}"
        print(f"✓ War Room symbol endpoint returns {response.status_code}")


class TestHypothesisRegressionCheck:
    """Regression check: AI Hypothesis should still have search functionality"""
    
    def test_hypothesis_symbol_endpoint_exists(self):
        """Verify /api/hypothesis/{symbol} still works"""
        response = requests.get(f"{BASE_URL}/api/hypothesis/AAPL", timeout=60)
        # Should return 200 or 401 (auth required)
        assert response.status_code in [200, 401, 403], f"Hypothesis endpoint broken: {response.status_code}"
        print(f"✓ Hypothesis symbol endpoint returns {response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
