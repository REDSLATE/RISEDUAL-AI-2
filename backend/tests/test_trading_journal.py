"""Trading Journal Feature Tests - CRUD, Analytics, Hypothesis Attachment, Free Limit"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
FREE_USER_EMAIL = "freeuser_test@test.com"
FREE_USER_PASSWORD = os.environ.get("FREE_USER_PASSWORD", "Test1234!")


@pytest.fixture(scope="module")
def owner_token():
    """Get owner (Pro) auth token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"Owner login failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def free_user_token():
    """Get free user auth token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": FREE_USER_EMAIL,
        "password": FREE_USER_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip(f"Free user login failed: {response.status_code} - {response.text}")


@pytest.fixture
def owner_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"}


@pytest.fixture
def free_headers(free_user_token):
    return {"Authorization": f"Bearer {free_user_token}", "Content-Type": "application/json"}


class TestJournalAuthentication:
    """Test that journal endpoints require authentication"""
    
    def test_get_trades_requires_auth(self):
        """GET /api/journal/trades requires authentication"""
        response = requests.get(f"{BASE_URL}/api/journal/trades")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: GET /api/journal/trades requires auth")
    
    def test_create_trade_requires_auth(self):
        """POST /api/journal/trade requires authentication"""
        response = requests.post(f"{BASE_URL}/api/journal/trade", json={
            "ticker": "TEST", "side": "buy", "entry_price": 100, "quantity": 10, "entry_date": "2026-01-15"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: POST /api/journal/trade requires auth")
    
    def test_analytics_requires_auth(self):
        """GET /api/journal/analytics requires authentication"""
        response = requests.get(f"{BASE_URL}/api/journal/analytics")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: GET /api/journal/analytics requires auth")


class TestJournalGetTrades:
    """Test GET /api/journal/trades endpoint"""
    
    def test_owner_can_get_trades(self, owner_headers):
        """Owner can retrieve their trades"""
        response = requests.get(f"{BASE_URL}/api/journal/trades", headers=owner_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "trades" in data, "Response should have 'trades' key"
        assert "total" in data, "Response should have 'total' key"
        assert "limit" in data, "Response should have 'limit' key"
        assert isinstance(data["trades"], list), "trades should be a list"
        # Owner is Pro, so limit should be -1 (unlimited)
        assert data["limit"] == -1, f"Pro user should have limit=-1, got {data['limit']}"
        print(f"PASSED: Owner has {data['total']} trades, limit={data['limit']}")
    
    def test_free_user_can_get_trades(self, free_headers):
        """Free user can retrieve their trades with limit info"""
        response = requests.get(f"{BASE_URL}/api/journal/trades", headers=free_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "trades" in data
        assert "total" in data
        assert "limit" in data
        # Free user should have limit=5
        assert data["limit"] == 5, f"Free user should have limit=5, got {data['limit']}"
        print(f"PASSED: Free user has {data['total']} trades, limit={data['limit']}")


class TestJournalCreateTrade:
    """Test POST /api/journal/trade endpoint"""
    
    def test_owner_can_create_trade(self, owner_headers):
        """Owner (Pro) can create a new trade"""
        trade_data = {
            "ticker": "TEST_NVDA",
            "side": "buy",
            "entry_price": 850.50,
            "quantity": 5,
            "entry_date": "2026-01-15",
            "notes": "Test trade for pytest"
        }
        response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "id" in data, "Response should have 'id'"
        assert data["ticker"] == "TEST_NVDA", f"Ticker should be TEST_NVDA, got {data['ticker']}"
        assert data["side"] == "buy", f"Side should be buy, got {data['side']}"
        assert data["entry_price"] == 850.50, f"Entry price should be 850.50, got {data['entry_price']}"
        assert data["quantity"] == 5, f"Quantity should be 5, got {data['quantity']}"
        assert data["status"] == "open", f"Status should be open, got {data['status']}"
        assert data["pnl"] == 0, f"Open trade should have pnl=0, got {data['pnl']}"
        print(f"PASSED: Created trade {data['id']} for TEST_NVDA")
        return data["id"]
    
    def test_create_trade_with_exit_price_calculates_pnl(self, owner_headers):
        """Creating a closed trade (with exit price) calculates P&L correctly"""
        trade_data = {
            "ticker": "TEST_GOOG",
            "side": "buy",
            "entry_price": 100.00,
            "exit_price": 120.00,
            "quantity": 10,
            "entry_date": "2026-01-10",
            "exit_date": "2026-01-15",
            "notes": "Closed trade test"
        }
        response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["status"] == "closed", f"Trade with exit price should be closed, got {data['status']}"
        # P&L for buy: (exit - entry) * qty = (120 - 100) * 10 = 200
        assert data["pnl"] == 200.00, f"P&L should be 200.00, got {data['pnl']}"
        # P&L percent: ((120 - 100) / 100) * 100 = 20%
        assert data["pnl_percent"] == 20.0, f"P&L percent should be 20.0, got {data['pnl_percent']}"
        print(f"PASSED: Closed trade P&L calculated correctly: ${data['pnl']} ({data['pnl_percent']}%)")
        return data["id"]
    
    def test_sell_side_pnl_calculation(self, owner_headers):
        """Sell (short) trade P&L calculated correctly: (entry - exit) * qty"""
        trade_data = {
            "ticker": "TEST_SHORT",
            "side": "sell",
            "entry_price": 150.00,
            "exit_price": 130.00,
            "quantity": 10,
            "entry_date": "2026-01-10",
            "exit_date": "2026-01-15",
            "notes": "Short trade test"
        }
        response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        # P&L for sell: (entry - exit) * qty = (150 - 130) * 10 = 200
        assert data["pnl"] == 200.00, f"Short P&L should be 200.00, got {data['pnl']}"
        print(f"PASSED: Short trade P&L calculated correctly: ${data['pnl']}")
        return data["id"]
    
    def test_free_user_at_limit_gets_403(self, free_headers):
        """Free user at 5 trade limit gets 403 when trying to create more"""
        # First check current count
        response = requests.get(f"{BASE_URL}/api/journal/trades", headers=free_headers)
        data = response.json()
        current_count = data["total"]
        print(f"Free user has {current_count} trades")
        
        if current_count >= 5:
            # Try to create a trade - should get 403
            trade_data = {
                "ticker": "TEST_BLOCKED",
                "side": "buy",
                "entry_price": 100,
                "quantity": 1,
                "entry_date": "2026-01-15"
            }
            response = requests.post(f"{BASE_URL}/api/journal/trade", headers=free_headers, json=trade_data)
            assert response.status_code == 403, f"Expected 403 at limit, got {response.status_code}: {response.text}"
            data = response.json()
            assert "Free accounts limited to 5 trades" in data.get("detail", ""), f"Expected limit message, got {data}"
            print("PASSED: Free user at limit gets 403")
        else:
            pytest.skip(f"Free user only has {current_count} trades, not at limit")


class TestJournalUpdateTrade:
    """Test PUT /api/journal/trade/{id} endpoint"""
    
    def test_close_open_trade(self, owner_headers):
        """Can close an open trade by adding exit price"""
        # First create an open trade
        trade_data = {
            "ticker": "TEST_CLOSE",
            "side": "buy",
            "entry_price": 200.00,
            "quantity": 5,
            "entry_date": "2026-01-10"
        }
        create_response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        assert create_response.status_code == 200
        trade_id = create_response.json()["id"]
        
        # Now close it
        update_data = {
            "exit_price": 220.00,
            "exit_date": "2026-01-15"
        }
        response = requests.put(f"{BASE_URL}/api/journal/trade/{trade_id}", headers=owner_headers, json=update_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["status"] == "closed", f"Trade should be closed, got {data['status']}"
        assert data["exit_price"] == 220.00, f"Exit price should be 220.00, got {data['exit_price']}"
        # P&L: (220 - 200) * 5 = 100
        assert data["pnl"] == 100.00, f"P&L should be 100.00, got {data['pnl']}"
        print(f"PASSED: Closed trade {trade_id}, P&L=${data['pnl']}")
    
    def test_update_notes(self, owner_headers):
        """Can update trade notes"""
        # Create a trade
        trade_data = {
            "ticker": "TEST_NOTES",
            "side": "buy",
            "entry_price": 100.00,
            "quantity": 1,
            "entry_date": "2026-01-15"
        }
        create_response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        trade_id = create_response.json()["id"]
        
        # Update notes
        update_data = {"notes": "Updated notes for testing"}
        response = requests.put(f"{BASE_URL}/api/journal/trade/{trade_id}", headers=owner_headers, json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["notes"] == "Updated notes for testing", f"Notes should be updated, got {data['notes']}"
        print("PASSED: Updated trade notes")
    
    def test_update_nonexistent_trade_returns_404(self, owner_headers):
        """Updating non-existent trade returns 404"""
        response = requests.put(f"{BASE_URL}/api/journal/trade/000000000000000000000000", headers=owner_headers, json={"notes": "test"})
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASSED: Non-existent trade returns 404")


class TestJournalDeleteTrade:
    """Test DELETE /api/journal/trade/{id} endpoint"""
    
    def test_delete_trade(self, owner_headers):
        """Can delete a trade"""
        # Create a trade to delete
        trade_data = {
            "ticker": "TEST_DELETE",
            "side": "buy",
            "entry_price": 100.00,
            "quantity": 1,
            "entry_date": "2026-01-15"
        }
        create_response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        trade_id = create_response.json()["id"]
        
        # Delete it
        response = requests.delete(f"{BASE_URL}/api/journal/trade/{trade_id}", headers=owner_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data.get("message") == "Trade deleted", f"Expected delete message, got {data}"
        
        # Verify it's gone
        get_response = requests.get(f"{BASE_URL}/api/journal/trades", headers=owner_headers)
        trades = get_response.json()["trades"]
        trade_ids = [t["id"] for t in trades]
        assert trade_id not in trade_ids, "Deleted trade should not appear in list"
        print(f"PASSED: Deleted trade {trade_id}")
    
    def test_delete_nonexistent_trade_returns_404(self, owner_headers):
        """Deleting non-existent trade returns 404"""
        response = requests.delete(f"{BASE_URL}/api/journal/trade/000000000000000000000000", headers=owner_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASSED: Delete non-existent trade returns 404")


class TestJournalAttachHypothesis:
    """Test POST /api/journal/trade/{id}/attach-hypothesis endpoint"""
    
    def test_attach_hypothesis_requires_auth(self):
        """Attach hypothesis requires authentication"""
        response = requests.post(f"{BASE_URL}/api/journal/trade/000000000000000000000000/attach-hypothesis")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASSED: Attach hypothesis requires auth")
    
    def test_attach_hypothesis_to_nonexistent_trade(self, owner_headers):
        """Attaching hypothesis to non-existent trade returns 404"""
        response = requests.post(f"{BASE_URL}/api/journal/trade/000000000000000000000000/attach-hypothesis", headers=owner_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASSED: Attach to non-existent trade returns 404")
    
    def test_attach_hypothesis_no_hypothesis_found(self, owner_headers):
        """Attaching hypothesis when no hypothesis exists for ticker returns 404"""
        # Create a trade with a ticker that likely has no hypothesis
        trade_data = {
            "ticker": "XYZNONEXISTENT",
            "side": "buy",
            "entry_price": 100.00,
            "quantity": 1,
            "entry_date": "2026-01-15"
        }
        create_response = requests.post(f"{BASE_URL}/api/journal/trade", headers=owner_headers, json=trade_data)
        trade_id = create_response.json()["id"]
        
        # Try to attach hypothesis
        response = requests.post(f"{BASE_URL}/api/journal/trade/{trade_id}/attach-hypothesis", headers=owner_headers)
        # Should return 404 because no hypothesis exists for this ticker
        assert response.status_code == 404, f"Expected 404 (no hypothesis), got {response.status_code}: {response.text}"
        print("PASSED: No hypothesis found returns 404")


class TestJournalAnalytics:
    """Test GET /api/journal/analytics endpoint"""
    
    def test_analytics_returns_correct_structure(self, owner_headers):
        """Analytics endpoint returns correct data structure"""
        response = requests.get(f"{BASE_URL}/api/journal/analytics", headers=owner_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Check required fields
        required_fields = [
            "total_trades", "open_trades", "closed_trades", "total_pnl",
            "win_rate", "avg_gain", "avg_loss", "best_trade", "worst_trade",
            "by_ticker", "pnl_timeline"
        ]
        for field in required_fields:
            assert field in data, f"Analytics should have '{field}' field"
        
        print("PASSED: Analytics structure correct")
        print(f"  - Total trades: {data['total_trades']}")
        print(f"  - Open: {data['open_trades']}, Closed: {data['closed_trades']}")
        print(f"  - Total P&L: ${data['total_pnl']}")
        print(f"  - Win rate: {data['win_rate']}%")
    
    def test_analytics_pnl_timeline_structure(self, owner_headers):
        """Analytics pnl_timeline has correct structure"""
        response = requests.get(f"{BASE_URL}/api/journal/analytics", headers=owner_headers)
        data = response.json()
        
        if data["pnl_timeline"]:
            timeline_entry = data["pnl_timeline"][0]
            assert "date" in timeline_entry, "Timeline entry should have 'date'"
            assert "pnl" in timeline_entry, "Timeline entry should have 'pnl'"
            assert "cumulative" in timeline_entry, "Timeline entry should have 'cumulative'"
            assert "ticker" in timeline_entry, "Timeline entry should have 'ticker'"
            print(f"PASSED: P&L timeline structure correct ({len(data['pnl_timeline'])} entries)")
        else:
            print("PASSED: P&L timeline empty (no closed trades)")
    
    def test_analytics_by_ticker_structure(self, owner_headers):
        """Analytics by_ticker has correct structure"""
        response = requests.get(f"{BASE_URL}/api/journal/analytics", headers=owner_headers)
        data = response.json()
        
        if data["by_ticker"]:
            ticker, ticker_data = list(data["by_ticker"].items())[0]
            assert "pnl" in ticker_data, "Ticker data should have 'pnl'"
            assert "trades" in ticker_data, "Ticker data should have 'trades'"
            assert "wins" in ticker_data, "Ticker data should have 'wins'"
            print(f"PASSED: By-ticker structure correct ({len(data['by_ticker'])} tickers)")
        else:
            print("PASSED: By-ticker empty (no closed trades)")
    
    def test_analytics_best_worst_trade(self, owner_headers):
        """Analytics best/worst trade have correct structure"""
        response = requests.get(f"{BASE_URL}/api/journal/analytics", headers=owner_headers)
        data = response.json()
        
        if data["best_trade"]:
            assert "ticker" in data["best_trade"], "Best trade should have 'ticker'"
            assert "pnl" in data["best_trade"], "Best trade should have 'pnl'"
            assert "pnl_percent" in data["best_trade"], "Best trade should have 'pnl_percent'"
            print(f"PASSED: Best trade: {data['best_trade']['ticker']} +${data['best_trade']['pnl']}")
        
        if data["worst_trade"]:
            assert "ticker" in data["worst_trade"], "Worst trade should have 'ticker'"
            assert "pnl" in data["worst_trade"], "Worst trade should have 'pnl'"
            print(f"PASSED: Worst trade: {data['worst_trade']['ticker']} ${data['worst_trade']['pnl']}")


class TestJournalCleanup:
    """Cleanup test trades created during testing"""
    
    def test_cleanup_test_trades(self, owner_headers):
        """Delete all TEST_ prefixed trades"""
        response = requests.get(f"{BASE_URL}/api/journal/trades?limit=100", headers=owner_headers)
        trades = response.json()["trades"]
        
        deleted_count = 0
        for trade in trades:
            if trade["ticker"].startswith("TEST_"):
                del_response = requests.delete(f"{BASE_URL}/api/journal/trade/{trade['id']}", headers=owner_headers)
                if del_response.status_code == 200:
                    deleted_count += 1
        
        print(f"PASSED: Cleaned up {deleted_count} test trades")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
