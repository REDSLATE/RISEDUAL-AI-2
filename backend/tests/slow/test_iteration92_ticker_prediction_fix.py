"""
Iteration 92: Ticker-Specific Market Prediction Bug Fix Tests

Tests verify that:
1. GET /api/market/prediction/{symbol} returns symbol field matching the requested ticker
2. GET /api/market/prediction (general market) returns symbol='MARKET'
3. ticker_focused flag is set correctly
4. Summary content is ticker-specific (not generic SPY/market analysis)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestTickerPredictionFix:
    """Tests for the ticker-specific prediction bug fix"""

    def test_nvda_prediction_returns_nvda_symbol(self):
        """Verify GET /api/market/prediction/NVDA returns symbol='NVDA'"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/NVDA", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        # Verify symbol field
        assert data.get('symbol') == 'NVDA', f"Expected symbol='NVDA', got '{data.get('symbol')}'"
        
        # Verify ticker_focused flag
        assert data.get('ticker_focused'), "Expected ticker_focused=True"
        
        print("✓ NVDA prediction returns correct symbol and ticker_focused flag")

    def test_aapl_prediction_returns_aapl_symbol(self):
        """Verify GET /api/market/prediction/AAPL returns symbol='AAPL'"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/AAPL", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        # Verify symbol field
        assert data.get('symbol') == 'AAPL', f"Expected symbol='AAPL', got '{data.get('symbol')}'"
        
        # Verify ticker_focused flag
        assert data.get('ticker_focused'), "Expected ticker_focused=True"
        
        print("✓ AAPL prediction returns correct symbol and ticker_focused flag")

    def test_general_market_returns_market_symbol(self):
        """Verify GET /api/market/prediction (no ticker) returns symbol='MARKET'"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        # Verify symbol field for general market
        assert data.get('symbol') == 'MARKET', f"Expected symbol='MARKET', got '{data.get('symbol')}'"
        
        # ticker_focused should be null/None for general market
        assert data.get('ticker_focused') is None or not data.get('ticker_focused'), \
            "Expected ticker_focused=null/false for general market"
        
        print("✓ General market prediction returns symbol='MARKET'")

    def test_nvda_summary_mentions_nvda(self):
        """Verify NVDA prediction summary is NVDA-focused, not generic market"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/NVDA", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        summary = data.get('summary', '')
        
        # Summary should mention NVDA, not just SPY or generic market
        assert 'NVDA' in summary or 'Nvidia' in summary.lower() or 'nvidia' in summary.lower(), \
            f"Summary should mention NVDA, got: {summary[:200]}"
        
        print("✓ NVDA prediction summary is NVDA-focused")

    def test_lowercase_ticker_normalized(self):
        """Verify lowercase ticker is normalized to uppercase"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/tsla", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        # Symbol should be uppercase
        assert data.get('symbol') == 'TSLA', f"Expected symbol='TSLA', got '{data.get('symbol')}'"
        
        print("✓ Lowercase ticker normalized to uppercase")

    def test_prediction_has_required_fields(self):
        """Verify prediction response has all required fields"""
        response = requests.get(f"{BASE_URL}/api/market/prediction/SPY", timeout=120)
        assert response.status_code == 200
        data = response.json()
        
        required_fields = ['symbol', 'overall_direction', 'confidence_score', 'summary', 'timeframes']
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        print("✓ Prediction has all required fields")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
