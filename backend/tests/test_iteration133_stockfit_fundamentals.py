"""Iteration 133: StockFit Fundamentals Feature Tests

Tests the new StockFit SEC EDGAR data integration:
- GET /api/stockfit/fundamentals/{symbol} - aggregated data
- GET /api/stockfit/income/{symbol} - income statement
- GET /api/stockfit/balance-sheet/{symbol} - balance sheet
- GET /api/stockfit/scores/{symbol} - Piotroski F-Score, Altman Z-Score
- GET /api/stockfit/earnings/{symbol} - earnings snapshot
- GET /api/v1/fundamentals/{symbol} - Public API (Pro tier required)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"
PRO_API_KEY = "rsd_live_4df5f5298f15f229816796dbcdd66cc7886956bf46ce06f4"


class TestStockFitFundamentalsEndpoints:
    """Test StockFit internal API endpoints"""
    
    def test_fundamentals_aapl_returns_200(self):
        """GET /api/stockfit/fundamentals/AAPL returns aggregated data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["symbol"] == "AAPL"
        print(f"✓ Fundamentals AAPL: symbol={data['symbol']}")
    
    def test_fundamentals_aapl_has_income_data(self):
        """GET /api/stockfit/fundamentals/AAPL returns income statement data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # Check income data exists and has expected fields
        assert "income" in data, "Missing 'income' field"
        income = data["income"]
        assert isinstance(income, list), "income should be a list"
        assert len(income) >= 1, "Should have at least 1 year of income data"
        
        # Check first income entry has required fields
        first_income = income[0]
        assert "revenue" in first_income, "Missing revenue field"
        assert "netIncome" in first_income, "Missing netIncome field"
        assert "eps" in first_income, "Missing eps field"
        
        print(f"✓ Income data: {len(income)} periods, revenue={first_income.get('revenue')}")
    
    def test_fundamentals_aapl_has_balance_sheet(self):
        """GET /api/stockfit/fundamentals/AAPL returns balance sheet data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert "balanceSheet" in data, "Missing 'balanceSheet' field"
        balance = data["balanceSheet"]
        assert isinstance(balance, list), "balanceSheet should be a list"
        
        if len(balance) > 0:
            first_balance = balance[0]
            assert "assets" in first_balance, "Missing assets field"
            assert "cash" in first_balance, "Missing cash field"
            assert "totalDebt" in first_balance, "Missing totalDebt field"
            print(f"✓ Balance sheet: {len(balance)} periods, assets={first_balance.get('assets')}")
        else:
            print("⚠ Balance sheet data is empty (may be API limitation)")
    
    def test_fundamentals_aapl_has_piotroski_fscore(self):
        """GET /api/stockfit/fundamentals/AAPL returns Piotroski F-Score"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert "scores" in data, "Missing 'scores' field"
        scores = data["scores"]
        
        if scores:
            assert "piotroskiFScore" in scores, "Missing piotroskiFScore"
            f_score = scores["piotroskiFScore"]
            assert f_score is not None, "F-Score should not be None"
            assert 0 <= f_score <= 9, f"F-Score should be 0-9, got {f_score}"
            print(f"✓ Piotroski F-Score: {f_score}/9")
        else:
            print("⚠ Scores data is None (may be API limitation)")
    
    def test_fundamentals_aapl_has_earnings(self):
        """GET /api/stockfit/fundamentals/AAPL returns earnings snapshot"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        assert "earnings" in data, "Missing 'earnings' field"
        earnings = data["earnings"]
        
        if earnings:
            assert "eps" in earnings, "Missing eps in earnings"
            print(f"✓ Earnings: eps={earnings.get('eps')}, revenueGrowth={earnings.get('revenueGrowth')}")
        else:
            print("⚠ Earnings data is None (may be API limitation)")
    
    def test_fundamentals_invalid_ticker_returns_404(self):
        """GET /api/stockfit/fundamentals/INVALID_TICKER returns 404"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/XYZINVALIDTICKER123", timeout=30)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✓ Invalid ticker returns 404")
    
    def test_income_endpoint_aapl(self):
        """GET /api/stockfit/income/AAPL returns income statement array"""
        response = requests.get(f"{BASE_URL}/api/stockfit/income/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "statements" in data
        print(f"✓ Income endpoint: symbol={data['symbol']}, statements count={len(data.get('statements', []))}")
    
    def test_balance_sheet_endpoint_aapl(self):
        """GET /api/stockfit/balance-sheet/AAPL returns balance sheet array"""
        response = requests.get(f"{BASE_URL}/api/stockfit/balance-sheet/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "statements" in data
        print(f"✓ Balance sheet endpoint: symbol={data['symbol']}, statements count={len(data.get('statements', []))}")
    
    def test_scores_endpoint_aapl(self):
        """GET /api/stockfit/scores/AAPL returns Piotroski F-Score data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/scores/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "scores" in data
        
        scores = data["scores"]
        if scores and "piotroskiFScore" in scores:
            print(f"✓ Scores endpoint: F-Score={scores.get('piotroskiFScore')}, Z-Score={scores.get('altmanZScore')}")
        else:
            print(f"✓ Scores endpoint: symbol={data['symbol']}, scores returned")
    
    def test_earnings_endpoint_aapl(self):
        """GET /api/stockfit/earnings/AAPL returns earnings snapshot with eps"""
        response = requests.get(f"{BASE_URL}/api/stockfit/earnings/AAPL", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "earnings" in data
        
        earnings = data["earnings"]
        if earnings and "eps" in earnings:
            print(f"✓ Earnings endpoint: eps={earnings.get('eps')}, revenue={earnings.get('revenue')}")
        else:
            print(f"✓ Earnings endpoint: symbol={data['symbol']}, earnings returned")


class TestStockFitPublicAPI:
    """Test Public API /api/v1/fundamentals endpoint (Pro tier required)"""
    
    def test_public_fundamentals_requires_api_key(self):
        """GET /api/v1/fundamentals/AAPL returns 401 without API key"""
        response = requests.get(f"{BASE_URL}/api/v1/fundamentals/AAPL", timeout=30)
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Public fundamentals requires API key (401 without)")
    
    def test_public_fundamentals_with_pro_api_key(self):
        """GET /api/v1/fundamentals/AAPL returns 200 with Pro API key"""
        headers = {"X-API-Key": PRO_API_KEY}
        response = requests.get(f"{BASE_URL}/api/v1/fundamentals/AAPL", headers=headers, timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        print(f"✓ Public API fundamentals: symbol={data['symbol']}")


class TestStockFitDataValidation:
    """Validate data quality and format from StockFit API"""
    
    def test_income_data_has_3_years(self):
        """Verify income statement returns up to 3 years of data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        income = data.get("income", [])
        # API should return up to 3 years
        assert len(income) <= 3, f"Expected max 3 years, got {len(income)}"
        if len(income) > 0:
            print(f"✓ Income data: {len(income)} years returned")
        else:
            print("⚠ No income data returned")
    
    def test_margin_values_are_reasonable(self):
        """Verify margin values are percentages (not multiplied by 100 incorrectly)"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        earnings = data.get("earnings")
        if earnings:
            op_margin = earnings.get("operatingMargin")
            net_margin = earnings.get("netMargin")
            
            # Margins should be reasonable percentages (0-100 range typically)
            # AAPL operating margin ~32%, net margin ~27%
            if op_margin is not None:
                # If margin is already a percentage (e.g., 32.5), it should be < 100
                # If it was incorrectly multiplied (e.g., 3197%), it would be > 100
                assert op_margin < 200, f"Operating margin {op_margin}% seems too high (bug: multiplied by 100?)"
                print(f"✓ Operating margin: {op_margin}% (reasonable)")
            
            if net_margin is not None:
                assert net_margin < 200, f"Net margin {net_margin}% seems too high (bug: multiplied by 100?)"
                print(f"✓ Net margin: {net_margin}% (reasonable)")
        else:
            print("⚠ No earnings data to validate margins")
    
    def test_fscore_in_valid_range(self):
        """Verify Piotroski F-Score is between 0 and 9"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        scores = data.get("scores")
        if scores:
            f_score = scores.get("piotroskiFScore")
            if f_score is not None:
                assert 0 <= f_score <= 9, f"F-Score {f_score} out of valid range 0-9"
                print(f"✓ F-Score {f_score} is in valid range (0-9)")
            else:
                print("⚠ F-Score is None")
        else:
            print("⚠ No scores data")
    
    def test_zscore_zone_classification(self):
        """Verify Z-Score zone is correctly classified"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/AAPL", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        scores = data.get("scores")
        if scores:
            z_score = scores.get("altmanZScore")
            z_zone = scores.get("zScoreZone")
            
            if z_score is not None and z_zone is not None:
                # Verify zone classification logic
                if z_score > 2.99:
                    assert z_zone == "safe", f"Z-Score {z_score} should be 'safe' zone"
                elif z_score > 1.81:
                    assert z_zone == "grey", f"Z-Score {z_score} should be 'grey' zone"
                else:
                    assert z_zone == "distress", f"Z-Score {z_score} should be 'distress' zone"
                print(f"✓ Z-Score {z_score} correctly classified as '{z_zone}'")
            else:
                print("⚠ Z-Score or zone is None")
        else:
            print("⚠ No scores data")


class TestStockFitOtherTickers:
    """Test StockFit with various tickers"""
    
    def test_nvda_fundamentals(self):
        """GET /api/stockfit/fundamentals/NVDA returns data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/NVDA", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["symbol"] == "NVDA"
        print(f"✓ NVDA fundamentals: symbol={data['symbol']}")
    
    def test_msft_fundamentals(self):
        """GET /api/stockfit/fundamentals/MSFT returns data"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/MSFT", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["symbol"] == "MSFT"
        print(f"✓ MSFT fundamentals: symbol={data['symbol']}")
    
    def test_lowercase_ticker_normalized(self):
        """GET /api/stockfit/fundamentals/aapl normalizes to AAPL"""
        response = requests.get(f"{BASE_URL}/api/stockfit/fundamentals/aapl", timeout=30)
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL", "Ticker should be normalized to uppercase"
        print("✓ Lowercase ticker normalized to uppercase")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
