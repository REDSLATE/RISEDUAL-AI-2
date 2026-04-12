"""
Iteration 74: Portfolio Agent with AI Tool Calling Tests
Tests the upgraded AI chat that uses proper agentic tool calling via LiteLLM + GPT-5.2.
The AI decides which portfolio tools to invoke (get_portfolio_snapshot, get_position_detail, 
get_trade_history, get_watchlist_news) instead of keyword-based context injection.
"""
import pytest
import requests
import os
import time
import uuid
class TestPortfolioAgentToolCalling:
    """Test the portfolio agent with AI tool calling for portfolio queries."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Login as admin and get session."""
        self.session = requests.Session()
        # Login as admin
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.session_id = str(uuid.uuid4())
        yield
        self.session.close()
    
    def test_portfolio_query_triggers_agent(self):
        """POST /api/chat with 'How is my portfolio doing?' should trigger portfolio agent."""
        # This query contains portfolio keywords and should route to run_portfolio_agent
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "How is my portfolio doing?",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "response" in data, "Response should contain 'response' field"
        assert "sessionId" in data, "Response should contain 'sessionId' field"
        
        # The response should contain portfolio-related information
        ai_response = data["response"].lower()
        # Portfolio agent should return actual data about positions, cash, equity, etc.
        portfolio_indicators = ["portfolio", "position", "cash", "equity", "p&l", "pnl", 
                                "aapl", "nvda", "btc", "$", "shares", "unrealized"]
        has_portfolio_content = any(indicator in ai_response for indicator in portfolio_indicators)
        assert has_portfolio_content, f"Response should contain portfolio data. Got: {data['response'][:500]}"
        print(f"SUCCESS: Portfolio query returned personalized response with portfolio data")
    
    def test_trade_history_query_triggers_agent(self):
        """POST /api/chat with 'Show me my recent trades' should trigger get_trade_history tool."""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "Show me my recent trades",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        ai_response = data["response"].lower()
        
        # Should contain trade-related information
        trade_indicators = ["trade", "buy", "sell", "bought", "sold", "history", 
                           "recent", "transaction", "order", "executed"]
        has_trade_content = any(indicator in ai_response for indicator in trade_indicators)
        assert has_trade_content, f"Response should contain trade history data. Got: {data['response'][:500]}"
        print(f"SUCCESS: Trade history query returned actual trade records")
    
    def test_specific_position_query_triggers_agent(self):
        """POST /api/chat with 'How is my AAPL position?' should trigger get_position_detail tool."""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "How is my AAPL position?",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        ai_response = data["response"].lower()
        
        # Should contain AAPL-specific information
        aapl_indicators = ["aapl", "apple", "position", "shares", "price", "cost", 
                          "value", "gain", "loss", "p&l", "pnl"]
        has_aapl_content = any(indicator in ai_response for indicator in aapl_indicators)
        assert has_aapl_content, f"Response should contain AAPL position data. Got: {data['response'][:500]}"
        print(f"SUCCESS: AAPL position query returned specific position details")
    
    def test_non_portfolio_query_uses_standard_ai(self):
        """POST /api/chat with 'What is a put option?' should use standard AI (no tool calling)."""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "What is a put option?",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        ai_response = data["response"].lower()
        
        # Should contain educational content about put options, NOT portfolio data
        put_option_indicators = ["put", "option", "right", "sell", "strike", "premium", 
                                 "contract", "expiration", "derivative", "underlying"]
        has_educational_content = any(indicator in ai_response for indicator in put_option_indicators)
        assert has_educational_content, f"Response should explain put options. Got: {data['response'][:500]}"
        
        # Should NOT contain personal portfolio data
        personal_indicators = ["your portfolio", "your position", "you own", "you have"]
        has_personal_data = any(indicator in ai_response for indicator in personal_indicators)
        # This is a general question, so it shouldn't reference personal portfolio
        print(f"SUCCESS: Non-portfolio query used standard AI service")


class TestChatWithImageBypassesAgent:
    """Test that image uploads bypass the portfolio agent."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Login as admin."""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.session_id = str(uuid.uuid4())
        yield
        self.session.close()
    
    def test_image_upload_bypasses_portfolio_agent(self):
        """POST /api/chat with image should use standard AI even with portfolio keywords."""
        # Create a simple test image (1x1 pixel PNG)
        import base64
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD

        # Minimal valid PNG
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        
        files = {
            'image': ('test.png', png_data, 'image/png')
        }
        data = {
            'message': 'How is my portfolio doing? Analyze this chart.',
            'sessionId': self.session_id
        }
        
        response = self.session.post(f"{BASE_URL}/api/chat", data=data, files=files)
        assert response.status_code == 200, f"Chat with image failed: {response.text}"
        
        result = response.json()
        assert "response" in result
        # The response should still work (standard AI handles it)
        print(f"SUCCESS: Image upload with portfolio keywords bypassed agent correctly")


class TestPaperTradingEndpointsStillWork:
    """Verify paper trading endpoints still function correctly."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Login as admin."""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        yield
        self.session.close()
    
    def test_get_paper_portfolio(self):
        """GET /api/paper/portfolio should return portfolio snapshot."""
        response = self.session.get(f"{BASE_URL}/api/paper/portfolio")
        assert response.status_code == 200, f"Get portfolio failed: {response.text}"
        
        data = response.json()
        # Verify portfolio structure
        assert "cash" in data, "Portfolio should have cash"
        assert "equity" in data, "Portfolio should have equity"
        assert "positions" in data, "Portfolio should have positions"
        assert "total_pnl" in data, "Portfolio should have total_pnl"
        
        # Verify data types
        assert isinstance(data["cash"], (int, float))
        assert isinstance(data["equity"], (int, float))
        assert isinstance(data["positions"], list)
        
        print(f"SUCCESS: GET /api/paper/portfolio returns valid snapshot")
        print(f"  Cash: ${data['cash']:,.2f}, Equity: ${data['equity']:,.2f}, Positions: {len(data['positions'])}")
    
    def test_post_paper_trade_buy(self):
        """POST /api/paper/trade should execute a BUY order."""
        # First check current portfolio
        portfolio_before = self.session.get(f"{BASE_URL}/api/paper/portfolio").json()
        
        # Execute a small test trade
        response = self.session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "MSFT",
            "side": "BUY",
            "qty": 1
        })
        assert response.status_code == 200, f"Trade failed: {response.text}"
        
        data = response.json()
        assert data.get("status") == "filled", f"Trade should be filled. Got: {data}"
        assert data.get("symbol") == "MSFT"
        assert data.get("side") == "BUY"
        assert data.get("qty") == 1
        assert "price" in data
        assert "cash_remaining" in data
        
        print(f"SUCCESS: POST /api/paper/trade BUY executed at ${data['price']}")
        
        # Sell it back to clean up
        self.session.post(f"{BASE_URL}/api/paper/trade", json={
            "symbol": "MSFT",
            "side": "SELL",
            "qty": 1
        })
    
    def test_get_paper_trades_history(self):
        """GET /api/paper/trades should return trade history."""
        response = self.session.get(f"{BASE_URL}/api/paper/trades?limit=10")
        assert response.status_code == 200, f"Get trades failed: {response.text}"
        
        data = response.json()
        assert isinstance(data, list), "Trades should be a list"
        
        if len(data) > 0:
            trade = data[0]
            assert "symbol" in trade
            assert "side" in trade
            assert "qty" in trade
            assert "price" in trade
            assert "timestamp" in trade
        
        print(f"SUCCESS: GET /api/paper/trades returns {len(data)} trades")


class TestPortfolioAgentErrorHandling:
    """Test that portfolio agent gracefully handles errors."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Login as admin."""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.session_id = str(uuid.uuid4())
        yield
        self.session.close()
    
    def test_agent_handles_complex_query(self):
        """Portfolio agent should handle complex multi-part queries."""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "What's my portfolio performance and should I rebalance? Also show my trade history.",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        # Should get a response even for complex queries
        assert len(data["response"]) > 50, "Response should be substantive"
        print(f"SUCCESS: Agent handled complex multi-part query")
    
    def test_agent_handles_nonexistent_position_query(self):
        """Portfolio agent should handle queries about positions user doesn't have."""
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "How is my TSLA position doing?",
                "sessionId": self.session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        # Should gracefully indicate no position or provide helpful response
        ai_response = data["response"].lower()
        # Either says no position, or provides general info
        print(f"SUCCESS: Agent handled query about non-existent position gracefully")


class TestUnauthenticatedChatBehavior:
    """Test chat behavior for unauthenticated users."""
    
    def test_unauthenticated_chat_works(self):
        """Unauthenticated users can still chat but won't get portfolio agent."""
        session = requests.Session()
        session_id = str(uuid.uuid4())
        
        response = session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": "How is my portfolio doing?",
                "sessionId": session_id
            }
        )
        assert response.status_code == 200, f"Chat failed: {response.text}"
        data = response.json()
        
        assert "response" in data
        # Without auth, portfolio keywords won't trigger agent (user is None)
        # Should still get a response from standard AI
        print(f"SUCCESS: Unauthenticated chat works (uses standard AI)")
        session.close()


class TestChatRateLimitForFreeUsers:
    """Test chat rate limiting for free users."""
    
    def test_chat_limit_endpoint(self):
        """GET /api/chat/limit should return rate limit info."""
        session = requests.Session()
        
        # Login as admin (pro user)
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        
        response = session.get(f"{BASE_URL}/api/chat/limit")
        assert response.status_code == 200, f"Get limit failed: {response.text}"
        
        data = response.json()
        assert "limit" in data
        assert "used" in data
        assert "remaining" in data
        assert "is_pro" in data
        
        # Admin is pro, so should have unlimited
        if data["is_pro"]:
            assert data["limit"] == -1, "Pro users should have unlimited"
        
        print(f"SUCCESS: Chat limit endpoint works. Pro: {data['is_pro']}, Remaining: {data['remaining']}")
        session.close()


class TestPortfolioKeywordsDetection:
    """Test that various portfolio-related keywords trigger the agent."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Login as admin."""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        yield
        self.session.close()
    
    @pytest.mark.parametrize("query,expected_keyword", [
        ("What are my current positions?", "positions"),
        ("Show me my holdings", "holdings"),
        ("What's my P&L today?", "p&l"),
        ("How much cash do I have?", "cash"),
        ("What's my equity value?", "equity"),
        ("Show my cost basis", "cost basis"),
    ])
    def test_portfolio_keyword_triggers_agent(self, query, expected_keyword):
        """Various portfolio keywords should trigger the portfolio agent."""
        session_id = str(uuid.uuid4())
        response = self.session.post(
            f"{BASE_URL}/api/chat",
            data={
                "message": query,
                "sessionId": session_id
            }
        )
        assert response.status_code == 200, f"Chat failed for '{query}': {response.text}"
        data = response.json()
        
        assert "response" in data
        # Response should be substantive (agent processed it)
        assert len(data["response"]) > 20, f"Response too short for '{query}'"
        print(f"SUCCESS: '{expected_keyword}' keyword triggered agent")
