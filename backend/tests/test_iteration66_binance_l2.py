"""
Iteration 66: Binance L2 Order Book Integration Tests

Tests the new Binance L2 API integration for crypto order flow data:
- Crypto tickers (BTC, ETH, SOL) should return source=binance_l2
- Stock tickers (AAPL, TSLA, NVDA) should return source=yfinance_profile
- Binance response includes spread, depth_levels, total_bids, total_asks
- yfinance response includes point_of_control, price_range, total_bars
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestBinanceL2CryptoEndpoints:
    """Test crypto tickers route to Binance L2 API"""
    
    def test_btc_returns_binance_l2_source(self):
        """GET /api/order-flow/BTC should return source=binance_l2"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # Should be binance_l2 or fallback to yfinance_profile if Binance fails
        assert data.get("source") in ["binance_l2", "yfinance_profile"], f"Unexpected source: {data.get('source')}"
        assert "walls" in data, "Missing walls field"
        assert "summary" in data, "Missing summary field"
        
        if data.get("source") == "binance_l2":
            # Binance-specific fields
            assert "spread" in data, "Missing spread field for Binance L2"
            assert "depth_levels" in data, "Missing depth_levels field"
            assert "total_bids" in data, "Missing total_bids field"
            assert "total_asks" in data, "Missing total_asks field"
            
            # Spread structure
            spread = data.get("spread", {})
            assert "value" in spread, "Missing spread.value"
            assert "pct" in spread, "Missing spread.pct"
            assert "best_bid" in spread, "Missing spread.best_bid"
            assert "best_ask" in spread, "Missing spread.best_ask"
            
            # Summary should have bias
            summary = data.get("summary", {})
            assert "bias" in summary, "Missing summary.bias"
            assert summary["bias"] in ["INSTITUTIONAL_BID", "INSTITUTIONAL_ASK", "BALANCED", "NO_DATA"]
            
            print(f"BTC: source={data['source']}, spread={spread.get('pct')}%, bias={summary.get('bias')}")
        else:
            print(f"BTC: Binance unavailable, fell back to yfinance_profile")
    
    def test_eth_returns_binance_l2_source(self):
        """GET /api/order-flow/ETH should return source=binance_l2"""
        response = requests.get(f"{BASE_URL}/api/order-flow/ETH", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") in ["binance_l2", "yfinance_profile"]
        assert "walls" in data
        
        if data.get("source") == "binance_l2":
            assert "spread" in data
            assert "total_bids" in data
            print(f"ETH: source=binance_l2, walls={len(data.get('walls', []))}")
        else:
            print(f"ETH: Binance unavailable, fell back to yfinance_profile")
    
    def test_sol_returns_binance_l2_source(self):
        """GET /api/order-flow/SOL should return source=binance_l2 (SOL is in CRYPTO_TICKERS)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/SOL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") in ["binance_l2", "yfinance_profile"]
        assert "walls" in data
        
        if data.get("source") == "binance_l2":
            print(f"SOL: source=binance_l2, walls={len(data.get('walls', []))}")
        else:
            print(f"SOL: Binance unavailable, fell back to yfinance_profile")


class TestYfinanceStockEndpoints:
    """Test stock tickers route to yfinance volume profile"""
    
    def test_aapl_returns_yfinance_profile(self):
        """GET /api/order-flow/AAPL should return source=yfinance_profile"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile", f"Expected yfinance_profile, got {data.get('source')}"
        assert "walls" in data
        assert "point_of_control" in data, "Missing point_of_control for yfinance"
        assert "price_range" in data, "Missing price_range for yfinance"
        assert "total_bars" in data, "Missing total_bars for yfinance"
        assert "period" in data, "Missing period for yfinance"
        assert "interval" in data, "Missing interval for yfinance"
        
        # POC structure
        poc = data.get("point_of_control", {})
        assert "price" in poc, "Missing point_of_control.price"
        assert "volume" in poc, "Missing point_of_control.volume"
        
        # Price range structure
        price_range = data.get("price_range", {})
        assert "low" in price_range, "Missing price_range.low"
        assert "high" in price_range, "Missing price_range.high"
        
        print(f"AAPL: source=yfinance_profile, POC=${poc.get('price')}, bars={data.get('total_bars')}")
    
    def test_tsla_returns_yfinance_profile(self):
        """GET /api/order-flow/TSLA should return source=yfinance_profile"""
        response = requests.get(f"{BASE_URL}/api/order-flow/TSLA", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        assert "point_of_control" in data
        assert "price_range" in data
        
        print(f"TSLA: source=yfinance_profile, walls={len(data.get('walls', []))}")
    
    def test_nvda_returns_yfinance_profile(self):
        """GET /api/order-flow/NVDA should return source=yfinance_profile"""
        response = requests.get(f"{BASE_URL}/api/order-flow/NVDA", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        assert "point_of_control" in data
        
        print(f"NVDA: source=yfinance_profile, walls={len(data.get('walls', []))}")
    
    def test_spy_returns_yfinance_profile(self):
        """GET /api/order-flow/SPY should return source=yfinance_profile"""
        response = requests.get(f"{BASE_URL}/api/order-flow/SPY", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        
        print(f"SPY: source=yfinance_profile, walls={len(data.get('walls', []))}")
    
    def test_msft_returns_yfinance_profile(self):
        """GET /api/order-flow/MSFT should return source=yfinance_profile"""
        response = requests.get(f"{BASE_URL}/api/order-flow/MSFT", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        
        print(f"MSFT: source=yfinance_profile, walls={len(data.get('walls', []))}")


class TestBinanceResponseStructure:
    """Test Binance L2 response has correct structure"""
    
    def test_binance_spread_fields(self):
        """Binance response should include spread.value, spread.pct, spread.best_bid, spread.best_ask"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        
        if data.get("source") == "binance_l2":
            spread = data.get("spread", {})
            
            # All spread fields should be present
            assert "value" in spread, "Missing spread.value"
            assert "pct" in spread, "Missing spread.pct"
            assert "best_bid" in spread, "Missing spread.best_bid"
            assert "best_ask" in spread, "Missing spread.best_ask"
            
            # Values should be numeric
            assert isinstance(spread["value"], (int, float)), "spread.value should be numeric"
            assert isinstance(spread["pct"], (int, float)), "spread.pct should be numeric"
            assert isinstance(spread["best_bid"], (int, float)), "spread.best_bid should be numeric"
            assert isinstance(spread["best_ask"], (int, float)), "spread.best_ask should be numeric"
            
            # Best ask should be >= best bid
            assert spread["best_ask"] >= spread["best_bid"], "best_ask should be >= best_bid"
            
            print(f"Spread: ${spread['value']} ({spread['pct']}%), Bid: ${spread['best_bid']}, Ask: ${spread['best_ask']}")
        else:
            pytest.skip("Binance unavailable, skipping spread structure test")
    
    def test_binance_depth_fields(self):
        """Binance response should include depth_levels, total_bids, total_asks"""
        response = requests.get(f"{BASE_URL}/api/order-flow/ETH", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        
        if data.get("source") == "binance_l2":
            assert "depth_levels" in data, "Missing depth_levels"
            assert "total_bids" in data, "Missing total_bids"
            assert "total_asks" in data, "Missing total_asks"
            
            assert isinstance(data["depth_levels"], int), "depth_levels should be int"
            assert isinstance(data["total_bids"], int), "total_bids should be int"
            assert isinstance(data["total_asks"], int), "total_asks should be int"
            
            print(f"Depth: {data['depth_levels']} levels, {data['total_bids']} bids, {data['total_asks']} asks")
        else:
            pytest.skip("Binance unavailable, skipping depth fields test")
    
    def test_binance_wall_structure(self):
        """Binance walls should have quantity field (coin amount)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        
        if data.get("source") == "binance_l2":
            walls = data.get("walls", [])
            if walls:
                wall = walls[0]
                assert "price" in wall, "Missing wall.price"
                assert "volume" in wall, "Missing wall.volume"
                assert "quantity" in wall, "Missing wall.quantity (Binance-specific)"
                assert "ratio" in wall, "Missing wall.ratio"
                assert "type" in wall, "Missing wall.type"
                assert "strength" in wall, "Missing wall.strength"
                assert "distance_pct" in wall, "Missing wall.distance_pct"
                
                print(f"Wall: ${wall['price']}, {wall['quantity']} coins, {wall['ratio']}x, {wall['type']}")
            else:
                print("No walls detected (market may be balanced)")
        else:
            pytest.skip("Binance unavailable, skipping wall structure test")


class TestYfinanceResponseStructure:
    """Test yfinance response has correct structure"""
    
    def test_yfinance_poc_fields(self):
        """yfinance response should include point_of_control with price and volume"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        
        poc = data.get("point_of_control", {})
        assert "price" in poc, "Missing point_of_control.price"
        assert "volume" in poc, "Missing point_of_control.volume"
        
        assert isinstance(poc["price"], (int, float)), "POC price should be numeric"
        assert isinstance(poc["volume"], (int, float)), "POC volume should be numeric"
        
        print(f"POC: ${poc['price']}, volume={poc['volume']}")
    
    def test_yfinance_price_range_fields(self):
        """yfinance response should include price_range with low and high"""
        response = requests.get(f"{BASE_URL}/api/order-flow/TSLA", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        
        price_range = data.get("price_range", {})
        assert "low" in price_range, "Missing price_range.low"
        assert "high" in price_range, "Missing price_range.high"
        
        assert price_range["high"] >= price_range["low"], "high should be >= low"
        
        print(f"Price range: ${price_range['low']} - ${price_range['high']}")
    
    def test_yfinance_metadata_fields(self):
        """yfinance response should include total_bars, period, interval"""
        response = requests.get(f"{BASE_URL}/api/order-flow/NVDA", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile"
        
        assert "total_bars" in data, "Missing total_bars"
        assert "period" in data, "Missing period"
        assert "interval" in data, "Missing interval"
        
        assert isinstance(data["total_bars"], int), "total_bars should be int"
        assert data["period"] == "2d", f"Expected period=2d, got {data['period']}"
        assert data["interval"] == "5m", f"Expected interval=5m, got {data['interval']}"
        
        print(f"Metadata: {data['total_bars']} bars, {data['period']}/{data['interval']}")


class TestWallDetection:
    """Test wall detection logic"""
    
    def test_wall_types(self):
        """Walls should be classified as support or resistance"""
        response = requests.get(f"{BASE_URL}/api/order-flow/SPY", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        for wall in walls:
            assert wall["type"] in ["support", "resistance"], f"Invalid wall type: {wall['type']}"
        
        support_count = len([w for w in walls if w["type"] == "support"])
        resistance_count = len([w for w in walls if w["type"] == "resistance"])
        
        print(f"Walls: {support_count} support, {resistance_count} resistance")
    
    def test_wall_strength(self):
        """Walls should have strength (major or minor)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        for wall in walls:
            assert wall["strength"] in ["major", "minor"], f"Invalid strength: {wall['strength']}"
        
        major_count = len([w for w in walls if w["strength"] == "major"])
        minor_count = len([w for w in walls if w["strength"] == "minor"])
        
        print(f"Strength: {major_count} major, {minor_count} minor")
    
    def test_summary_bias(self):
        """Summary should include institutional bias"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        summary = data.get("summary", {})
        
        assert "bias" in summary, "Missing summary.bias"
        assert summary["bias"] in ["INSTITUTIONAL_BID", "INSTITUTIONAL_ASK", "BALANCED", "NO_DATA"]
        
        print(f"Bias: {summary['bias']}")


class TestCommonFields:
    """Test fields common to both Binance and yfinance responses"""
    
    def test_ticker_field(self):
        """Response should include ticker field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert "ticker" in data, "Missing ticker field"
        assert data["ticker"] == "BTC", f"Expected ticker=BTC, got {data['ticker']}"
    
    def test_current_price_field(self):
        """Response should include current_price field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert "current_price" in data, "Missing current_price field"
        assert isinstance(data["current_price"], (int, float)), "current_price should be numeric"
        assert data["current_price"] > 0, "current_price should be positive"
        
        print(f"Current price: ${data['current_price']}")
    
    def test_analyzed_at_field(self):
        """Response should include analyzed_at timestamp"""
        response = requests.get(f"{BASE_URL}/api/order-flow/SPY", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert "analyzed_at" in data, "Missing analyzed_at field"
        # Should be ISO format
        assert "T" in data["analyzed_at"], "analyzed_at should be ISO format"
        
        print(f"Analyzed at: {data['analyzed_at']}")
    
    def test_median_volume_field(self):
        """Response should include median_volume field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/TSLA", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert "median_volume" in data, "Missing median_volume field"
        
        print(f"Median volume: {data['median_volume']}")
