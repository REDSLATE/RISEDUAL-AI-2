"""
Iteration 113: Trading Bots (Phase 4) - Grid Bot, Signal Bot, TradingView Webhook Bot
Tests: CRUD operations, toggle on/off, webhook receiver, signal processing
All bots MUST default to enabled=false (user requirement)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD


class TestTradingBotsPhase4:
    """Trading Bots API tests - Grid, Signal, Webhook bots"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session and authenticate"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.bot_ids = []  # Track created bots for cleanup
        
        # Login to get auth cookies
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.user = login_resp.json()
        print(f"Logged in as: {self.user.get('email')}")
        
        yield
        
        # Cleanup: Delete all test bots
        for bot_id in self.bot_ids:
            try:
                self.session.delete(f"{BASE_URL}/api/bots/{bot_id}")
            except:
                pass
    
    # ── Authentication Tests ──
    
    def test_01_login_returns_200(self):
        """POST /api/auth/login with admin returns 200"""
        resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "email" in data
        assert data["email"] == ADMIN_EMAIL
        print("PASS: Admin login returns 200")
    
    def test_02_bots_without_auth_returns_401(self):
        """POST /api/bots without auth returns 401"""
        # Create new session without auth
        no_auth_session = requests.Session()
        no_auth_session.headers.update({"Content-Type": "application/json"})
        
        resp = no_auth_session.post(f"{BASE_URL}/api/bots", json={
            "type": "grid",
            "name": "Test Bot"
        })
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("PASS: POST /api/bots without auth returns 401")
    
    # ── Grid Bot Tests ──
    
    def test_03_create_grid_bot_defaults_to_off(self):
        """POST /api/bots with type=grid creates bot with enabled=false"""
        resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "grid",
            "name": "TEST_Grid Bot",
            "mode": "paper",
            "config": {
                "symbol": "BTC",
                "upper_price": 100000,
                "lower_price": 90000,
                "grid_levels": 5,
                "qty_per_grid": 0.01
            }
        })
        assert resp.status_code == 200, f"Create grid bot failed: {resp.text}"
        data = resp.json()
        
        # CRITICAL: Bot must default to OFF
        assert data.get("enabled") == False, f"Grid bot should default to enabled=false, got {data.get('enabled')}"
        assert data.get("type") == "grid"
        assert data.get("name") == "TEST_Grid Bot"
        assert "bot_id" in data
        assert data.get("config", {}).get("symbol") == "BTC"
        
        self.bot_ids.append(data["bot_id"])
        print(f"PASS: Grid bot created with enabled=false, bot_id={data['bot_id']}")
    
    # ── Signal Bot Tests ──
    
    def test_04_create_signal_bot_defaults_to_off(self):
        """POST /api/bots with type=signal creates bot with enabled=false"""
        resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "signal",
            "name": "TEST_Signal Bot",
            "mode": "paper",
            "config": {
                "min_confidence": 70,
                "qty": 5,
                "auto_sl_pct": 3,
                "auto_tp_pct": 6,
                "use_smart_order": True
            }
        })
        assert resp.status_code == 200, f"Create signal bot failed: {resp.text}"
        data = resp.json()
        
        # CRITICAL: Bot must default to OFF
        assert data.get("enabled") == False, f"Signal bot should default to enabled=false, got {data.get('enabled')}"
        assert data.get("type") == "signal"
        assert "bot_id" in data
        assert data.get("config", {}).get("min_confidence") == 70
        
        self.bot_ids.append(data["bot_id"])
        print(f"PASS: Signal bot created with enabled=false, bot_id={data['bot_id']}")
    
    # ── Webhook Bot Tests ──
    
    def test_05_create_webhook_bot_with_secret_defaults_to_off(self):
        """POST /api/bots with type=webhook creates bot with webhook_secret and enabled=false"""
        resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "webhook",
            "name": "TEST_Webhook Bot",
            "mode": "paper",
            "config": {
                "default_qty": 10,
                "max_trades_per_day": 10
            }
        })
        assert resp.status_code == 200, f"Create webhook bot failed: {resp.text}"
        data = resp.json()
        
        # CRITICAL: Bot must default to OFF
        assert data.get("enabled") == False, f"Webhook bot should default to enabled=false, got {data.get('enabled')}"
        assert data.get("type") == "webhook"
        assert "bot_id" in data
        assert "webhook_secret" in data, "Webhook bot should have webhook_secret"
        assert len(data.get("webhook_secret", "")) > 10, "Webhook secret should be a secure token"
        
        self.bot_ids.append(data["bot_id"])
        self.webhook_bot_id = data["bot_id"]
        self.webhook_secret = data["webhook_secret"]
        print(f"PASS: Webhook bot created with enabled=false and webhook_secret, bot_id={data['bot_id']}")
    
    # ── List Bots Test ──
    
    def test_06_get_bots_returns_list(self):
        """GET /api/bots returns list of user bots"""
        # First create a bot
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "grid",
            "name": "TEST_List Bot"
        })
        assert create_resp.status_code == 200
        created_bot = create_resp.json()
        self.bot_ids.append(created_bot["bot_id"])
        
        # Get list
        resp = self.session.get(f"{BASE_URL}/api/bots")
        assert resp.status_code == 200, f"Get bots failed: {resp.text}"
        data = resp.json()
        
        assert isinstance(data, list), "Response should be a list"
        # Find our created bot
        found = any(b.get("name") == "TEST_List Bot" for b in data)
        assert found, "Created bot should be in the list"
        print(f"PASS: GET /api/bots returns list with {len(data)} bots")
    
    # ── Toggle Bot Tests ──
    
    def test_07_toggle_bot_on(self):
        """PATCH /api/bots/{bot_id}/toggle with enabled=true activates bot"""
        # Create a bot first
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "grid",
            "name": "TEST_Toggle Bot"
        })
        assert create_resp.status_code == 200
        bot_id = create_resp.json()["bot_id"]
        self.bot_ids.append(bot_id)
        
        # Toggle ON
        resp = self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={
            "enabled": True
        })
        assert resp.status_code == 200, f"Toggle on failed: {resp.text}"
        data = resp.json()
        assert data.get("enabled") == True, f"Bot should be enabled=true after toggle, got {data.get('enabled')}"
        print(f"PASS: Bot toggled ON, enabled={data.get('enabled')}")
    
    def test_08_toggle_bot_off(self):
        """PATCH /api/bots/{bot_id}/toggle with enabled=false deactivates bot"""
        # Create and enable a bot first
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "signal",
            "name": "TEST_Toggle Off Bot"
        })
        assert create_resp.status_code == 200
        bot_id = create_resp.json()["bot_id"]
        self.bot_ids.append(bot_id)
        
        # Toggle ON first
        self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={"enabled": True})
        
        # Toggle OFF
        resp = self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={
            "enabled": False
        })
        assert resp.status_code == 200, f"Toggle off failed: {resp.text}"
        data = resp.json()
        assert data.get("enabled") == False, f"Bot should be enabled=false after toggle, got {data.get('enabled')}"
        print(f"PASS: Bot toggled OFF, enabled={data.get('enabled')}")
    
    # ── Delete Bot Test ──
    
    def test_09_delete_bot(self):
        """DELETE /api/bots/{bot_id} deletes a bot"""
        # Create a bot first
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "webhook",
            "name": "TEST_Delete Bot"
        })
        assert create_resp.status_code == 200
        bot_id = create_resp.json()["bot_id"]
        
        # Delete
        resp = self.session.delete(f"{BASE_URL}/api/bots/{bot_id}")
        assert resp.status_code == 200, f"Delete failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "deleted"
        
        # Verify it's gone
        list_resp = self.session.get(f"{BASE_URL}/api/bots")
        bots = list_resp.json()
        found = any(b.get("bot_id") == bot_id for b in bots)
        assert not found, "Deleted bot should not be in the list"
        print("PASS: Bot deleted successfully")
    
    # ── Webhook Endpoint Tests ──
    
    def test_10_webhook_executes_when_bot_enabled(self):
        """POST /api/bots/webhook/{bot_id}/{secret} executes a webhook trade when bot is enabled"""
        # Create webhook bot
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "webhook",
            "name": "TEST_Webhook Execute Bot",
            "config": {"default_qty": 5, "max_trades_per_day": 10}
        })
        assert create_resp.status_code == 200
        bot_data = create_resp.json()
        bot_id = bot_data["bot_id"]
        webhook_secret = bot_data["webhook_secret"]
        self.bot_ids.append(bot_id)
        
        # Enable the bot
        toggle_resp = self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={"enabled": True})
        assert toggle_resp.status_code == 200
        
        # Send webhook (no auth required - uses secret in URL)
        webhook_session = requests.Session()
        webhook_session.headers.update({"Content-Type": "application/json"})
        
        resp = webhook_session.post(f"{BASE_URL}/api/bots/webhook/{bot_id}/{webhook_secret}", json={
            "action": "buy",
            "symbol": "AAPL",
            "qty": 10
        })
        assert resp.status_code == 200, f"Webhook execution failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "executed", f"Expected status=executed, got {data}"
        print("PASS: Webhook executed trade when bot enabled")
    
    def test_11_webhook_fails_when_bot_disabled(self):
        """POST /api/bots/webhook/{bot_id}/{secret} returns error when bot is disabled"""
        # Create webhook bot (defaults to OFF)
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "webhook",
            "name": "TEST_Webhook Disabled Bot"
        })
        assert create_resp.status_code == 200
        bot_data = create_resp.json()
        bot_id = bot_data["bot_id"]
        webhook_secret = bot_data["webhook_secret"]
        self.bot_ids.append(bot_id)
        
        # Bot is OFF by default - send webhook
        webhook_session = requests.Session()
        webhook_session.headers.update({"Content-Type": "application/json"})
        
        resp = webhook_session.post(f"{BASE_URL}/api/bots/webhook/{bot_id}/{webhook_secret}", json={
            "action": "buy",
            "symbol": "AAPL"
        })
        assert resp.status_code == 400, f"Expected 400 for disabled bot, got {resp.status_code}"
        data = resp.json()
        assert "disabled" in data.get("detail", "").lower(), f"Expected 'disabled' in error, got {data}"
        print("PASS: Webhook returns error when bot is disabled")
    
    def test_12_webhook_fails_with_wrong_secret(self):
        """POST /api/bots/webhook/{bot_id}/wrong_secret returns error for invalid secret"""
        # Create webhook bot
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "webhook",
            "name": "TEST_Webhook Wrong Secret Bot"
        })
        assert create_resp.status_code == 200
        bot_data = create_resp.json()
        bot_id = bot_data["bot_id"]
        self.bot_ids.append(bot_id)
        
        # Enable the bot
        self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={"enabled": True})
        
        # Send webhook with wrong secret
        webhook_session = requests.Session()
        webhook_session.headers.update({"Content-Type": "application/json"})
        
        resp = webhook_session.post(f"{BASE_URL}/api/bots/webhook/{bot_id}/wrong_secret_12345", json={
            "action": "buy",
            "symbol": "AAPL"
        })
        assert resp.status_code == 400, f"Expected 400 for wrong secret, got {resp.status_code}"
        data = resp.json()
        assert "secret" in data.get("detail", "").lower() or "invalid" in data.get("detail", "").lower(), f"Expected secret error, got {data}"
        print("PASS: Webhook returns error for invalid secret")
    
    # ── Signal Processing Test ──
    
    def test_13_signal_process_requires_auth(self):
        """POST /api/bots/signal/process requires authentication"""
        no_auth_session = requests.Session()
        no_auth_session.headers.update({"Content-Type": "application/json"})
        
        resp = no_auth_session.post(f"{BASE_URL}/api/bots/signal/process", json={
            "symbol": "AAPL",
            "ai_confidence": 85,
            "ai_verdict": "strong_buy",
            "price": 150.0
        })
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("PASS: POST /api/bots/signal/process requires auth")
    
    def test_14_signal_process_with_auth(self):
        """POST /api/bots/signal/process processes a signal through signal bots"""
        # Create and enable a signal bot
        create_resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "signal",
            "name": "TEST_Signal Process Bot",
            "config": {
                "min_confidence": 70,
                "qty": 5,
                "use_smart_order": False
            }
        })
        assert create_resp.status_code == 200
        bot_id = create_resp.json()["bot_id"]
        self.bot_ids.append(bot_id)
        
        # Enable the bot
        self.session.patch(f"{BASE_URL}/api/bots/{bot_id}/toggle", json={"enabled": True})
        
        # Process a signal
        resp = self.session.post(f"{BASE_URL}/api/bots/signal/process", json={
            "symbol": "AAPL",
            "ai_confidence": 85,
            "ai_verdict": "strong_buy",
            "price": 150.0,
            "strategy_id": "momentum"
        })
        assert resp.status_code == 200, f"Signal process failed: {resp.text}"
        data = resp.json()
        assert "executions" in data
        assert "count" in data
        print(f"PASS: Signal processed, executions={data.get('count')}")
    
    # ── Invalid Bot Type Test ──
    
    def test_15_create_invalid_bot_type_fails(self):
        """POST /api/bots with invalid type returns 400"""
        resp = self.session.post(f"{BASE_URL}/api/bots", json={
            "type": "invalid_type",
            "name": "TEST_Invalid Bot"
        })
        assert resp.status_code == 400, f"Expected 400 for invalid type, got {resp.status_code}"
        print("PASS: Invalid bot type returns 400")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
