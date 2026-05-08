"""
Iteration 68: 0-100 Intensity Scale & Whale Wall Alerts Testing

Tests:
1. GET /api/order-flow/{ticker} returns walls with 'intensity' field (0-100 integer)
2. SSE /api/stream/orderflow/{ticker} returns walls with 'intensity' field in snapshot events
3. Intensity score follows formula: min(int(((ratio-1)/9)*100), 100)
4. Push notification endpoints work (subscribe, test)
5. Volume display shows K/M suffixed values
"""

import pytest
import requests
import os
import json
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD
# Test credentials
def ratio_to_intensity(ratio: float) -> int:
    """Expected intensity calculation formula."""
    return min(int(((ratio - 1) / 9.0) * 100), 100) if ratio >= 1 else 0


class TestIntensityFormula:
    """Test the intensity calculation formula."""
    
    def test_ratio_1x_equals_intensity_0(self):
        """ratio 1x should give intensity 0"""
        assert ratio_to_intensity(1.0) == 0
    
    def test_ratio_3x_equals_intensity_22(self):
        """ratio 3x should give intensity ~22"""
        intensity = ratio_to_intensity(3.0)
        assert 20 <= intensity <= 25, f"Expected ~22, got {intensity}"
    
    def test_ratio_5x_equals_intensity_44(self):
        """ratio 5x should give intensity ~44"""
        intensity = ratio_to_intensity(5.0)
        assert 42 <= intensity <= 46, f"Expected ~44, got {intensity}"
    
    def test_ratio_10x_equals_intensity_100(self):
        """ratio 10x should give intensity 100"""
        assert ratio_to_intensity(10.0) == 100
    
    def test_ratio_20x_equals_intensity_100(self):
        """ratio 20x+ should cap at intensity 100"""
        assert ratio_to_intensity(20.0) == 100
    
    def test_ratio_8_7x_equals_intensity_85(self):
        """ratio ~8.7x should give intensity ~85 (whale threshold)"""
        intensity = ratio_to_intensity(8.65)
        assert 83 <= intensity <= 87, f"Expected ~85, got {intensity}"


class TestOrderFlowIntensityBTC:
    """Test GET /api/order-flow/BTC returns walls with intensity field."""
    
    def test_btc_returns_intensity_field(self):
        """BTC order flow should return walls with intensity field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "binance_l2", f"Expected binance_l2, got {data.get('source')}"
        
        walls = data.get("walls", [])
        assert len(walls) > 0, "Expected at least one wall"
        
        for wall in walls:
            assert "intensity" in wall, f"Wall missing intensity field: {wall}"
            intensity = wall["intensity"]
            assert isinstance(intensity, int), f"Intensity should be int, got {type(intensity)}"
            assert 0 <= intensity <= 100, f"Intensity should be 0-100, got {intensity}"
    
    def test_btc_intensity_matches_ratio(self):
        """BTC wall intensity should match the formula based on ratio"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        for wall in walls[:5]:  # Check first 5 walls
            ratio = wall.get("ratio", 0)
            intensity = wall.get("intensity", 0)
            expected = ratio_to_intensity(ratio)
            # Allow small tolerance due to rounding
            assert abs(intensity - expected) <= 2, f"Intensity {intensity} doesn't match expected {expected} for ratio {ratio}"


class TestOrderFlowIntensityAAPL:
    """Test GET /api/order-flow/AAPL returns walls with intensity field."""
    
    def test_aapl_returns_intensity_field(self):
        """AAPL order flow should return walls with intensity field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "yfinance_profile", f"Expected yfinance_profile, got {data.get('source')}"
        
        walls = data.get("walls", [])
        # AAPL may have fewer walls, but if any exist, they should have intensity
        for wall in walls:
            assert "intensity" in wall, f"Wall missing intensity field: {wall}"
            intensity = wall["intensity"]
            assert isinstance(intensity, int), f"Intensity should be int, got {type(intensity)}"
            assert 0 <= intensity <= 100, f"Intensity should be 0-100, got {intensity}"


class TestOrderFlowIntensityETH:
    """Test GET /api/order-flow/ETH returns walls with intensity field."""
    
    def test_eth_returns_intensity_field(self):
        """ETH order flow should return walls with intensity field"""
        response = requests.get(f"{BASE_URL}/api/order-flow/ETH", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("source") == "binance_l2", f"Expected binance_l2, got {data.get('source')}"
        
        walls = data.get("walls", [])
        for wall in walls:
            assert "intensity" in wall, f"Wall missing intensity field: {wall}"
            intensity = wall["intensity"]
            assert isinstance(intensity, int), f"Intensity should be int, got {type(intensity)}"
            assert 0 <= intensity <= 100, f"Intensity should be 0-100, got {intensity}"


class TestSSEStreamIntensity:
    """Test SSE /api/stream/orderflow/{ticker} returns walls with intensity field."""
    
    def test_sse_btc_snapshot_has_intensity(self):
        """SSE BTC stream snapshot should contain walls with intensity field"""
        import sseclient
        
        url = f"{BASE_URL}/api/stream/orderflow/BTC"
        response = requests.get(url, stream=True, timeout=15)
        assert response.status_code == 200
        
        client = sseclient.SSEClient(response)
        snapshot_found = False
        
        for event in client.events():
            if event.event == "snapshot":
                data = json.loads(event.data)
                walls = data.get("walls", [])
                
                if walls:
                    snapshot_found = True
                    for wall in walls:
                        assert "intensity" in wall, f"SSE wall missing intensity: {wall}"
                        intensity = wall["intensity"]
                        assert isinstance(intensity, int), f"Intensity should be int, got {type(intensity)}"
                        assert 0 <= intensity <= 100, f"Intensity should be 0-100, got {intensity}"
                    break
        
        assert snapshot_found, "No snapshot with walls received from SSE stream"
        response.close()
    
    def test_sse_snapshot_intensity_matches_ratio(self):
        """SSE snapshot wall intensity should match the formula"""
        import sseclient
        
        url = f"{BASE_URL}/api/stream/orderflow/BTC"
        response = requests.get(url, stream=True, timeout=15)
        assert response.status_code == 200
        
        client = sseclient.SSEClient(response)
        
        for event in client.events():
            if event.event == "snapshot":
                data = json.loads(event.data)
                walls = data.get("walls", [])
                
                for wall in walls[:3]:  # Check first 3 walls
                    ratio = wall.get("ratio", 0)
                    intensity = wall.get("intensity", 0)
                    expected = ratio_to_intensity(ratio)
                    assert abs(intensity - expected) <= 2, f"SSE intensity {intensity} doesn't match expected {expected} for ratio {ratio}"
                break
        
        response.close()


class TestWhaleWallThreshold:
    """Test whale wall detection (intensity >= 85)."""
    
    def test_whale_threshold_is_85(self):
        """Whale walls should have intensity >= 85"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        whale_walls = [w for w in walls if w.get("intensity", 0) >= 85]
        # Most BTC walls have very high ratios, so we expect whale walls
        print(f"Found {len(whale_walls)} whale walls (intensity >= 85)")
        
        for whale in whale_walls:
            ratio = whale.get("ratio", 0)
            # Whale threshold ~8.7x ratio
            assert ratio >= 8.0, f"Whale wall should have ratio >= 8.0, got {ratio}"


class TestPushNotificationEndpoints:
    """Test push notification endpoints."""
    
    @pytest.fixture
    def admin_token(self):
        """Get admin auth token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        pytest.skip("Admin login failed")
    
    def test_push_subscribe_requires_auth(self):
        """POST /api/push/subscribe should require authentication"""
        response = requests.post(
            f"{BASE_URL}/api/push/subscribe",
            json={"subscription": {"endpoint": "https://test.example.com", "keys": {"p256dh": "test", "auth": "test"}}},
            timeout=10
        )
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
    
    def test_push_subscribe_with_auth(self, admin_token):
        """POST /api/push/subscribe should work with auth"""
        response = requests.post(
            f"{BASE_URL}/api/push/subscribe",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"subscription": {"endpoint": "https://test.example.com/push", "keys": {"p256dh": "test_key", "auth": "test_auth"}}},
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data or "enabled" in str(data).lower()
    
    def test_push_test_admin_only(self, admin_token):
        """POST /api/push/test should work for admin"""
        response = requests.post(
            f"{BASE_URL}/api/push/test",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        # sent: false is expected since we don't have real browser subscriptions
        assert "sent" in data


class TestVolumeDisplay:
    """Test volume display shows K/M suffixed values."""
    
    def test_btc_volume_values_are_numeric(self):
        """BTC walls should have numeric volume values"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        for wall in walls:
            volume = wall.get("volume", 0)
            assert isinstance(volume, (int, float)), f"Volume should be numeric, got {type(volume)}"
            assert volume > 0, f"Volume should be positive, got {volume}"
    
    def test_aapl_volume_values_are_numeric(self):
        """AAPL walls should have numeric volume values"""
        response = requests.get(f"{BASE_URL}/api/order-flow/AAPL", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        for wall in walls:
            volume = wall.get("volume", 0)
            assert isinstance(volume, (int, float)), f"Volume should be numeric, got {type(volume)}"


class TestOrderFlowStructure:
    """Test order flow response structure."""
    
    def test_btc_response_structure(self):
        """BTC response should have all required fields"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        
        # Required fields
        assert "ticker" in data
        assert "current_price" in data
        assert "source" in data
        assert "walls" in data
        assert "summary" in data
        
        # Binance-specific fields
        assert "spread" in data
        assert "depth_levels" in data
        assert "total_bids" in data
        assert "total_asks" in data
    
    def test_wall_structure(self):
        """Each wall should have required fields including intensity"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        required_fields = ["price", "volume", "ratio", "intensity", "type", "strength", "distance_pct"]
        
        for wall in walls[:5]:
            for field in required_fields:
                assert field in wall, f"Wall missing required field '{field}': {wall}"


class TestIntensityEdgeCases:
    """Test intensity calculation edge cases."""
    
    def test_low_ratio_walls(self):
        """Walls with ratio ~3x should have intensity ~22"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        low_ratio_walls = [w for w in walls if 3.0 <= w.get("ratio", 0) <= 4.0]
        
        for wall in low_ratio_walls[:3]:
            intensity = wall.get("intensity", 0)
            # ratio 3x = intensity 22, ratio 4x = intensity 33
            assert 20 <= intensity <= 40, f"Low ratio wall intensity should be 20-40, got {intensity}"
    
    def test_high_ratio_walls_cap_at_100(self):
        """Walls with ratio >= 10x should have intensity 100"""
        response = requests.get(f"{BASE_URL}/api/order-flow/BTC", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        walls = data.get("walls", [])
        
        high_ratio_walls = [w for w in walls if w.get("ratio", 0) >= 10.0]
        
        for wall in high_ratio_walls:
            intensity = wall.get("intensity", 0)
            assert intensity == 100, f"High ratio wall intensity should be 100, got {intensity}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
