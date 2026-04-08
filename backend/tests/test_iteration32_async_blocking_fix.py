"""
Iteration 32: Async Blocking Fix Tests
Tests that synchronous requests.get() calls wrapped in asyncio.to_thread() 
no longer block the FastAPI event loop.

Key test: Login should complete in <3s even while slow scraping endpoints are running.
"""
import pytest
import requests
import os
import time
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor, as_completed
from conftest_creds import OWNER_EMAIL, OWNER_PASSWORD, BASE_URL

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = OWNER_EMAIL
OWNER_PASSWORD = OWNER_PASSWORD


class TestLoginSpeed:
    """Test that login completes quickly without blocking"""
    
    def test_login_returns_token_quickly(self):
        """Login should complete in under 3 seconds"""
        start = time.time()
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        elapsed = time.time() - start
        
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "access_token" in data, "No access_token in response"
        assert elapsed < 3.0, f"Login took {elapsed:.2f}s, expected <3s"
        print(f"✓ Login completed in {elapsed:.2f}s (target: <3s)")


class TestConcurrentRequests:
    """Test that slow endpoints don't block fast endpoints"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200
        return response.json()["access_token"]
    
    def test_login_during_market_prediction(self, auth_token):
        """
        CRITICAL TEST: Login should complete in <3s even while market/prediction is running.
        This was the main production bug - scraping blocked the event loop for 30+ seconds.
        """
        results = {"login_time": None, "prediction_started": False, "prediction_completed": False}
        
        def start_prediction():
            """Start the slow market prediction endpoint"""
            try:
                results["prediction_started"] = True
                headers = {"Authorization": f"Bearer {auth_token}"}
                # This endpoint takes 20-30 seconds
                response = requests.get(
                    f"{BASE_URL}/api/market/prediction",
                    headers=headers,
                    timeout=60
                )
                results["prediction_completed"] = True
                return response.status_code
            except Exception as e:
                return f"Error: {e}"
        
        def do_login():
            """Attempt login while prediction is running"""
            time.sleep(1)  # Wait for prediction to start
            start = time.time()
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
                timeout=10
            )
            elapsed = time.time() - start
            results["login_time"] = elapsed
            return response.status_code, elapsed
        
        # Run both concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            prediction_future = executor.submit(start_prediction)
            login_future = executor.submit(do_login)
            
            # Wait for login to complete (should be fast)
            login_status, login_time = login_future.result(timeout=15)
            
            assert login_status == 200, f"Login failed with status {login_status}"
            assert login_time < 3.0, f"Login took {login_time:.2f}s while prediction running - EVENT LOOP BLOCKED!"
            print(f"✓ Login completed in {login_time:.2f}s while prediction was running")
            
            # Let prediction complete (or timeout)
            try:
                prediction_status = prediction_future.result(timeout=45)
                print(f"✓ Prediction completed with status {prediction_status}")
            except Exception as e:
                print(f"⚠ Prediction timed out or failed: {e} (expected for slow endpoint)")


class TestScrapingEndpoints:
    """Test that all refactored scraping endpoints work correctly"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200
        return response.json()["access_token"]
    
    def test_world_events_endpoint(self, auth_token):
        """GET /api/world-events - uses asyncio.to_thread for RSS scraping"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/world-events", headers=headers, timeout=30)
        elapsed = time.time() - start
        
        # Should return 200 or 401 (if auth required) - not hang
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ World events endpoint responded in {elapsed:.2f}s with status {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            assert "all_events" in data or "events" in data or "total_events" in data
    
    def test_foreign_markets_endpoint(self, auth_token):
        """GET /api/foreign-markets - uses asyncio.to_thread for Yahoo Finance scraping"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/foreign-markets", headers=headers, timeout=30)
        elapsed = time.time() - start
        
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ Foreign markets endpoint responded in {elapsed:.2f}s with status {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            # Should have regional market data
            assert any(key in data for key in ["asia", "europe", "americas", "markets"])
    
    def test_gov_filings_endpoint(self, auth_token):
        """GET /api/gov-filings - uses async _get() helper"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/gov-filings", headers=headers, timeout=30)
        elapsed = time.time() - start
        
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ Gov filings endpoint responded in {elapsed:.2f}s with status {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            # Should have filings data
            assert any(key in data for key in ["insider_trades", "fed_announcements", "filings"])
    
    def test_dark_pool_endpoint(self, auth_token):
        """GET /api/dark-pool - should return dark pool data"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/dark-pool", headers=headers, timeout=15)
        elapsed = time.time() - start
        
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ Dark pool endpoint responded in {elapsed:.2f}s with status {response.status_code}")
    
    def test_stocks_ticker_endpoint(self, auth_token):
        """GET /api/stocks/ticker - uses asyncio.to_thread for Alpha Vantage"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/stocks/ticker", headers=headers, timeout=30)
        elapsed = time.time() - start
        
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ Stocks ticker endpoint responded in {elapsed:.2f}s with status {response.status_code}")
    
    def test_crypto_prices_endpoint(self, auth_token):
        """GET /api/crypto/prices - uses asyncio.to_thread for Alpha Vantage"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/crypto/prices", headers=headers, timeout=30)
        elapsed = time.time() - start
        
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"
        print(f"✓ Crypto prices endpoint responded in {elapsed:.2f}s with status {response.status_code}")


class TestAIIntelligenceEndpoint:
    """Test AI intelligence endpoint still works after async refactor"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200
        return response.json()["access_token"]
    
    def test_intelligence_brief_endpoint(self, auth_token):
        """GET /api/intelligence/brief/AAPL - uses async _fetch_daily and _fetch_quote"""
        headers = {"Authorization": f"Bearer {auth_token}"}
        start = time.time()
        response = requests.get(
            f"{BASE_URL}/api/intelligence/brief/AAPL",
            headers=headers,
            timeout=45  # AI endpoints can take longer
        )
        elapsed = time.time() - start
        
        # Should return 200 or auth error, not hang
        assert response.status_code in [200, 401, 403, 429], f"Unexpected status: {response.status_code}"
        print(f"✓ Intelligence brief endpoint responded in {elapsed:.2f}s with status {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            assert "symbol" in data or "brief" in data


class TestMultipleConcurrentRequests:
    """Test multiple concurrent requests to verify event loop isn't blocked"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for authenticated requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200
        return response.json()["access_token"]
    
    def test_multiple_fast_requests_during_slow_request(self, auth_token):
        """
        Multiple fast requests should complete quickly even while a slow request is running.
        This verifies the event loop isn't blocked.
        """
        headers = {"Authorization": f"Bearer {auth_token}"}
        fast_times = []
        
        def slow_request():
            """Start a slow scraping request"""
            try:
                response = requests.get(
                    f"{BASE_URL}/api/world-events",
                    headers=headers,
                    timeout=45
                )
                return response.status_code
            except Exception as e:
                return f"Error: {e}"
        
        def fast_request(request_num):
            """Make a fast health check or auth request"""
            time.sleep(0.5 * request_num)  # Stagger requests
            start = time.time()
            response = requests.get(f"{BASE_URL}/api/", timeout=10)
            elapsed = time.time() - start
            fast_times.append(elapsed)
            return response.status_code, elapsed
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            # Start slow request
            slow_future = executor.submit(slow_request)
            
            # Start 5 fast requests while slow one is running
            fast_futures = [executor.submit(fast_request, i) for i in range(5)]
            
            # Check fast requests completed quickly
            for future in as_completed(fast_futures, timeout=15):
                status, elapsed = future.result()
                assert status == 200, f"Fast request failed with status {status}"
                assert elapsed < 2.0, f"Fast request took {elapsed:.2f}s - event loop may be blocked!"
            
            print(f"✓ All 5 fast requests completed. Times: {[f'{t:.2f}s' for t in fast_times]}")
            
            # Let slow request finish
            try:
                slow_status = slow_future.result(timeout=45)
                print(f"✓ Slow request completed with status {slow_status}")
            except Exception as e:
                print(f"⚠ Slow request timed out: {e}")


class TestHealthEndpoint:
    """Basic health check to ensure server is responsive"""
    
    def test_health_endpoint_fast(self):
        """Root API endpoint should respond in <1s"""
        start = time.time()
        response = requests.get(f"{BASE_URL}/api/", timeout=5)
        elapsed = time.time() - start
        
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        assert elapsed < 1.0, f"Health check took {elapsed:.2f}s, expected <1s"
        print(f"✓ Root API endpoint responded in {elapsed:.2f}s")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
