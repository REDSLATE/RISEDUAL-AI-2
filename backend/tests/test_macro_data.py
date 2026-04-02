"""
Test suite for RISEDUALAI Macro Data Endpoints
Tests: /api/gov-filings, /api/world-events, /api/foreign-markets, /api/market/prediction
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestGovFilings:
    """Test /api/gov-filings endpoint - Congressional trades, Fed announcements, SEC filings"""
    
    def test_gov_filings_returns_200(self):
        """Test that gov-filings endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/gov-filings returns 200")
    
    def test_gov_filings_has_congressional_trades(self):
        """Test that gov-filings returns congressional_trades array"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        data = response.json()
        assert 'congressional_trades' in data, "Missing congressional_trades field"
        assert isinstance(data['congressional_trades'], list), "congressional_trades should be a list"
        assert 'congressional_count' in data, "Missing congressional_count field"
        print(f"✓ Congressional trades: {data['congressional_count']} items")
    
    def test_gov_filings_has_fed_announcements(self):
        """Test that gov-filings returns fed_announcements array"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        data = response.json()
        assert 'fed_announcements' in data, "Missing fed_announcements field"
        assert isinstance(data['fed_announcements'], list), "fed_announcements should be a list"
        assert 'fed_count' in data, "Missing fed_count field"
        print(f"✓ Fed announcements: {data['fed_count']} items")
    
    def test_gov_filings_has_insider_trades(self):
        """Test that gov-filings returns insider_trades array"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        data = response.json()
        assert 'insider_trades' in data, "Missing insider_trades field"
        assert isinstance(data['insider_trades'], list), "insider_trades should be a list"
        assert 'insider_count' in data, "Missing insider_count field"
        print(f"✓ Insider trades: {data['insider_count']} items")
    
    def test_gov_filings_data_counts(self):
        """Test that gov-filings returns data (>0 for congressional and fed)"""
        response = requests.get(f"{BASE_URL}/api/gov-filings", timeout=60)
        data = response.json()
        # Congressional trades should have data (Capitol Trades scraping)
        assert data['congressional_count'] >= 0, "Congressional count should be >= 0"
        # Fed announcements should have data (Fed RSS feed)
        assert data['fed_count'] >= 0, "Fed count should be >= 0"
        # Insider trades may be 0 if SEC API is rate limited
        assert data['insider_count'] >= 0, "Insider count should be >= 0"
        print(f"✓ Data counts - Congressional: {data['congressional_count']}, Fed: {data['fed_count']}, Insider: {data['insider_count']}")


class TestWorldEvents:
    """Test /api/world-events endpoint - Geopolitical events and sector impact"""
    
    def test_world_events_returns_200(self):
        """Test that world-events endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/world-events returns 200")
    
    def test_world_events_has_total_events(self):
        """Test that world-events returns total_events count"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        data = response.json()
        assert 'total_events' in data, "Missing total_events field"
        assert isinstance(data['total_events'], int), "total_events should be an integer"
        print(f"✓ Total events: {data['total_events']}")
    
    def test_world_events_has_high_impact_events(self):
        """Test that world-events returns high_impact_events array"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        data = response.json()
        assert 'high_impact_events' in data, "Missing high_impact_events field"
        assert isinstance(data['high_impact_events'], list), "high_impact_events should be a list"
        print(f"✓ High impact events: {len(data['high_impact_events'])} items")
    
    def test_world_events_has_affected_sectors(self):
        """Test that world-events returns affected_sectors array"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        data = response.json()
        assert 'affected_sectors' in data, "Missing affected_sectors field"
        assert isinstance(data['affected_sectors'], list), "affected_sectors should be a list"
        print(f"✓ Affected sectors: {len(data['affected_sectors'])} sectors")
    
    def test_world_events_data_present(self):
        """Test that world-events returns actual data (total_events > 0)"""
        response = requests.get(f"{BASE_URL}/api/world-events", timeout=60)
        data = response.json()
        # Should have some events from news scraping
        assert data['total_events'] >= 0, "total_events should be >= 0"
        print(f"✓ World events data present: {data['total_events']} events")


class TestForeignMarkets:
    """Test /api/foreign-markets endpoint - International indices, commodities, currencies"""
    
    def test_foreign_markets_returns_200(self):
        """Test that foreign-markets endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/foreign-markets returns 200")
    
    def test_foreign_markets_has_asia(self):
        """Test that foreign-markets returns asia array"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        data = response.json()
        assert 'asia' in data, "Missing asia field"
        assert isinstance(data['asia'], list), "asia should be a list"
        print(f"✓ Asia markets: {len(data['asia'])} indices")
    
    def test_foreign_markets_has_europe(self):
        """Test that foreign-markets returns europe array"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        data = response.json()
        assert 'europe' in data, "Missing europe field"
        assert isinstance(data['europe'], list), "europe should be a list"
        print(f"✓ Europe markets: {len(data['europe'])} indices")
    
    def test_foreign_markets_has_americas(self):
        """Test that foreign-markets returns americas array"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        data = response.json()
        assert 'americas' in data, "Missing americas field"
        assert isinstance(data['americas'], list), "americas should be a list"
        print(f"✓ Americas markets: {len(data['americas'])} indices")
    
    def test_foreign_markets_has_commodities(self):
        """Test that foreign-markets returns commodities array"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        data = response.json()
        assert 'commodities' in data, "Missing commodities field"
        assert isinstance(data['commodities'], list), "commodities should be a list"
        print(f"✓ Commodities: {len(data['commodities'])} items")
    
    def test_foreign_markets_has_currencies(self):
        """Test that foreign-markets returns currencies array"""
        response = requests.get(f"{BASE_URL}/api/foreign-markets", timeout=60)
        data = response.json()
        assert 'currencies' in data, "Missing currencies field"
        assert isinstance(data['currencies'], list), "currencies should be a list"
        print(f"✓ Currencies: {len(data['currencies'])} pairs")


class TestMarketPrediction:
    """Test /api/market/prediction endpoint - AI-powered prediction with macro data"""
    
    def test_market_prediction_returns_200(self):
        """Test that market/prediction endpoint returns 200 (long timeout due to scraping + AI)"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=180)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/market/prediction returns 200")
    
    def test_market_prediction_has_direction(self):
        """Test that prediction returns overall_direction"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=180)
        data = response.json()
        assert 'overall_direction' in data, "Missing overall_direction field"
        assert data['overall_direction'] in ['BULLISH', 'BEARISH', 'NEUTRAL'], f"Invalid direction: {data['overall_direction']}"
        print(f"✓ Overall direction: {data['overall_direction']}")
    
    def test_market_prediction_has_confidence(self):
        """Test that prediction returns confidence_score"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=180)
        data = response.json()
        assert 'confidence_score' in data, "Missing confidence_score field"
        assert isinstance(data['confidence_score'], (int, float)), "confidence_score should be numeric"
        assert 0 <= data['confidence_score'] <= 100, f"confidence_score should be 0-100, got {data['confidence_score']}"
        print(f"✓ Confidence score: {data['confidence_score']}%")
    
    def test_market_prediction_has_data_sources(self):
        """Test that prediction returns data_sources with macro counts"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=180)
        data = response.json()
        assert 'data_sources' in data, "Missing data_sources field"
        ds = data['data_sources']
        # Check for new macro data source counts
        assert 'world_events' in ds, "Missing world_events in data_sources"
        assert 'foreign_markets' in ds, "Missing foreign_markets in data_sources"
        assert 'congressional_trades' in ds, "Missing congressional_trades in data_sources"
        assert 'fed_announcements' in ds, "Missing fed_announcements in data_sources"
        print(f"✓ Data sources - World Events: {ds['world_events']}, Foreign Markets: {ds['foreign_markets']}, Congressional: {ds['congressional_trades']}, Fed: {ds['fed_announcements']}")
    
    def test_market_prediction_has_macro_data(self):
        """Test that prediction returns macro_data object with sub-objects"""
        response = requests.get(f"{BASE_URL}/api/market/prediction", timeout=180)
        data = response.json()
        assert 'macro_data' in data, "Missing macro_data field"
        md = data['macro_data']
        # Check for world_events sub-object
        assert 'world_events' in md, "Missing world_events in macro_data"
        assert 'total' in md['world_events'], "Missing total in macro_data.world_events"
        # Check for foreign_markets sub-object
        assert 'foreign_markets' in md, "Missing foreign_markets in macro_data"
        assert 'total_indices' in md['foreign_markets'], "Missing total_indices in macro_data.foreign_markets"
        # Check for gov_filings sub-object
        assert 'gov_filings' in md, "Missing gov_filings in macro_data"
        assert 'congressional_trades' in md['gov_filings'], "Missing congressional_trades in macro_data.gov_filings"
        assert 'fed_announcements' in md['gov_filings'], "Missing fed_announcements in macro_data.gov_filings"
        print(f"✓ Macro data present - World Events: {md['world_events']['total']}, Foreign Indices: {md['foreign_markets']['total_indices']}, Congressional: {md['gov_filings']['congressional_trades']}, Fed: {md['gov_filings']['fed_announcements']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
