"""
Iteration 73: Paper Trading Feature Tests
Tests for paper trading backend APIs with live prices from Alpha Vantage + Binance US.
Features: Portfolio management, trade execution, trade history, portfolio reset, AI chat portfolio context.
"""
import pytest
import requests
import os
import time
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

# Test credentials from test_credentials.md
class TestPaperTradingAuth:
    """Test authentication for paper trading endpoints"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a session with auth cookies"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        return s
    
    @pytest.fixture(scope="class")
    def auth_session(self, session):
        """Login and return authenticated session"""
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return session
    
    def test_portfolio_requires_auth(self, session):
        """GET /api/paper/portfolio should require authentication"""
        resp = session.get(f"{BASE_URL}/api/paper/portfolio")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    
    def test_trade_requires_auth(self, session):
        """POST /api/paper/trade should require authentication"""
        resp = session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 1
        })
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    
    def test_trades_requires_auth(self, session):
        """GET /api/paper/trades should require authentication"""
        resp = session.get(f"{BASE_URL}/api/paper/trades")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    
    def test_reset_requires_auth(self, session):
        """POST /api/paper/reset should require authentication"""
        resp = session.post(f"{BASE_URL}/api/paper/reset")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


class TestPaperTradingPortfolio:
    """Test paper trading portfolio endpoints"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_get_portfolio_snapshot(self, auth_session):
        """GET /api/paper/portfolio returns portfolio snapshot with cash, equity, positions, total_pnl"""
        resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        # Verify required fields
        assert "cash" in data, "Portfolio should have 'cash' field"
        assert "equity" in data, "Portfolio should have 'equity' field"
        assert "positions" in data, "Portfolio should have 'positions' field"
        assert "total_pnl" in data, "Portfolio should have 'total_pnl' field"
        assert "total_pnl_pct" in data, "Portfolio should have 'total_pnl_pct' field"
        assert "position_count" in data, "Portfolio should have 'position_count' field"
        assert "starting_cash" in data, "Portfolio should have 'starting_cash' field"
        
        # Verify data types
        assert isinstance(data["cash"], (int, float)), "Cash should be numeric"
        assert isinstance(data["equity"], (int, float)), "Equity should be numeric"
        assert isinstance(data["positions"], list), "Positions should be a list"
        assert isinstance(data["total_pnl"], (int, float)), "Total P&L should be numeric"
        
        # Starting cash should be $100,000
        assert data["starting_cash"] == 100000.0, f"Starting cash should be $100,000, got {data['starting_cash']}"
        
        print(f"Portfolio snapshot: Cash=${data['cash']}, Equity=${data['equity']}, P&L=${data['total_pnl']}, Positions={data['position_count']}")
    
    def test_portfolio_positions_structure(self, auth_session):
        """Verify position structure in portfolio"""
        resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert resp.status_code == 200
        
        data = resp.json()
        positions = data.get("positions", [])
        
        if len(positions) > 0:
            pos = positions[0]
            # Verify position fields
            assert "symbol" in pos, "Position should have 'symbol'"
            assert "qty" in pos, "Position should have 'qty'"
            assert "avg_cost" in pos, "Position should have 'avg_cost'"
            assert "live_price" in pos, "Position should have 'live_price'"
            assert "market_value" in pos, "Position should have 'market_value'"
            assert "unrealized_pnl" in pos, "Position should have 'unrealized_pnl'"
            assert "unrealized_pnl_pct" in pos, "Position should have 'unrealized_pnl_pct'"
            
            print(f"Position: {pos['symbol']} - {pos['qty']} shares @ ${pos['avg_cost']} avg, now ${pos['live_price']}")


class TestPaperTradingTrades:
    """Test paper trading trade execution"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_buy_stock(self, auth_session):
        """POST /api/paper/trade with BUY executes a paper buy"""
        # First get current portfolio to check cash
        portfolio_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert portfolio_resp.status_code == 200
        initial_cash = portfolio_resp.json()["cash"]
        
        # Execute a small buy (1 share of MSFT)
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "MSFT",
            "side": "BUY",
            "qty": 1
        })
        
        # Trade should succeed if we have enough cash
        if trade_resp.status_code == 200:
            data = trade_resp.json()
            assert data["status"] == "filled", f"Trade status should be 'filled', got {data['status']}"
            assert data["symbol"] == "MSFT", f"Symbol should be MSFT, got {data['symbol']}"
            assert data["side"] == "BUY", f"Side should be BUY, got {data['side']}"
            assert data["qty"] == 1, f"Qty should be 1, got {data['qty']}"
            assert "price" in data, "Trade should have 'price'"
            assert "total" in data, "Trade should have 'total'"
            assert "cash_remaining" in data, "Trade should have 'cash_remaining'"
            
            # Verify cash decreased
            assert data["cash_remaining"] < initial_cash, "Cash should decrease after buy"
            print(f"BUY 1 MSFT @ ${data['price']} - Total: ${data['total']}, Cash remaining: ${data['cash_remaining']}")
        elif trade_resp.status_code == 400:
            # Insufficient cash is acceptable
            print(f"Buy failed (expected if low cash): {trade_resp.json()}")
        else:
            pytest.fail(f"Unexpected status code: {trade_resp.status_code} - {trade_resp.text}")
    
    def test_sell_existing_position(self, auth_session):
        """POST /api/paper/trade with SELL executes a paper sell for owned stock"""
        # Get current positions
        portfolio_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert portfolio_resp.status_code == 200
        positions = portfolio_resp.json()["positions"]
        
        if len(positions) == 0:
            pytest.skip("No positions to sell")
        
        # Find a position with qty > 0
        pos_to_sell = None
        for pos in positions:
            if pos["qty"] >= 1:
                pos_to_sell = pos
                break
        
        if pos_to_sell is None:
            pytest.skip("No position with qty >= 1 to sell")
        
        initial_cash = portfolio_resp.json()["cash"]
        
        # Sell 1 share
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": pos_to_sell["symbol"],
            "side": "SELL",
            "qty": 1
        })
        
        assert trade_resp.status_code == 200, f"Sell failed: {trade_resp.text}"
        data = trade_resp.json()
        
        assert data["status"] == "filled", f"Trade status should be 'filled', got {data['status']}"
        assert data["side"] == "SELL", f"Side should be SELL, got {data['side']}"
        assert data["cash_remaining"] > initial_cash, "Cash should increase after sell"
        
        print(f"SELL 1 {pos_to_sell['symbol']} @ ${data['price']} - Cash remaining: ${data['cash_remaining']}")
    
    def test_insufficient_cash_validation(self, auth_session):
        """POST /api/paper/trade validates insufficient cash"""
        # Try to buy 10000 shares of AAPL (should fail due to insufficient cash)
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 10000
        })
        
        assert trade_resp.status_code == 400, f"Expected 400 for insufficient cash, got {trade_resp.status_code}"
        data = trade_resp.json()
        assert "detail" in data, "Error response should have 'detail'"
        assert "insufficient" in data["detail"].lower() or "cash" in data["detail"].lower(), \
            f"Error should mention insufficient cash: {data['detail']}"
        
        print(f"Insufficient cash validation: {data['detail']}")
    
    def test_sell_more_than_owned_validation(self, auth_session):
        """POST /api/paper/trade validates selling more than owned"""
        # Get current positions
        portfolio_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert portfolio_resp.status_code == 200
        positions = portfolio_resp.json()["positions"]
        
        if len(positions) == 0:
            # Try to sell a stock we don't own
            trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
                "symbol": "GOOGL",
                "side": "SELL",
                "qty": 1
            })
            assert trade_resp.status_code == 400, f"Expected 400 for no position, got {trade_resp.status_code}"
            print(f"No position validation: {trade_resp.json()['detail']}")
        else:
            # Try to sell more than we own
            pos = positions[0]
            trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
                "symbol": pos["symbol"],
                "side": "SELL",
                "qty": pos["qty"] + 1000
            })
            assert trade_resp.status_code == 400, f"Expected 400 for selling more than owned, got {trade_resp.status_code}"
            print(f"Sell more than owned validation: {trade_resp.json()['detail']}")
    
    def test_invalid_side_validation(self, auth_session):
        """POST /api/paper/trade validates invalid side"""
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "AAPL",
            "side": "INVALID",
            "qty": 1
        })
        
        # Should return 400 or 422 for validation error
        assert trade_resp.status_code in [400, 422], f"Expected 400/422 for invalid side, got {trade_resp.status_code}"
        print(f"Invalid side validation: {trade_resp.json()}")
    
    def test_invalid_qty_validation(self, auth_session):
        """POST /api/paper/trade validates invalid quantity"""
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "AAPL",
            "side": "BUY",
            "qty": -1
        })
        
        # Should return 400 or 422 for validation error
        assert trade_resp.status_code in [400, 422], f"Expected 400/422 for invalid qty, got {trade_resp.status_code}"
        print(f"Invalid qty validation: {trade_resp.json()}")


class TestPaperTradingHistory:
    """Test paper trading history endpoint"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_get_trade_history(self, auth_session):
        """GET /api/paper/trades returns trade history sorted by timestamp desc"""
        resp = auth_session.get(f"{BASE_URL}/api/paper/trades")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert isinstance(data, list), "Trade history should be a list"
        
        if len(data) > 0:
            trade = data[0]
            # Verify trade record structure
            assert "symbol" in trade, "Trade should have 'symbol'"
            assert "side" in trade, "Trade should have 'side'"
            assert "qty" in trade, "Trade should have 'qty'"
            assert "price" in trade, "Trade should have 'price'"
            assert "total" in trade, "Trade should have 'total'"
            assert "timestamp" in trade, "Trade should have 'timestamp'"
            
            print(f"Trade history: {len(data)} trades, most recent: {trade['side']} {trade['qty']} {trade['symbol']} @ ${trade['price']}")
            
            # Verify sorted by timestamp desc (most recent first)
            if len(data) > 1:
                for i in range(len(data) - 1):
                    assert data[i]["timestamp"] >= data[i+1]["timestamp"], \
                        "Trades should be sorted by timestamp descending"
        else:
            print("No trade history yet")
    
    def test_trade_history_with_limit(self, auth_session):
        """GET /api/paper/trades with limit parameter"""
        resp = auth_session.get(f"{BASE_URL}/api/paper/trades?limit=5")
        assert resp.status_code == 200
        
        data = resp.json()
        assert len(data) <= 5, f"Should return at most 5 trades, got {len(data)}"
        print(f"Trade history with limit=5: {len(data)} trades returned")
    
    def test_trade_history_with_symbol_filter(self, auth_session):
        """GET /api/paper/trades with symbol filter"""
        resp = auth_session.get(f"{BASE_URL}/api/paper/trades?symbol=AAPL")
        assert resp.status_code == 200
        
        data = resp.json()
        for trade in data:
            assert trade["symbol"] == "AAPL", f"All trades should be AAPL, got {trade['symbol']}"
        
        print(f"Trade history for AAPL: {len(data)} trades")


class TestPaperTradingReset:
    """Test paper trading reset endpoint"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_reset_portfolio(self, auth_session):
        """POST /api/paper/reset resets portfolio to $100K with no positions"""
        # Get current portfolio state
        before_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert before_resp.status_code == 200
        before_data = before_resp.json()
        print(f"Before reset: Cash=${before_data['cash']}, Positions={before_data['position_count']}")
        
        # Reset portfolio
        reset_resp = auth_session.post(f"{BASE_URL}/api/paper/reset")
        assert reset_resp.status_code == 200, f"Reset failed: {reset_resp.text}"
        
        reset_data = reset_resp.json()
        assert reset_data["status"] == "reset", f"Status should be 'reset', got {reset_data['status']}"
        assert reset_data["cash"] == 100000.0, f"Cash should be $100,000, got {reset_data['cash']}"
        assert reset_data["positions"] == [], f"Positions should be empty, got {reset_data['positions']}"
        
        # Verify by fetching portfolio again
        after_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert after_resp.status_code == 200
        after_data = after_resp.json()
        
        assert after_data["cash"] == 100000.0, f"Cash should be $100,000 after reset, got {after_data['cash']}"
        assert after_data["position_count"] == 0, f"Position count should be 0 after reset, got {after_data['position_count']}"
        assert after_data["total_pnl"] == 0.0, f"Total P&L should be 0 after reset, got {after_data['total_pnl']}"
        
        print(f"After reset: Cash=${after_data['cash']}, Positions={after_data['position_count']}, P&L=${after_data['total_pnl']}")
        
        # Verify trade history is cleared
        trades_resp = auth_session.get(f"{BASE_URL}/api/paper/trades")
        assert trades_resp.status_code == 200
        trades = trades_resp.json()
        assert len(trades) == 0, f"Trade history should be empty after reset, got {len(trades)} trades"
        print("Trade history cleared after reset")


class TestAIChatPortfolioContext:
    """Test AI chat portfolio context injection"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session (without Content-Type header for form data)"""
        s = requests.Session()
        # Don't set Content-Type header - let requests handle it for form data
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_chat_portfolio_context_injection(self, auth_session):
        """POST /api/chat with portfolio-related message should inject portfolio context"""
        # First, ensure we have some positions by buying stocks
        # Buy some test positions
        auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 5
        })
        auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "NVDA",
            "side": "BUY",
            "qty": 3
        })
        
        # Wait a bit for trades to process
        time.sleep(1)
        
        # Get current portfolio to verify positions
        portfolio_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert portfolio_resp.status_code == 200
        portfolio = portfolio_resp.json()
        print(f"Portfolio before chat: {portfolio['position_count']} positions, Cash=${portfolio['cash']}")
        
        # Send a portfolio-related message to chat using form data
        chat_resp = auth_session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "How is my portfolio doing?",
                "sessionId": "test-paper-trading-73"
            }
        )
        
        # Chat should succeed
        assert chat_resp.status_code == 200, f"Chat failed: {chat_resp.status_code} - {chat_resp.text}"
        
        chat_data = chat_resp.json()
        assert "response" in chat_data, "Chat response should have 'response' field"
        
        response_text = chat_data["response"].lower()
        
        # The AI response should reference portfolio data if context was injected
        # Check for portfolio-related terms in response
        portfolio_terms = ["portfolio", "position", "stock", "share", "cash", "equity", "p&l", "pnl", "profit", "loss"]
        has_portfolio_context = any(term in response_text for term in portfolio_terms)
        
        print(f"Chat response (first 500 chars): {chat_data['response'][:500]}...")
        print(f"Portfolio context detected in response: {has_portfolio_context}")
        
        # Note: We can't guarantee the AI will mention specific tickers, but it should discuss portfolio
        assert has_portfolio_context, "AI response should reference portfolio context"


class TestCryptoTrading:
    """Test paper trading with crypto assets"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Login and return authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        login_resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        return s
    
    def test_buy_crypto(self, auth_session):
        """Test buying crypto (BTC) with live Binance US prices"""
        # Get current portfolio
        portfolio_resp = auth_session.get(f"{BASE_URL}/api/paper/portfolio")
        assert portfolio_resp.status_code == 200
        portfolio_resp.json()["cash"]
        
        # Buy a small amount of BTC (0.001)
        trade_resp = auth_session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "BTC",
            "side": "BUY",
            "qty": 0.001
        })
        
        if trade_resp.status_code == 200:
            data = trade_resp.json()
            assert data["status"] == "filled"
            assert data["symbol"] == "BTC"
            print(f"BUY 0.001 BTC @ ${data['price']} - Total: ${data['total']}")
        elif trade_resp.status_code == 400:
            # Could be insufficient cash or price fetch issue
            print(f"Crypto buy failed: {trade_resp.json()}")
        else:
            pytest.fail(f"Unexpected status: {trade_resp.status_code} - {trade_resp.text}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
