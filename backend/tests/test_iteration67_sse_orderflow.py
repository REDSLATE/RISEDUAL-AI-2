"""
Iteration 67: SSE Order Flow Streaming Tests

Tests the new real-time order flow heatmap streaming feature:
- GET /api/stream/orderflow/{symbol} - SSE endpoint for crypto tickers
- Validates SSE snapshot data structure (type, symbol, ts, mid, spread, bids, asks, walls, bias, bid_pct)
- Validates intensity values are log-scaled (range 0.2-1.0)
- Validates error response for non-crypto tickers (AAPL)
- Validates static /api/order-flow/{symbol} still works for both crypto and stocks
"""

import pytest
import requests
import os
import json

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestSSEOrderFlowStream:
    """Tests for the SSE order flow streaming endpoint"""
    
    def test_sse_btc_stream_returns_snapshot_events(self):
        """GET /api/stream/orderflow/BTC returns SSE stream with snapshot events"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("Content-Type", "")
        
        # Read first few events
        events = []
        for i, line in enumerate(response.iter_lines(decode_unicode=True)):
            if line:
                events.append(line)
            if i > 10:  # Get enough lines for at least one event
                break
        response.close()
        
        # Should have event: snapshot and data: {...}
        event_lines = [e for e in events if e.startswith("event:")]
        data_lines = [e for e in events if e.startswith("data:")]
        
        assert len(event_lines) > 0, "Should have at least one event line"
        assert len(data_lines) > 0, "Should have at least one data line"
        assert "snapshot" in event_lines[0], "First event should be snapshot"
        print("PASS: BTC SSE stream returns snapshot events")
    
    def test_sse_eth_stream_returns_snapshot_events(self):
        """GET /api/stream/orderflow/ETH returns SSE stream (ETH is supported crypto)"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/ETH",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("Content-Type", "")
        
        # Read first event
        events = []
        for i, line in enumerate(response.iter_lines(decode_unicode=True)):
            if line:
                events.append(line)
            if i > 10:
                break
        response.close()
        
        event_lines = [e for e in events if e.startswith("event:")]
        assert len(event_lines) > 0, "Should have at least one event line"
        print("PASS: ETH SSE stream returns snapshot events")
    
    def test_sse_aapl_returns_error(self):
        """GET /api/stream/orderflow/AAPL returns error (not crypto, not supported for live stream)"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/AAPL",
            timeout=10
        )
        
        assert response.status_code == 200  # Returns JSON error, not 4xx
        data = response.json()
        assert "error" in data, "Should return error for non-crypto ticker"
        assert "AAPL" in data["error"] or "not a supported" in data["error"].lower()
        print(f"PASS: AAPL SSE returns error: {data['error']}")
    
    def test_sse_snapshot_data_structure(self):
        """SSE snapshot data has correct fields: type, symbol, ts, mid, spread, bids, asks, walls, bias, bid_pct"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        
        # Find first data line
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()  # Remove "data:" prefix
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None, "Should receive snapshot data"
        
        # Validate required fields
        required_fields = ["type", "symbol", "ts", "mid", "spread", "bids", "asks", "walls", "bias", "bid_pct"]
        for field in required_fields:
            assert field in snapshot_data, f"Missing required field: {field}"
        
        # Validate field types
        assert snapshot_data["type"] == "snapshot"
        assert snapshot_data["symbol"] == "BTC"
        assert isinstance(snapshot_data["ts"], str)  # ISO timestamp
        assert isinstance(snapshot_data["mid"], (int, float))
        assert isinstance(snapshot_data["spread"], (int, float))
        assert isinstance(snapshot_data["bids"], list)
        assert isinstance(snapshot_data["asks"], list)
        assert isinstance(snapshot_data["walls"], list)
        assert snapshot_data["bias"] in ["INSTITUTIONAL_BID", "INSTITUTIONAL_ASK", "BALANCED"]
        assert isinstance(snapshot_data["bid_pct"], (int, float))
        
        print(f"PASS: Snapshot has all required fields - mid: ${snapshot_data['mid']}, bias: {snapshot_data['bias']}")
    
    def test_sse_snapshot_intensity_values_log_scaled(self):
        """SSE snapshot intensity values are log-scaled (range 0.2-1.0, not 0.001-1.0)"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        
        # Find first data line
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        
        # Check bid intensities
        bid_intensities = [b["intensity"] for b in snapshot_data["bids"]]
        ask_intensities = [a["intensity"] for a in snapshot_data["asks"]]
        
        all_intensities = bid_intensities + ask_intensities
        
        # All intensities should be between 0 and 1
        for intensity in all_intensities:
            assert 0 <= intensity <= 1, f"Intensity {intensity} out of range [0, 1]"
        
        # Log-scaled means most values should be > 0.2 (not tiny like 0.001)
        # At least some values should be reasonably high
        high_intensities = [i for i in all_intensities if i > 0.2]
        assert len(high_intensities) > len(all_intensities) * 0.3, \
            f"Log-scaled intensities should have many values > 0.2, got {len(high_intensities)}/{len(all_intensities)}"
        
        print(f"PASS: Intensities are log-scaled - min: {min(all_intensities):.3f}, max: {max(all_intensities):.3f}, >0.2: {len(high_intensities)}/{len(all_intensities)}")
    
    def test_sse_snapshot_bids_asks_structure(self):
        """SSE snapshot bids/asks have price, intensity, qty fields"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        assert len(snapshot_data["bids"]) > 0, "Should have bid levels"
        assert len(snapshot_data["asks"]) > 0, "Should have ask levels"
        
        # Check bid structure
        bid = snapshot_data["bids"][0]
        assert "price" in bid, "Bid should have price"
        assert "intensity" in bid, "Bid should have intensity"
        assert "qty" in bid, "Bid should have qty"
        
        # Check ask structure
        ask = snapshot_data["asks"][0]
        assert "price" in ask, "Ask should have price"
        assert "intensity" in ask, "Ask should have intensity"
        assert "qty" in ask, "Ask should have qty"
        
        print(f"PASS: Bids/asks have correct structure - {len(snapshot_data['bids'])} bids, {len(snapshot_data['asks'])} asks")
    
    def test_sse_snapshot_walls_structure(self):
        """SSE snapshot walls have price, qty, value, ratio, side, strength fields"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        assert response.status_code == 200
        
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        
        # Walls may or may not be present depending on market conditions
        if len(snapshot_data["walls"]) > 0:
            wall = snapshot_data["walls"][0]
            assert "price" in wall, "Wall should have price"
            assert "qty" in wall, "Wall should have qty"
            assert "value" in wall, "Wall should have value"
            assert "ratio" in wall, "Wall should have ratio"
            assert "side" in wall, "Wall should have side"
            assert wall["side"] in ["bid", "ask"], f"Wall side should be bid or ask, got {wall['side']}"
            assert "strength" in wall, "Wall should have strength"
            assert wall["strength"] in ["major", "minor"], f"Wall strength should be major or minor, got {wall['strength']}"
            print(f"PASS: Walls have correct structure - {len(snapshot_data['walls'])} walls detected")
        else:
            print("PASS: No walls detected (market conditions may vary)")


class TestStaticOrderFlowEndpoint:
    """Tests for the static /api/order-flow/{symbol} endpoint"""
    
    def test_static_orderflow_btc_works(self):
        """GET /api/order-flow/BTC still works (snapshot endpoint, Binance L2)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=15)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "error" not in data, f"Should not have error: {data.get('error')}"
        assert data.get("source") == "binance_l2", f"BTC should use binance_l2, got {data.get('source')}"
        assert "current_price" in data
        assert "walls" in data
        assert "summary" in data
        assert data["summary"]["bias"] in ["INSTITUTIONAL_BID", "INSTITUTIONAL_ASK", "BALANCED", "NO_DATA"]
        
        print(f"PASS: Static /api/order-flow/BTC works - price: ${data['current_price']}, source: {data['source']}")
    
    def test_static_orderflow_aapl_works(self):
        """GET /api/order-flow/AAPL still works (snapshot endpoint, yfinance)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=15)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "error" not in data, f"Should not have error: {data.get('error')}"
        assert data.get("source") == "yfinance_profile", f"AAPL should use yfinance_profile, got {data.get('source')}"
        assert "current_price" in data
        assert "point_of_control" in data
        assert "price_range" in data
        assert "walls" in data
        
        print(f"PASS: Static /api/order-flow/AAPL works - price: ${data['current_price']}, source: {data['source']}")
    
    def test_static_orderflow_eth_works(self):
        """GET /api/order-flow/ETH works (Binance L2)"""
        response = requests.get(f"{BASE_URL}/api/order-flow/ETH", timeout=15)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "error" not in data
        assert data.get("source") == "binance_l2"
        assert "spread" in data
        assert "depth_levels" in data
        
        print(f"PASS: Static /api/order-flow/ETH works - price: ${data['current_price']}")


class TestSSECryptoValidation:
    """Tests for crypto ticker validation in SSE endpoint"""
    
    def test_sse_sol_supported(self):
        """SOL is a supported crypto ticker for SSE"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/SOL",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        # Should return SSE stream, not error
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            response.close()
            print("PASS: SOL SSE stream is supported")
        else:
            # Check if it's an error response
            data = response.json()
            assert "error" not in data, f"SOL should be supported, got error: {data.get('error')}"
    
    def test_sse_msft_not_supported(self):
        """MSFT is not a crypto ticker, should return error"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/MSFT",
            timeout=10
        )
        
        data = response.json()
        assert "error" in data, "MSFT should return error for SSE"
        print(f"PASS: MSFT SSE returns error: {data['error']}")
    
    def test_sse_spy_not_supported(self):
        """SPY is not a crypto ticker, should return error"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/SPY",
            timeout=10
        )
        
        data = response.json()
        assert "error" in data, "SPY should return error for SSE"
        print(f"PASS: SPY SSE returns error: {data['error']}")


class TestSSEAdditionalFields:
    """Tests for additional SSE snapshot fields"""
    
    def test_sse_snapshot_has_spread_fields(self):
        """SSE snapshot has spread and spread_pct fields"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        assert "spread" in snapshot_data
        assert "spread_pct" in snapshot_data
        assert isinstance(snapshot_data["spread"], (int, float))
        assert isinstance(snapshot_data["spread_pct"], (int, float))
        
        print(f"PASS: Spread fields present - spread: ${snapshot_data['spread']}, spread_pct: {snapshot_data['spread_pct']}%")
    
    def test_sse_snapshot_has_bid_ask_totals(self):
        """SSE snapshot has bid_total and ask_total fields"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        assert "bid_total" in snapshot_data
        assert "ask_total" in snapshot_data
        assert isinstance(snapshot_data["bid_total"], (int, float))
        assert isinstance(snapshot_data["ask_total"], (int, float))
        
        print(f"PASS: Bid/Ask totals present - bid: ${snapshot_data['bid_total']:.2f}, ask: ${snapshot_data['ask_total']:.2f}")
    
    def test_sse_snapshot_has_wall_events(self):
        """SSE snapshot has wall_events field (may be empty)"""
        response = requests.get(
            f"{BASE_URL}/api/stream/orderflow/BTC",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=10
        )
        
        snapshot_data = None
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                json_str = line[5:].strip()
                snapshot_data = json.loads(json_str)
                break
        response.close()
        
        assert snapshot_data is not None
        assert "wall_events" in snapshot_data
        assert isinstance(snapshot_data["wall_events"], list)
        
        print(f"PASS: wall_events field present - {len(snapshot_data['wall_events'])} events")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
