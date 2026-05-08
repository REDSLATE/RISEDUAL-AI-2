"""
Iteration 65: Order Flow / Institutional Wall Detection Tests

Tests the new Order Flow system:
- GET /api/order-flow/{symbol} endpoint for AAPL, SPY, BTC
- get_volume_profile() function
- get_order_flow_context() function
- Crew definitions integration (signature verification only - no actual API calls)
"""

import pytest
import requests
import os
import sys
import asyncio

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestOrderFlowEndpoint:
    """Test GET /api/order-flow/{symbol} endpoint"""

    def test_order_flow_aapl_returns_volume_profile(self):
        """AAPL should return volume profile with walls, summary, POC, and bias"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Verify required fields
        assert "ticker" in data, "Missing ticker field"
        assert data["ticker"] == "AAPL", f"Expected AAPL, got {data['ticker']}"
        assert "current_price" in data, "Missing current_price"
        assert isinstance(data["current_price"], (int, float)), "current_price should be numeric"
        
        # Verify walls structure
        assert "walls" in data, "Missing walls field"
        assert isinstance(data["walls"], list), "walls should be a list"
        
        # Verify summary structure
        assert "summary" in data, "Missing summary field"
        summary = data["summary"]
        assert "support_walls" in summary, "Missing support_walls count"
        assert "resistance_walls" in summary, "Missing resistance_walls count"
        assert "bias" in summary, "Missing bias field"
        assert summary["bias"] in ["INSTITUTIONAL_BID", "INSTITUTIONAL_ASK", "BALANCED", "NO_DATA"], \
            f"Invalid bias: {summary['bias']}"
        
        # Verify POC
        assert "point_of_control" in data, "Missing point_of_control"
        poc = data["point_of_control"]
        assert "price" in poc, "POC missing price"
        assert "volume" in poc, "POC missing volume"
        
        print(f"AAPL Order Flow: {len(data['walls'])} walls, bias={summary['bias']}, POC=${poc['price']}")

    def test_order_flow_spy_returns_support_resistance_counts(self):
        """SPY should return data with support/resistance wall counts"""
        response = requests.get(f"{BASE_URL}/api/order-flow/SPY")
        assert response.status_code == 200
        
        data = response.json()
        summary = data.get("summary", {})
        
        assert "support_walls" in summary, "Missing support_walls"
        assert "resistance_walls" in summary, "Missing resistance_walls"
        assert isinstance(summary["support_walls"], int), "support_walls should be int"
        assert isinstance(summary["resistance_walls"], int), "resistance_walls should be int"
        
        # Verify total volume fields
        assert "total_support_volume" in summary, "Missing total_support_volume"
        assert "total_resistance_volume" in summary, "Missing total_resistance_volume"
        
        print(f"SPY: {summary['support_walls']} support walls, {summary['resistance_walls']} resistance walls")

    def test_order_flow_btc_converts_to_btc_usd(self):
        """BTC should be converted to BTC-USD for yfinance"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC")
        assert response.status_code == 200
        
        data = response.json()
        
        # Should return BTC data (ticker field shows original input)
        assert data.get("ticker") == "BTC", f"Expected BTC, got {data.get('ticker')}"
        
        # Should have valid crypto price (much higher than stocks)
        current_price = data.get("current_price", 0)
        assert current_price > 10000, f"BTC price {current_price} seems too low for crypto"
        
        # Should have walls or at least valid structure
        assert "walls" in data, "Missing walls"
        assert "summary" in data, "Missing summary"
        
        print(f"BTC: price=${current_price}, bias={data['summary'].get('bias')}")

    def test_order_flow_wall_structure(self):
        """Verify wall objects have correct structure"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL")
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        if walls:
            wall = walls[0]
            required_fields = ["price", "volume", "ratio", "type", "strength", "distance_pct"]
            for field in required_fields:
                assert field in wall, f"Wall missing {field}"
            
            assert wall["type"] in ["support", "resistance"], f"Invalid wall type: {wall['type']}"
            assert wall["strength"] in ["major", "minor"], f"Invalid strength: {wall['strength']}"
            assert isinstance(wall["ratio"], (int, float)), "ratio should be numeric"
            
            print(f"Wall structure valid: ${wall['price']} {wall['type']} ({wall['strength']}, {wall['ratio']}x)")

    def test_order_flow_price_range(self):
        """Verify price_range field is present and valid"""
        response = requests.get(f"{BASE_URL}/api/order-flow/NVDA")
        assert response.status_code == 200
        
        data = response.json()
        
        assert "price_range" in data, "Missing price_range"
        price_range = data["price_range"]
        assert "low" in price_range, "Missing price_range.low"
        assert "high" in price_range, "Missing price_range.high"
        assert price_range["low"] <= price_range["high"], "low should be <= high"
        
        print(f"NVDA price range: ${price_range['low']} - ${price_range['high']}")

    def test_order_flow_metadata_fields(self):
        """Verify metadata fields: period, interval, total_bars, analyzed_at"""
        response = requests.get(f"{BASE_URL}/api/order-flow/MSFT")
        assert response.status_code == 200
        
        data = response.json()
        
        assert "period" in data, "Missing period"
        assert "interval" in data, "Missing interval"
        assert "total_bars" in data, "Missing total_bars"
        assert "analyzed_at" in data, "Missing analyzed_at"
        assert "median_volume" in data, "Missing median_volume"
        
        assert data["period"] == "2d", f"Expected 2d period, got {data['period']}"
        assert data["interval"] == "5m", f"Expected 5m interval, got {data['interval']}"
        assert data["total_bars"] > 0, "total_bars should be positive"
        
        print(f"MSFT: {data['total_bars']} bars, median_vol={data['median_volume']}")


class TestOrderFlowService:
    """Test order_flow_service.py functions directly"""

    def test_get_volume_profile_returns_dict(self):
        """get_volume_profile should return a dict with expected keys"""
        from services.order_flow_service import get_volume_profile
        
        result = asyncio.run(get_volume_profile("AAPL"))
        
        assert isinstance(result, dict), "Should return dict"
        assert "ticker" in result or "error" in result, "Should have ticker or error"
        
        if "error" not in result:
            assert "walls" in result, "Missing walls"
            assert "summary" in result, "Missing summary"
            assert "point_of_control" in result, "Missing POC"
            print(f"get_volume_profile(AAPL): {len(result.get('walls', []))} walls")

    def test_get_order_flow_context_returns_formatted_text(self):
        """get_order_flow_context should return formatted text for AI injection"""
        from services.order_flow_service import get_order_flow_context
        
        result = asyncio.run(get_order_flow_context("AAPL"))
        
        assert isinstance(result, str), "Should return string"
        
        if result:  # May be empty if no walls detected
            assert "ORDER FLOW ANALYSIS" in result, "Should contain header"
            assert "AAPL" in result, "Should mention ticker"
            assert "Institutional Bias" in result, "Should mention bias"
            assert "Implications" in result, "Should have implications section"
            print(f"get_order_flow_context(AAPL): {len(result)} chars")
        else:
            print("get_order_flow_context(AAPL): Empty (no walls detected)")

    def test_crypto_ticker_conversion(self):
        """Crypto tickers should be converted to -USD format"""
        from services.order_flow_service import _yf_symbol
        
        assert _yf_symbol("BTC") == "BTC-USD", "BTC should convert to BTC-USD"
        assert _yf_symbol("ETH") == "ETH-USD", "ETH should convert to ETH-USD"
        assert _yf_symbol("SOL") == "SOL-USD", "SOL should convert to SOL-USD"
        assert _yf_symbol("AAPL") == "AAPL", "AAPL should stay AAPL"
        assert _yf_symbol("btc") == "BTC-USD", "lowercase btc should convert"
        assert _yf_symbol("BTC-USD") == "BTC-USD", "Already formatted should stay"
        
        print("Crypto ticker conversion: PASS")

    def test_wall_detection_threshold(self):
        """Verify wall detection uses 3x median threshold"""
        from services.order_flow_service import WALL_MULTIPLIER, SIGNIFICANT_WALL
        
        assert WALL_MULTIPLIER == 3.0, f"WALL_MULTIPLIER should be 3.0, got {WALL_MULTIPLIER}"
        assert SIGNIFICANT_WALL == 5.0, f"SIGNIFICANT_WALL should be 5.0, got {SIGNIFICANT_WALL}"
        
        print(f"Wall thresholds: minor={WALL_MULTIPLIER}x, major={SIGNIFICANT_WALL}x")


class TestCrewDefinitionsIntegration:
    """Verify crew definitions properly integrate order_flow_context (signature only)"""

    def test_run_war_room_crew_fetches_order_flow(self):
        """run_war_room_crew should fetch order_flow_context internally"""
        import inspect
        from services.crew_definitions import run_war_room_crew
        
        # Check source code contains order_flow_context fetch
        source = inspect.getsource(run_war_room_crew)
        
        assert "order_flow_context" in source, "Should reference order_flow_context"
        assert "get_order_flow_context" in source, "Should call get_order_flow_context"
        assert "INSTITUTIONAL ORDER FLOW" in source, "Should inject into prompt"
        
        print("run_war_room_crew: order_flow integration verified")

    def test_run_hypothesis_crew_fetches_order_flow(self):
        """run_hypothesis_crew should fetch order_flow_context internally"""
        import inspect
        from services.crew_definitions import run_hypothesis_crew
        
        source = inspect.getsource(run_hypothesis_crew)
        
        assert "order_flow_context" in source, "Should reference order_flow_context"
        assert "get_order_flow_context" in source, "Should call get_order_flow_context"
        assert "INSTITUTIONAL ORDER FLOW" in source, "Should inject into prompt"
        
        print("run_hypothesis_crew: order_flow integration verified")

    def test_run_prediction_crew_accepts_order_flow_param(self):
        """run_prediction_crew should accept order_flow_context parameter"""
        import inspect
        from services.crew_definitions import run_prediction_crew
        
        sig = inspect.signature(run_prediction_crew)
        params = list(sig.parameters.keys())
        
        assert "order_flow_context" in params, "Should have order_flow_context parameter"
        
        # Check it's used in the function
        source = inspect.getsource(run_prediction_crew)
        assert "INSTITUTIONAL ORDER FLOW" in source, "Should inject into prompt"
        
        print("run_prediction_crew: order_flow_context parameter verified")

    def test_market_prediction_service_fetches_spy_order_flow(self):
        """MarketPredictionService should fetch SPY order flow for market prediction"""
        import inspect
        from services.market_prediction_service import MarketPredictionService
        
        source = inspect.getsource(MarketPredictionService.analyze_market)
        
        assert "order_flow_context" in source, "Should reference order_flow_context"
        assert "get_order_flow_context" in source, "Should call get_order_flow_context"
        assert '"SPY"' in source or "'SPY'" in source, "Should fetch SPY order flow"
        
        print("MarketPredictionService: SPY order flow integration verified")


class TestOrderFlowEdgeCases:
    """Test edge cases and error handling"""

    def test_invalid_symbol_returns_error_or_empty(self):
        """Invalid symbol should return error or empty walls"""
        response = requests.get(f"{BASE_URL}/api/order-flow/INVALID123XYZ")
        
        # Should not crash - either 200 with error or 500
        assert response.status_code in [200, 500], f"Unexpected status: {response.status_code}"
        
        if response.status_code == 200:
            data = response.json()
            # Should have error or empty walls
            has_error = "error" in data and data["error"]
            has_empty_walls = data.get("walls", []) == []
            assert has_error or has_empty_walls, "Should indicate no data"
        
        print("Invalid symbol handling: PASS")

    def test_eth_crypto_conversion(self):
        """ETH should be converted and return valid data"""
        response = requests.get(f"{BASE_URL}/api/order-flow/ETH")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("ticker") == "ETH", f"Expected ETH, got {data.get('ticker')}"
        
        # ETH price should be in thousands
        current_price = data.get("current_price", 0)
        assert current_price > 100, f"ETH price {current_price} seems too low"
        
        print(f"ETH: price=${current_price}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
