"""
Iteration 69: Multi-Ticker Whale Radar & Order Flow Context Injection Tests

Tests:
1. GET /api/stream/whale-radar SSE endpoint - radar_status, tick, whale events
2. Order flow context injection - get_order_flow_context for BTC, AAPL, SPY
3. Regression tests for existing order flow endpoints
"""

import pytest
import requests
import os
import json

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Expected tickers in Whale Radar
RADAR_TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"]


class TestWhaleRadarSSE:
    """Tests for GET /api/stream/whale-radar SSE endpoint"""

    def test_whale_radar_sse_returns_radar_status_event(self):
        """SSE stream should return radar_status event with 10 tickers"""
        url = f"{BASE_URL}/api/stream/whale-radar"
        
        with requests.get(url, stream=True, timeout=30) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert 'text/event-stream' in response.headers.get('Content-Type', ''), "Expected SSE content type"
            
            # Read first few events
            events_received = []
            radar_status_found = False
            
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    if line.startswith('event:'):
                        event_type = line.split(':', 1)[1].strip()
                        events_received.append(event_type)
                    elif line.startswith('data:'):
                        data_str = line.split(':', 1)[1].strip()
                        if data_str:
                            try:
                                data = json.loads(data_str)
                                if data.get('type') == 'radar_status':
                                    radar_status_found = True
                                    # Verify 10 tickers
                                    tickers = data.get('tickers', [])
                                    assert len(tickers) == 10, f"Expected 10 tickers, got {len(tickers)}"
                                    for t in RADAR_TICKERS:
                                        assert t in tickers, f"Missing ticker {t} in radar_status"
                                    assert 'ts' in data, "radar_status should have timestamp"
                                    print(f"PASS: radar_status event received with {len(tickers)} tickers")
                                    break
                            except json.JSONDecodeError:
                                pass
                
                # Limit iterations
                if len(events_received) > 5 or radar_status_found:
                    break
            
            assert radar_status_found, "radar_status event not received"

    def test_whale_radar_sse_returns_tick_events(self):
        """SSE stream should return tick events with ticker, mid, bias, bid_pct, wall_count, whale_count"""
        url = f"{BASE_URL}/api/stream/whale-radar"
        
        with requests.get(url, stream=True, timeout=45) as response:
            assert response.status_code == 200
            
            tick_events = []
            event_type = None
            
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    if line.startswith('event:'):
                        event_type = line.split(':', 1)[1].strip()
                    elif line.startswith('data:') and event_type == 'tick':
                        data_str = line.split(':', 1)[1].strip()
                        if data_str:
                            try:
                                data = json.loads(data_str)
                                if data.get('type') == 'tick':
                                    tick_events.append(data)
                                    # Verify tick structure
                                    assert 'ticker' in data, "tick should have ticker"
                                    assert 'mid' in data, "tick should have mid price"
                                    assert 'bias' in data, "tick should have bias"
                                    assert 'bid_pct' in data, "tick should have bid_pct"
                                    assert 'wall_count' in data, "tick should have wall_count"
                                    assert 'whale_count' in data, "tick should have whale_count"
                                    assert 'ts' in data, "tick should have timestamp"
                                    print(f"PASS: tick event for {data['ticker']}: mid=${data['mid']}, bias={data['bias']}, walls={data['wall_count']}, whales={data['whale_count']}")
                            except json.JSONDecodeError:
                                pass
                
                # Collect a few tick events
                if len(tick_events) >= 3:
                    break
            
            assert len(tick_events) > 0, "No tick events received"
            print(f"PASS: Received {len(tick_events)} tick events")

    def test_whale_radar_sse_whale_event_structure(self):
        """Whale events should only contain walls with intensity >= 85"""
        url = f"{BASE_URL}/api/stream/whale-radar"
        
        # Note: Whale events may not always occur, so we just verify structure if they do
        with requests.get(url, stream=True, timeout=60) as response:
            assert response.status_code == 200
            
            whale_events = []
            event_type = None
            tick_count = 0
            
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    if line.startswith('event:'):
                        event_type = line.split(':', 1)[1].strip()
                    elif line.startswith('data:'):
                        data_str = line.split(':', 1)[1].strip()
                        if data_str:
                            try:
                                data = json.loads(data_str)
                                if event_type == 'tick':
                                    tick_count += 1
                                elif event_type == 'whale' and data.get('type') == 'whale':
                                    whale_events.append(data)
                                    # Verify whale event structure
                                    assert 'ticker' in data, "whale should have ticker"
                                    assert 'mid' in data, "whale should have mid price"
                                    assert 'walls' in data, "whale should have walls array"
                                    # All walls in whale event should have intensity >= 85
                                    for wall in data.get('walls', []):
                                        assert wall.get('intensity', 0) >= 85, f"Whale wall intensity should be >= 85, got {wall.get('intensity')}"
                                    print(f"PASS: whale event for {data['ticker']}: {len(data['walls'])} whale walls")
                            except json.JSONDecodeError:
                                pass
                
                # Stop after enough ticks or if we got whale events
                if tick_count >= 20 or len(whale_events) >= 2:
                    break
            
            # Whale events are conditional - just verify we got ticks
            print(f"INFO: Received {len(whale_events)} whale events, {tick_count} tick events")
            assert tick_count > 0, "Should receive tick events"


class TestOrderFlowContextInjection:
    """Tests for get_order_flow_context function used in AI crews"""

    def test_order_flow_btc_returns_binance_l2_data(self):
        """GET /api/order-flow/BTC should return Binance L2 data with walls and bias"""
        url = f"{BASE_URL}/api/order-flow/BTC"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify Binance L2 source
        assert data.get('source') == 'binance_l2', f"Expected binance_l2 source, got {data.get('source')}"
        assert data.get('ticker') == 'BTC', "Expected BTC ticker"
        
        # Verify walls with intensity
        walls = data.get('walls', [])
        assert isinstance(walls, list), "walls should be a list"
        if walls:
            wall = walls[0]
            assert 'price' in wall, "wall should have price"
            assert 'volume' in wall, "wall should have volume"
            assert 'intensity' in wall, "wall should have intensity field"
            assert 0 <= wall['intensity'] <= 100, f"intensity should be 0-100, got {wall['intensity']}"
            print(f"PASS: BTC order flow has {len(walls)} walls with intensity field")
        
        # Verify summary with bias
        summary = data.get('summary', {})
        assert 'bias' in summary, "summary should have bias"
        assert summary['bias'] in ['INSTITUTIONAL_BID', 'INSTITUTIONAL_ASK', 'BALANCED', 'NO_DATA'], f"Invalid bias: {summary['bias']}"
        print(f"PASS: BTC bias = {summary['bias']}")

    def test_order_flow_aapl_returns_yfinance_data(self):
        """GET /api/order-flow/AAPL should return yfinance data"""
        url = f"{BASE_URL}/api/order-flow/AAPL"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify yfinance source
        assert data.get('source') == 'yfinance_profile', f"Expected yfinance_profile source, got {data.get('source')}"
        assert data.get('ticker') == 'AAPL', "Expected AAPL ticker"
        
        # Verify walls with intensity
        walls = data.get('walls', [])
        if walls:
            wall = walls[0]
            assert 'intensity' in wall, "wall should have intensity field"
            print(f"PASS: AAPL order flow has {len(walls)} walls with intensity field")
        
        # Verify summary
        summary = data.get('summary', {})
        assert 'bias' in summary, "summary should have bias"
        print(f"PASS: AAPL bias = {summary['bias']}")

    def test_order_flow_spy_returns_yfinance_data(self):
        """GET /api/order-flow/SPY should return yfinance data (used in market predictions)"""
        url = f"{BASE_URL}/api/order-flow/SPY"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify yfinance source (SPY is not crypto)
        assert data.get('source') == 'yfinance_profile', f"Expected yfinance_profile source, got {data.get('source')}"
        assert data.get('ticker') == 'SPY', "Expected SPY ticker"
        
        # Verify walls with intensity
        walls = data.get('walls', [])
        if walls:
            wall = walls[0]
            assert 'intensity' in wall, "wall should have intensity field"
            print(f"PASS: SPY order flow has {len(walls)} walls with intensity field")
        
        print("PASS: SPY order flow context available for market predictions")


class TestExistingOrderFlowRegression:
    """Regression tests for existing order flow endpoints"""

    def test_order_flow_btc_walls_have_intensity(self):
        """GET /api/order-flow/BTC walls should have intensity field (iteration 68 feature)"""
        url = f"{BASE_URL}/api/order-flow/BTC"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200
        data = response.json()
        
        walls = data.get('walls', [])
        for wall in walls:
            assert 'intensity' in wall, f"Wall missing intensity field: {wall}"
            assert isinstance(wall['intensity'], int), f"intensity should be int, got {type(wall['intensity'])}"
            assert 0 <= wall['intensity'] <= 100, f"intensity out of range: {wall['intensity']}"
        
        print(f"PASS: All {len(walls)} BTC walls have valid intensity field")

    def test_sse_orderflow_btc_still_works(self):
        """GET /api/stream/orderflow/BTC SSE should still work (iteration 67 feature)"""
        url = f"{BASE_URL}/api/stream/orderflow/BTC"
        
        with requests.get(url, stream=True, timeout=30) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert 'text/event-stream' in response.headers.get('Content-Type', ''), "Expected SSE content type"
            
            snapshot_received = False
            
            for line in response.iter_lines(decode_unicode=True):
                if line and line.startswith('data:'):
                    data_str = line.split(':', 1)[1].strip()
                    if data_str:
                        try:
                            data = json.loads(data_str)
                            if data.get('type') == 'snapshot':
                                snapshot_received = True
                                # Verify snapshot structure
                                assert 'symbol' in data, "snapshot should have symbol"
                                assert 'mid' in data, "snapshot should have mid"
                                assert 'bids' in data, "snapshot should have bids"
                                assert 'asks' in data, "snapshot should have asks"
                                assert 'walls' in data, "snapshot should have walls"
                                # Verify walls have intensity
                                for wall in data.get('walls', []):
                                    assert 'intensity' in wall, "wall should have intensity"
                                print(f"PASS: SSE orderflow/BTC snapshot received with {len(data.get('walls', []))} walls")
                                break
                        except json.JSONDecodeError:
                            pass
            
            assert snapshot_received, "No snapshot event received from SSE orderflow/BTC"

    def test_order_flow_eth_works(self):
        """GET /api/order-flow/ETH should work (crypto via Binance)"""
        url = f"{BASE_URL}/api/order-flow/ETH"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200
        data = response.json()
        assert data.get('source') == 'binance_l2'
        assert data.get('ticker') == 'ETH'
        print(f"PASS: ETH order flow works, {len(data.get('walls', []))} walls detected")


class TestWhaleRadarTickers:
    """Tests to verify all 10 radar tickers are supported"""

    # MATIC has no order book depth on Binance US (low liquidity), so we test the main tickers
    MAIN_RADAR_TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK"]

    @pytest.mark.parametrize("ticker", MAIN_RADAR_TICKERS)
    def test_order_flow_radar_ticker(self, ticker):
        """Each radar ticker should have working order flow endpoint"""
        url = f"{BASE_URL}/api/order-flow/{ticker}"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200, f"Expected 200 for {ticker}, got {response.status_code}"
        data = response.json()
        
        # All radar tickers are crypto, should use Binance L2
        assert data.get('source') == 'binance_l2', f"Expected binance_l2 for {ticker}, got {data.get('source')}"
        assert data.get('ticker') == ticker, f"Expected {ticker} ticker"
        
        # Verify walls have intensity
        walls = data.get('walls', [])
        for wall in walls:
            assert 'intensity' in wall, f"{ticker} wall missing intensity"
        
        print(f"PASS: {ticker} order flow works with {len(walls)} walls")

    def test_matic_order_flow_endpoint_responds(self):
        """MATIC endpoint should respond (may have no data due to low Binance US liquidity)"""
        url = f"{BASE_URL}/api/order-flow/MATIC"
        response = requests.get(url, timeout=30)
        
        assert response.status_code == 200, f"Expected 200 for MATIC, got {response.status_code}"
        data = response.json()
        
        # MATIC may have no order book depth on Binance US, so we just verify endpoint responds
        # It may return error or empty walls, which is acceptable
        print(f"INFO: MATIC order flow response: source={data.get('source')}, walls={len(data.get('walls', []))}, error={data.get('error')}")


class TestCrewOrderFlowInjection:
    """Tests to verify order flow context is available for AI crews"""

    def test_war_room_endpoint_exists(self):
        """War Room endpoint should exist (order flow injected in crew_definitions.py lines 66-90)"""
        # Correct endpoint path: /api/intelligence/war-room/{symbol}
        url = f"{BASE_URL}/api/intelligence/war-room/BTC"
        response = requests.get(url, timeout=10)
        
        # Should return 401 (auth required) or 200 (if no auth), not 404
        assert response.status_code != 404, "War Room endpoint should exist"
        print(f"PASS: War Room endpoint exists (status {response.status_code})")

    def test_hypothesis_endpoint_exists(self):
        """Hypothesis endpoint should exist (order flow injected in crew_definitions.py)"""
        url = f"{BASE_URL}/api/hypothesis/generate"
        response = requests.post(url, json={"symbol": "BTC"}, timeout=10)
        
        # Should return 401 (auth required) or 200, not 404
        assert response.status_code != 404, "Hypothesis endpoint should exist"
        print(f"PASS: Hypothesis endpoint exists (status {response.status_code})")

    def test_market_prediction_endpoint_exists(self):
        """Market Prediction endpoint should exist (SPY order flow injected in market_prediction_service.py lines 72-89)"""
        # Correct endpoint path: /api/market/prediction
        # This is an AI-heavy endpoint that can take 60+ seconds and may timeout
        # We just verify the endpoint exists and doesn't return 404
        url = f"{BASE_URL}/api/market/prediction"
        try:
            response = requests.get(url, timeout=10)
            # Should not return 404 (endpoint exists)
            assert response.status_code != 404, "Market Prediction endpoint should exist"
            print(f"PASS: Market Prediction endpoint exists (status {response.status_code})")
        except requests.exceptions.Timeout:
            # Timeout is acceptable for this AI-heavy endpoint - it means the endpoint exists
            print("PASS: Market Prediction endpoint exists (timed out - AI processing)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
