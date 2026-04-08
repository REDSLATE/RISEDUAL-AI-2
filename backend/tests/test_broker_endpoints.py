"""
Broker Integration Tests - RISEDUAL AI Trading Platform
Tests for: broker connection, validation, account info, positions, orders, portfolio sync
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if response.status_code == 200:
        data = response.json()
        return data.get("access_token") or data.get("token")
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Headers with auth token."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


class TestBrokerAuthRequired:
    """Test that broker endpoints require authentication."""
    
    def test_connect_requires_auth(self):
        """POST /api/broker/connect should return 401 without token."""
        response = requests.post(f"{BASE_URL}/api/broker/connect", json={
            "broker_id": "alpaca",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "paper": True
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/connect requires authentication")
    
    def test_connections_requires_auth(self):
        """GET /api/broker/connections should return 401 without token."""
        response = requests.get(f"{BASE_URL}/api/broker/connections")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/connections requires authentication")
    
    def test_disconnect_requires_auth(self):
        """DELETE /api/broker/disconnect/{broker_id} should return 401 without token."""
        response = requests.delete(f"{BASE_URL}/api/broker/disconnect/alpaca")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/disconnect requires authentication")
    
    def test_account_requires_auth(self):
        """GET /api/broker/account/{broker_id} should return 401 without token."""
        response = requests.get(f"{BASE_URL}/api/broker/account/alpaca")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/account requires authentication")
    
    def test_positions_requires_auth(self):
        """GET /api/broker/positions/{broker_id} should return 401 without token."""
        response = requests.get(f"{BASE_URL}/api/broker/positions/alpaca")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/positions requires authentication")
    
    def test_orders_requires_auth(self):
        """GET /api/broker/orders/{broker_id} should return 401 without token."""
        response = requests.get(f"{BASE_URL}/api/broker/orders/alpaca")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/orders requires authentication")
    
    def test_place_order_requires_auth(self):
        """POST /api/broker/order/{broker_id} should return 401 without token."""
        response = requests.post(f"{BASE_URL}/api/broker/order/alpaca", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "buy",
            "order_type": "market"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/order requires authentication")
    
    def test_cancel_order_requires_auth(self):
        """DELETE /api/broker/order/{broker_id}/{order_id} should return 401 without token."""
        response = requests.delete(f"{BASE_URL}/api/broker/order/alpaca/test-order-id")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/order cancel requires authentication")
    
    def test_portfolio_sync_requires_auth(self):
        """GET /api/broker/portfolio-sync/{broker_id} should return 401 without token."""
        response = requests.get(f"{BASE_URL}/api/broker/portfolio-sync/alpaca")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: /api/broker/portfolio-sync requires authentication")


class TestBrokerInputValidation:
    """Test input validation for broker endpoints."""
    
    def test_connect_unsupported_broker(self, auth_headers):
        """POST /api/broker/connect should return 400 for unsupported broker."""
        response = requests.post(f"{BASE_URL}/api/broker/connect", 
            headers=auth_headers,
            json={
                "broker_id": "unsupported_broker",
                "api_key": "test_key",
                "api_secret": "test_secret",
                "paper": True
            })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        assert "unsupported" in data["detail"].lower() or "supported" in data["detail"].lower()
        print(f"PASS: Unsupported broker returns 400 with message: {data['detail']}")
    
    def test_connect_invalid_alpaca_keys(self, auth_headers):
        """POST /api/broker/connect should return 400 for invalid Alpaca API keys."""
        response = requests.post(f"{BASE_URL}/api/broker/connect",
            headers=auth_headers,
            json={
                "broker_id": "alpaca",
                "api_key": "FAKE_API_KEY_12345",
                "api_secret": "FAKE_API_SECRET_67890",
                "paper": True
            })
        # Should return 400 because Alpaca will reject fake credentials
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"PASS: Invalid Alpaca keys return 400 with message: {data['detail']}")
    
    def test_connect_missing_fields(self, auth_headers):
        """POST /api/broker/connect should return 422 for missing required fields."""
        response = requests.post(f"{BASE_URL}/api/broker/connect",
            headers=auth_headers,
            json={
                "broker_id": "alpaca"
                # Missing api_key and api_secret
            })
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        print("PASS: Missing fields return 422 validation error")


class TestBrokerConnectionsEndpoint:
    """Test GET /api/broker/connections endpoint."""
    
    def test_list_connections_returns_list(self, auth_headers):
        """GET /api/broker/connections should return a list of connections."""
        response = requests.get(f"{BASE_URL}/api/broker/connections", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "connections" in data
        assert isinstance(data["connections"], list)
        print(f"PASS: /api/broker/connections returns list with {len(data['connections'])} connections")
    
    def test_connections_excludes_encrypted_keys(self, auth_headers):
        """GET /api/broker/connections should NOT return encrypted keys."""
        response = requests.get(f"{BASE_URL}/api/broker/connections", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        for conn in data.get("connections", []):
            assert "api_key_enc" not in conn, "api_key_enc should not be in response"
            assert "api_secret_enc" not in conn, "api_secret_enc should not be in response"
            assert "api_key" not in conn, "api_key should not be in response"
            assert "api_secret" not in conn, "api_secret should not be in response"
        print("PASS: /api/broker/connections excludes encrypted keys from response")


class TestBrokerNoConnectionErrors:
    """Test error handling when no broker connection exists."""
    
    def test_account_no_connection(self, auth_headers):
        """GET /api/broker/account/{broker_id} should return 404 if no connection."""
        # Use a broker that likely isn't connected
        response = requests.get(f"{BASE_URL}/api/broker/account/schwab", headers=auth_headers)
        # Should be 404 if not connected
        if response.status_code == 404:
            data = response.json()
            assert "detail" in data
            print(f"PASS: No connection returns 404: {data['detail']}")
        else:
            # If connected, should return 200 with account data
            assert response.status_code == 200
            print("PASS: Schwab is connected, returned account data")
    
    def test_positions_no_connection(self, auth_headers):
        """GET /api/broker/positions/{broker_id} should return 404 if no connection."""
        response = requests.get(f"{BASE_URL}/api/broker/positions/ibkr", headers=auth_headers)
        if response.status_code == 404:
            data = response.json()
            assert "detail" in data
            print(f"PASS: No connection returns 404: {data['detail']}")
        else:
            assert response.status_code == 200
            print("PASS: IBKR is connected, returned positions")
    
    def test_orders_no_connection(self, auth_headers):
        """GET /api/broker/orders/{broker_id} should return 404 if no connection."""
        response = requests.get(f"{BASE_URL}/api/broker/orders/ibkr", headers=auth_headers)
        if response.status_code == 404:
            data = response.json()
            assert "detail" in data
            print(f"PASS: No connection returns 404: {data['detail']}")
        else:
            assert response.status_code == 200
            print("PASS: IBKR is connected, returned orders")


class TestBrokerDisconnect:
    """Test broker disconnect functionality."""
    
    def test_disconnect_nonexistent_connection(self, auth_headers):
        """DELETE /api/broker/disconnect/{broker_id} should return 404 for non-existent connection."""
        response = requests.delete(f"{BASE_URL}/api/broker/disconnect/nonexistent_broker", headers=auth_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: Disconnect non-existent broker returns 404")


class TestTradingHealthEndpoint:
    """Test legacy trading health endpoint."""
    
    def test_trading_health(self):
        """GET /api/trading/health should return ok status."""
        response = requests.get(f"{BASE_URL}/api/trading/health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok"
        assert data.get("module") == "trading"
        print(f"PASS: /api/trading/health returns: {data}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
