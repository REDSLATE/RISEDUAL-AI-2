"""
Test Fear & Greed Index and Lobbying Data APIs
Tests for iteration 80 - new data integration from user-uploaded XLSX files
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestFearGreedAPI:
    """Fear & Greed Index endpoint tests"""
    
    def test_fear_greed_returns_200(self):
        """GET /api/fear-greed returns 200"""
        response = requests.get(f"{BASE_URL}/api/fear-greed", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/fear-greed returns 200")
    
    def test_fear_greed_has_current_value(self):
        """Fear & Greed response contains current value and label"""
        response = requests.get(f"{BASE_URL}/api/fear-greed", timeout=15)
        data = response.json()
        
        assert "current" in data, "Missing 'current' field"
        current = data["current"]
        assert "value" in current, "Missing 'value' in current"
        assert "label" in current, "Missing 'label' in current"
        assert isinstance(current["value"], (int, float)), "Value should be numeric"
        assert 0 <= current["value"] <= 100, f"Value {current['value']} out of range 0-100"
        print(f"PASS: Current value={current['value']}, label={current['label']}")
    
    def test_fear_greed_has_averages(self):
        """Fear & Greed response contains 7d and 30d averages"""
        response = requests.get(f"{BASE_URL}/api/fear-greed", timeout=15)
        data = response.json()
        
        assert "avg_7d" in data, "Missing 'avg_7d' field"
        assert "avg_30d" in data, "Missing 'avg_30d' field"
        assert isinstance(data["avg_7d"], (int, float)), "avg_7d should be numeric"
        assert isinstance(data["avg_30d"], (int, float)), "avg_30d should be numeric"
        print(f"PASS: 7d avg={data['avg_7d']}, 30d avg={data['avg_30d']}")
    
    def test_fear_greed_has_history(self):
        """Fear & Greed response contains historical data (90 days)"""
        response = requests.get(f"{BASE_URL}/api/fear-greed", timeout=15)
        data = response.json()
        
        assert "history" in data, "Missing 'history' field"
        history = data["history"]
        assert isinstance(history, list), "History should be a list"
        assert len(history) > 0, "History should not be empty"
        
        # Check first record structure
        first = history[0]
        assert "date" in first, "History record missing 'date'"
        assert "index" in first, "History record missing 'index'"
        print(f"PASS: History contains {len(history)} records")
    
    def test_fear_greed_total_records(self):
        """Fear & Greed response contains total_records count"""
        response = requests.get(f"{BASE_URL}/api/fear-greed", timeout=15)
        data = response.json()
        
        assert "total_records" in data, "Missing 'total_records' field"
        assert data["total_records"] > 0, "total_records should be > 0"
        print(f"PASS: Total records in DB: {data['total_records']}")


class TestLobbyingAPI:
    """Lobbying data endpoint tests"""
    
    def test_lobbying_summary_returns_200(self):
        """GET /api/lobbying returns 200"""
        response = requests.get(f"{BASE_URL}/api/lobbying", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/lobbying returns 200")
    
    def test_lobbying_has_total_filings(self):
        """Lobbying summary contains total_filings count"""
        response = requests.get(f"{BASE_URL}/api/lobbying", timeout=15)
        data = response.json()
        
        assert "total_filings" in data, "Missing 'total_filings' field"
        assert data["total_filings"] > 0, "total_filings should be > 0"
        print(f"PASS: Total lobbying filings: {data['total_filings']}")
    
    def test_lobbying_has_top_spenders(self):
        """Lobbying summary contains top_spenders list"""
        response = requests.get(f"{BASE_URL}/api/lobbying", timeout=15)
        data = response.json()
        
        assert "top_spenders" in data, "Missing 'top_spenders' field"
        spenders = data["top_spenders"]
        assert isinstance(spenders, list), "top_spenders should be a list"
        assert len(spenders) > 0, "top_spenders should not be empty"
        
        # Check first spender structure
        first = spenders[0]
        assert "ticker" in first, "Spender missing 'ticker'"
        assert "total_amount" in first, "Spender missing 'total_amount'"
        assert "filing_count" in first, "Spender missing 'filing_count'"
        assert "client" in first, "Spender missing 'client'"
        print(f"PASS: Top spender: {first['ticker']} - ${first['total_amount']:,}")
    
    def test_lobbying_has_recent_filings(self):
        """Lobbying summary contains recent_filings list"""
        response = requests.get(f"{BASE_URL}/api/lobbying", timeout=15)
        data = response.json()
        
        assert "recent_filings" in data, "Missing 'recent_filings' field"
        filings = data["recent_filings"]
        assert isinstance(filings, list), "recent_filings should be a list"
        print(f"PASS: Recent filings count: {len(filings)}")
    
    def test_lobbying_has_top_issues(self):
        """Lobbying summary contains top_issues list"""
        response = requests.get(f"{BASE_URL}/api/lobbying", timeout=15)
        data = response.json()
        
        assert "top_issues" in data, "Missing 'top_issues' field"
        issues = data["top_issues"]
        assert isinstance(issues, list), "top_issues should be a list"
        print(f"PASS: Top issues count: {len(issues)}")


class TestLobbyingByTicker:
    """Lobbying by ticker endpoint tests"""
    
    def test_lobbying_ticker_meta_returns_200(self):
        """GET /api/lobbying/ticker/META returns 200"""
        response = requests.get(f"{BASE_URL}/api/lobbying/ticker/META", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/lobbying/ticker/META returns 200")
    
    def test_lobbying_ticker_meta_has_filings(self):
        """META lobbying response contains filings"""
        response = requests.get(f"{BASE_URL}/api/lobbying/ticker/META", timeout=15)
        data = response.json()
        
        assert "ticker" in data, "Missing 'ticker' field"
        assert data["ticker"] == "META", f"Expected ticker META, got {data['ticker']}"
        assert "filings" in data, "Missing 'filings' field"
        assert "count" in data, "Missing 'count' field"
        assert data["count"] > 0, "META should have lobbying filings"
        print(f"PASS: META has {data['count']} lobbying filings")
    
    def test_lobbying_ticker_unknown_returns_empty(self):
        """Unknown ticker returns empty filings list"""
        response = requests.get(f"{BASE_URL}/api/lobbying/ticker/ZZZZZ", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["count"] == 0, "Unknown ticker should have 0 filings"
        print("PASS: Unknown ticker returns empty filings")


class TestTopSpendersEndpoint:
    """Top lobbying spenders endpoint tests"""
    
    def test_top_spenders_returns_200(self):
        """GET /api/lobbying/top-spenders returns 200"""
        response = requests.get(f"{BASE_URL}/api/lobbying/top-spenders", timeout=15)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/lobbying/top-spenders returns 200")
    
    def test_top_spenders_returns_20(self):
        """Top spenders endpoint returns up to 20 spenders"""
        response = requests.get(f"{BASE_URL}/api/lobbying/top-spenders", timeout=15)
        data = response.json()
        
        assert "top_spenders" in data, "Missing 'top_spenders' field"
        spenders = data["top_spenders"]
        assert len(spenders) <= 20, f"Expected max 20 spenders, got {len(spenders)}"
        assert len(spenders) > 0, "Should have at least 1 spender"
        print(f"PASS: Top spenders count: {len(spenders)}")
    
    def test_top_spenders_sorted_by_amount(self):
        """Top spenders are sorted by total_amount descending"""
        response = requests.get(f"{BASE_URL}/api/lobbying/top-spenders", timeout=15)
        data = response.json()
        spenders = data["top_spenders"]
        
        amounts = [s["total_amount"] for s in spenders]
        assert amounts == sorted(amounts, reverse=True), "Spenders not sorted by amount"
        print(f"PASS: Spenders sorted correctly, top: ${amounts[0]:,}")


class TestGovFilingsWithLobbying:
    """Gov filings endpoint now includes lobbying data"""
    
    def test_gov_filings_returns_200(self):
        """GET /api/gov-filings returns 200"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/gov-filings returns 200")
    
    def test_gov_filings_has_lobbying_spenders(self):
        """Gov filings response includes lobbying_top_spenders"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        data = response.json()
        
        assert "lobbying_top_spenders" in data, "Missing 'lobbying_top_spenders' field"
        assert "lobbying_count" in data, "Missing 'lobbying_count' field"
        spenders = data["lobbying_top_spenders"]
        assert isinstance(spenders, list), "lobbying_top_spenders should be a list"
        print(f"PASS: Gov filings includes {data['lobbying_count']} lobbying spenders")
    
    def test_gov_filings_has_congressional_trades(self):
        """Gov filings response includes congressional_trades with real tickers"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        data = response.json()
        
        assert "congressional_trades" in data, "Missing 'congressional_trades' field"
        assert "congressional_count" in data, "Missing 'congressional_count' field"
        trades = data["congressional_trades"]
        assert isinstance(trades, list), "congressional_trades should be a list"
        print(f"PASS: Congressional trades count: {data['congressional_count']}")
    
    def test_gov_filings_has_fed_announcements(self):
        """Gov filings response includes fed_announcements"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        data = response.json()
        
        assert "fed_announcements" in data, "Missing 'fed_announcements' field"
        assert "fed_count" in data, "Missing 'fed_count' field"
        print(f"PASS: Fed announcements count: {data['fed_count']}")
    
    def test_gov_filings_has_insider_trades(self):
        """Gov filings response includes insider_trades"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=30)
        data = response.json()
        
        assert "insider_trades" in data, "Missing 'insider_trades' field"
        assert "insider_count" in data, "Missing 'insider_count' field"
        print(f"PASS: Insider trades count: {data['insider_count']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
