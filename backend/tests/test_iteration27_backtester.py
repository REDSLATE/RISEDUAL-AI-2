"""
Iteration 27 Tests: Strategy Backtester Feature
Tests for:
- POST /api/auth/login — Owner login verification
- POST /api/strategy/backtest — Backtest with valid strategy, symbol AAPL, years=3
- POST /api/strategy/backtest — Returns 400 for empty symbol
- POST /api/strategy/backtest — Returns 400 for years > 5
- POST /api/strategy/generate — Returns strategy with name, entry_rules, exit_rules, risk_management
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = "managingdirector@redslateholdings.com"
OWNER_PASSWORD = "RedSlate2026!"


class TestOwnerLogin:
    """Verify owner login works"""
    
    def test_owner_login_returns_access_token(self):
        """POST /api/auth/login with owner credentials returns access_token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "No access_token in response"
        assert len(data["access_token"]) > 0, "access_token is empty"
        print(f"SUCCESS: Owner login returns access_token")


@pytest.fixture(scope="module")
def owner_token():
    """Get owner auth token for authenticated tests"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    if response.status_code != 200:
        pytest.skip(f"Owner login failed: {response.text}")
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def auth_headers(owner_token):
    """Auth headers with bearer token"""
    return {"Authorization": f"Bearer {owner_token}"}


@pytest.fixture(scope="module")
def test_strategy():
    """A valid test strategy for backtesting"""
    return {
        "name": "MACD Crossover Strategy",
        "summary": "Buy when MACD crosses above signal line, sell when RSI > 70",
        "timeframe": "Daily",
        "indicators": [
            {"name": "MACD", "period": 12, "description": "Moving Average Convergence Divergence"},
            {"name": "RSI", "period": 14, "description": "Relative Strength Index"}
        ],
        "entry_rules": [
            {"condition": "MACD line crosses above signal line", "description": "Bullish MACD crossover", "priority": 1}
        ],
        "exit_rules": [
            {"condition": "RSI > 70", "description": "Overbought condition", "priority": 1}
        ],
        "risk_management": {
            "stop_loss": "2% below entry",
            "take_profit": "6% above entry",
            "position_size": "5% of portfolio"
        }
    }


class TestStrategyGenerate:
    """Test strategy generation endpoint returns proper structure"""
    
    def test_strategy_generate_returns_required_fields(self, auth_headers):
        """POST /api/strategy/generate returns strategy with name, entry_rules, exit_rules, risk_management"""
        description = "Buy AAPL when RSI drops below 30 and MACD crosses bullish. Sell when RSI hits 70. Use 2% stop loss and 6% take profit."
        
        response = requests.post(
            f"{BASE_URL}/api/strategy/generate",
            headers=auth_headers,
            json={"description": description, "model": "gpt-5.2"},
            timeout=90  # AI generation can take 15-20 seconds
        )
        
        assert response.status_code == 200, f"Strategy generation failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "strategy" in data, "No strategy in response"
        strategy = data["strategy"]
        
        # Required fields per test requirements
        assert "name" in strategy, "Missing name in strategy"
        assert "entry_rules" in strategy, "Missing entry_rules in strategy"
        assert "exit_rules" in strategy, "Missing exit_rules in strategy"
        assert "risk_management" in strategy, "Missing risk_management in strategy"
        
        # Verify types
        assert isinstance(strategy["name"], str), "name should be string"
        assert isinstance(strategy["entry_rules"], list), "entry_rules should be list"
        assert isinstance(strategy["exit_rules"], list), "exit_rules should be list"
        assert isinstance(strategy["risk_management"], dict), "risk_management should be dict"
        
        # Verify non-empty
        assert len(strategy["name"]) > 0, "name should not be empty"
        assert len(strategy["entry_rules"]) > 0, "entry_rules should not be empty"
        assert len(strategy["exit_rules"]) > 0, "exit_rules should not be empty"
        
        print(f"SUCCESS: Strategy generated with name='{strategy['name']}', {len(strategy['entry_rules'])} entry rules, {len(strategy['exit_rules'])} exit rules")


class TestBacktestValidation:
    """Test backtest endpoint validation"""
    
    def test_backtest_requires_auth(self, test_strategy):
        """POST /api/strategy/backtest — requires authentication"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            json={"strategy": test_strategy, "symbol": "AAPL", "years": 3}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("SUCCESS: Backtest requires auth")
    
    def test_backtest_returns_400_for_empty_symbol(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest returns 400 for empty symbol"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "", "years": 3}
        )
        assert response.status_code == 400, f"Expected 400 for empty symbol, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data, "No detail in error response"
        print(f"SUCCESS: Empty symbol returns 400 - {data['detail']}")
    
    def test_backtest_returns_400_for_years_greater_than_5(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest returns 400 for years > 5"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "AAPL", "years": 6}
        )
        assert response.status_code == 400, f"Expected 400 for years > 5, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data, "No detail in error response"
        print(f"SUCCESS: Years > 5 returns 400 - {data['detail']}")
    
    def test_backtest_returns_400_for_years_less_than_1(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest returns 400 for years < 1"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "AAPL", "years": 0}
        )
        assert response.status_code == 400, f"Expected 400 for years < 1, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data, "No detail in error response"
        print(f"SUCCESS: Years < 1 returns 400 - {data['detail']}")


class TestBacktestSuccess:
    """Test backtest endpoint with valid inputs - returns metrics"""
    
    def test_backtest_with_valid_strategy_returns_metrics(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest with valid strategy, symbol AAPL, years=3 returns metrics"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "AAPL", "years": 3},
            timeout=120  # Backtest can take 15-30 seconds (Alpha Vantage + LLM)
        )
        
        assert response.status_code == 200, f"Backtest failed: {response.text}"
        data = response.json()
        
        # Verify top-level response structure
        assert "symbol" in data, "Missing symbol in response"
        assert data["symbol"] == "AAPL", f"Expected AAPL, got {data['symbol']}"
        assert "strategy_name" in data, "Missing strategy_name"
        assert "period" in data, "Missing period"
        assert "data_points" in data, "Missing data_points"
        assert "date_range" in data, "Missing date_range"
        assert "metrics" in data, "Missing metrics"
        assert "trades" in data, "Missing trades"
        
        # Verify metrics structure
        metrics = data["metrics"]
        assert "total_trades" in metrics, "Missing total_trades in metrics"
        assert "win_rate" in metrics, "Missing win_rate in metrics"
        assert "total_pnl" in metrics, "Missing total_pnl in metrics"
        assert "sharpe_ratio" in metrics, "Missing sharpe_ratio in metrics"
        assert "buy_hold_pnl" in metrics, "Missing buy_hold_pnl in metrics"
        
        # Verify total_trades > 0 (strategy should generate trades)
        assert metrics["total_trades"] > 0, f"Expected total_trades > 0, got {metrics['total_trades']}"
        
        # Verify trades array exists
        assert isinstance(data["trades"], list), "trades should be a list"
        
        # Verify win_rate is a valid percentage
        assert 0 <= metrics["win_rate"] <= 100, f"win_rate should be 0-100, got {metrics['win_rate']}"
        
        # Verify sharpe_ratio is numeric
        assert isinstance(metrics["sharpe_ratio"], (int, float)), "sharpe_ratio should be numeric"
        
        print(f"SUCCESS: Backtest completed for AAPL")
        print(f"  Total trades: {metrics['total_trades']}")
        print(f"  Win rate: {metrics['win_rate']}%")
        print(f"  Total P&L: ${metrics['total_pnl']}")
        print(f"  Sharpe ratio: {metrics['sharpe_ratio']}")
        print(f"  Buy & Hold P&L: ${metrics['buy_hold_pnl']}")
        print(f"  Data points: {data['data_points']}")
        print(f"  Date range: {data['date_range']['start']} to {data['date_range']['end']}")
    
    def test_backtest_trades_have_required_fields(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest — trades array has required fields"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "AAPL", "years": 1},  # Use 1 year for faster test
            timeout=120
        )
        
        assert response.status_code == 200, f"Backtest failed: {response.text}"
        data = response.json()
        
        trades = data.get("trades", [])
        if len(trades) > 0:
            trade = trades[0]
            # Verify trade structure
            assert "entry_date" in trade, "Missing entry_date in trade"
            assert "exit_date" in trade, "Missing exit_date in trade"
            assert "entry_price" in trade, "Missing entry_price in trade"
            assert "exit_price" in trade, "Missing exit_price in trade"
            assert "pnl" in trade, "Missing pnl in trade"
            assert "pnl_pct" in trade, "Missing pnl_pct in trade"
            assert "holding_days" in trade, "Missing holding_days in trade"
            assert "exit_reason" in trade, "Missing exit_reason in trade"
            
            # Verify exit_reason is valid
            valid_reasons = ["signal", "stop_loss", "take_profit", "open"]
            assert trade["exit_reason"] in valid_reasons, f"Invalid exit_reason: {trade['exit_reason']}"
            
            print(f"SUCCESS: Trade structure verified - {len(trades)} trades returned")
            print(f"  Sample trade: entry={trade['entry_date']}, exit={trade['exit_date']}, pnl=${trade['pnl']} ({trade['pnl_pct']}%), reason={trade['exit_reason']}")
        else:
            print("WARNING: No trades generated - strategy may need adjustment")


class TestBacktestMetricsCompleteness:
    """Test that backtest returns all expected metrics fields"""
    
    def test_backtest_metrics_include_all_fields(self, auth_headers, test_strategy):
        """POST /api/strategy/backtest — metrics include all expected fields"""
        response = requests.post(
            f"{BASE_URL}/api/strategy/backtest",
            headers=auth_headers,
            json={"strategy": test_strategy, "symbol": "MSFT", "years": 2},  # Use different symbol
            timeout=120
        )
        
        assert response.status_code == 200, f"Backtest failed: {response.text}"
        data = response.json()
        metrics = data["metrics"]
        
        # All expected metrics fields
        expected_fields = [
            "total_trades", "winning_trades", "losing_trades",
            "win_rate", "total_pnl", "avg_pnl", "avg_gain", "avg_loss",
            "max_drawdown", "sharpe_ratio", "avg_holding_days",
            "best_trade", "worst_trade", "buy_hold_pnl", "buy_hold_pct",
            "monthly", "cumulative_pnl"
        ]
        
        for field in expected_fields:
            assert field in metrics, f"Missing {field} in metrics"
        
        # Verify monthly is a list
        assert isinstance(metrics["monthly"], list), "monthly should be a list"
        
        # Verify cumulative_pnl is a list
        assert isinstance(metrics["cumulative_pnl"], list), "cumulative_pnl should be a list"
        
        print(f"SUCCESS: All {len(expected_fields)} metrics fields present")
        print(f"  Monthly breakdown: {len(metrics['monthly'])} months")
        print(f"  Cumulative P&L points: {len(metrics['cumulative_pnl'])}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
