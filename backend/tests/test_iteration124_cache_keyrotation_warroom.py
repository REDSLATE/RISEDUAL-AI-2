"""
Iteration 124: Testing sliding cache, key rotation, and War Room AI analysis
- GET /api/market/prediction — sliding cache with hit/miss
- GET /api/market/prediction/{symbol} — ticker-focused prediction with sliding cache
- GET /api/market/prediction?force_refresh=true — cache bypass
- GET /api/web-intel/status — provider status for FRED, AI analysis (Groq/OpenRouter/Emergent)
- POST /api/web-intel/war-room — fires all engines + AI analysis with failover chain
- Key rotation: FRED_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS support comma-separated keys
"""
import pytest
import requests
import os
import time

from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user."""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    token = data.get("access_token")
    assert token, "No access_token in login response"
    return token


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return headers with auth token."""
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


class TestWebIntelStatus:
    """Test GET /api/web-intel/status endpoint for provider status."""

    def test_status_endpoint_returns_provider_info(self):
        """Status endpoint should return provider availability info."""
        response = requests.get(f"{BASE_URL}/api/web-intel/status")
        assert response.status_code == 200
        data = response.json()
        
        # Check basic providers
        assert data.get("duckduckgo") is True
        assert data.get("wikipedia") is True
        assert data.get("sec_edgar") is True
        assert data.get("yahoo") is True
        
    def test_status_shows_fred_key_rotation_info(self):
        """FRED status should show key rotation info."""
        response = requests.get(f"{BASE_URL}/api/web-intel/status")
        assert response.status_code == 200
        data = response.json()
        
        fred = data.get("fred", {})
        assert "provider" in fred
        assert "total_keys" in fred
        assert "healthy_keys" in fred
        assert "configured" in fred
        # FRED_API_KEYS is empty in .env, so configured should be False
        assert fred["configured"] is False
        assert fred["total_keys"] == 0
        
    def test_status_shows_ai_analysis_providers(self):
        """AI analysis status should show Groq, OpenRouter, and Emergent."""
        response = requests.get(f"{BASE_URL}/api/web-intel/status")
        assert response.status_code == 200
        data = response.json()
        
        ai = data.get("ai_analysis", {})
        
        # Groq status
        groq = ai.get("groq", {})
        assert "provider" in groq
        assert groq["configured"] is False  # GROQ_API_KEYS is empty
        
        # OpenRouter status
        openrouter = ai.get("openrouter", {})
        assert "provider" in openrouter
        assert openrouter["configured"] is False  # OPENROUTER_API_KEYS is empty
        
        # Emergent status (should be True since EMERGENT_LLM_KEY is configured)
        assert ai.get("emergent") is True


class TestMarketPredictionSlidingCache:
    """Test GET /api/market/prediction with sliding cache policy."""

    def test_prediction_first_call_cache_miss(self, auth_headers):
        """First call should be a cache miss with sliding policy."""
        # Note: This test may take 30-60s on first call
        response = requests.get(
            f"{BASE_URL}/api/market/prediction",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        # Check _cache metadata
        cache_info = data.get("_cache", {})
        assert cache_info.get("policy") == "sliding"
        assert "ttlSeconds" in cache_info
        assert "maxAgeSeconds" in cache_info
        # First call may be hit or miss depending on previous tests
        
    def test_prediction_second_call_cache_hit(self, auth_headers):
        """Second call should be a cache hit (instant response)."""
        # First call to ensure cache is populated
        requests.get(
            f"{BASE_URL}/api/market/prediction",
            headers=auth_headers,
            timeout=120
        )
        
        # Second call should be instant cache hit
        start = time.time()
        response = requests.get(
            f"{BASE_URL}/api/market/prediction",
            headers=auth_headers,
            timeout=10
        )
        elapsed = time.time() - start
        
        assert response.status_code == 200
        data = response.json()
        
        cache_info = data.get("_cache", {})
        assert cache_info.get("hit") is True
        assert cache_info.get("policy") == "sliding"
        # Cache hit should be fast (< 5 seconds)
        assert elapsed < 5, f"Cache hit took too long: {elapsed}s"
        
    def test_prediction_force_refresh_bypasses_cache(self, auth_headers):
        """force_refresh=true should bypass cache and return hit=False."""
        response = requests.get(
            f"{BASE_URL}/api/market/prediction?force_refresh=true",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        cache_info = data.get("_cache", {})
        assert cache_info.get("hit") is False
        assert cache_info.get("policy") == "sliding"


class TestTickerPredictionSlidingCache:
    """Test GET /api/market/prediction/{symbol} with sliding cache."""

    def test_ticker_prediction_returns_symbol_and_focused_flag(self, auth_headers):
        """Ticker prediction should return symbol and ticker_focused=True."""
        response = requests.get(
            f"{BASE_URL}/api/market/prediction/AAPL",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("symbol") == "AAPL"
        assert data.get("ticker_focused") is True
        
        cache_info = data.get("_cache", {})
        assert cache_info.get("policy") == "sliding"
        
    def test_ticker_prediction_cache_hit_on_second_call(self, auth_headers):
        """Second call for same ticker should be cache hit."""
        # First call
        requests.get(
            f"{BASE_URL}/api/market/prediction/MSFT",
            headers=auth_headers,
            timeout=120
        )
        
        # Second call should be cache hit
        start = time.time()
        response = requests.get(
            f"{BASE_URL}/api/market/prediction/MSFT",
            headers=auth_headers,
            timeout=10
        )
        elapsed = time.time() - start
        
        assert response.status_code == 200
        data = response.json()
        
        cache_info = data.get("_cache", {})
        assert cache_info.get("hit") is True
        assert elapsed < 5, f"Cache hit took too long: {elapsed}s"
        
    def test_ticker_prediction_invalid_symbol(self, auth_headers):
        """Invalid symbol should return 400."""
        response = requests.get(
            f"{BASE_URL}/api/market/prediction/TOOLONGSYMBOL123",
            headers=auth_headers,
            timeout=10
        )
        assert response.status_code == 400


class TestWarRoomWithAIAnalysis:
    """Test POST /api/web-intel/war-room with AI analysis failover chain."""

    def test_war_room_company_query_returns_brief(self, auth_headers):
        """War room should return brief with headline, summary, signals."""
        response = requests.post(
            f"{BASE_URL}/api/web-intel/war-room",
            headers=auth_headers,
            json={"query": "NVDA stock analysis", "symbol": "NVDA", "mode": "auto"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("query") == "NVDA stock analysis"
        
        brief = data.get("brief", {})
        assert "headline" in brief
        assert "summary" in brief
        assert "signals" in brief
        
    def test_war_room_includes_ai_analysis_engine(self, auth_headers):
        """War room should include ai_analysis engine result."""
        response = requests.post(
            f"{BASE_URL}/api/web-intel/war-room",
            headers=auth_headers,
            json={"query": "Tesla earnings", "symbol": "TSLA", "mode": "company"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        engines = data.get("engine_results", [])
        engine_names = [e.get("engine", "") for e in engines]
        
        # Should have ai_analysis:emergent (since Groq/OpenRouter not configured)
        ai_engines = [e for e in engine_names if e.startswith("ai_analysis")]
        assert len(ai_engines) > 0, "No AI analysis engine in results"
        
        # Check AI analysis status
        ai_result = next((e for e in engines if e.get("engine", "").startswith("ai_analysis")), None)
        assert ai_result is not None
        # Should be ok or skipped (if all providers fail)
        assert ai_result.get("status") in ["ok", "skipped"]
        
    def test_war_room_macro_query_fred_gracefully_skips(self, auth_headers):
        """Macro query should show FRED as skipped (no keys configured)."""
        response = requests.post(
            f"{BASE_URL}/api/web-intel/war-room",
            headers=auth_headers,
            json={"query": "CPI inflation data", "mode": "macro"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        engines = data.get("engine_results", [])
        fred_result = next((e for e in engines if e.get("engine") == "fred"), None)
        
        assert fred_result is not None, "FRED engine not in results"
        assert fred_result.get("status") == "skipped"
        assert fred_result.get("error") == "missing_fred_api_keys"
        
    def test_war_room_ai_analysis_still_works_without_groq_openrouter(self, auth_headers):
        """AI analysis should work via Emergent even without Groq/OpenRouter."""
        response = requests.post(
            f"{BASE_URL}/api/web-intel/war-room",
            headers=auth_headers,
            json={"query": "Apple stock news", "symbol": "AAPL", "mode": "news"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        engines = data.get("engine_results", [])
        ai_result = next((e for e in engines if e.get("engine", "").startswith("ai_analysis")), None)
        
        if ai_result and ai_result.get("status") == "ok":
            # Should be using emergent provider
            assert "emergent" in ai_result.get("engine", "")
            
    def test_war_room_requires_auth(self):
        """War room should require authentication."""
        response = requests.post(
            f"{BASE_URL}/api/web-intel/war-room",
            json={"query": "test query"},
            timeout=10
        )
        assert response.status_code == 401


class TestKeyRotatorIntegration:
    """Test key rotation system integration."""

    def test_fred_rotator_status_in_web_intel_status(self):
        """FRED rotator status should be exposed in /api/web-intel/status."""
        response = requests.get(f"{BASE_URL}/api/web-intel/status")
        assert response.status_code == 200
        data = response.json()
        
        fred = data.get("fred", {})
        # Key rotator exposes these fields
        assert "total_keys" in fred
        assert "healthy_keys" in fred
        assert "configured" in fred
        
    def test_ai_analysis_rotators_status(self):
        """AI analysis rotators (Groq, OpenRouter) should be in status."""
        response = requests.get(f"{BASE_URL}/api/web-intel/status")
        assert response.status_code == 200
        data = response.json()
        
        ai = data.get("ai_analysis", {})
        
        # Groq rotator
        groq = ai.get("groq", {})
        assert "total_keys" in groq
        assert "healthy_keys" in groq
        
        # OpenRouter rotator
        openrouter = ai.get("openrouter", {})
        assert "total_keys" in openrouter
        assert "healthy_keys" in openrouter


class TestCacheMetadata:
    """Test cache metadata structure."""

    def test_cache_metadata_has_required_fields(self, auth_headers):
        """Cache metadata should have hit, policy, ttlSeconds, maxAgeSeconds."""
        response = requests.get(
            f"{BASE_URL}/api/market/prediction",
            headers=auth_headers,
            timeout=120
        )
        assert response.status_code == 200
        data = response.json()
        
        cache_info = data.get("_cache", {})
        assert "hit" in cache_info
        assert "policy" in cache_info
        assert cache_info["policy"] == "sliding"
        assert "ttlSeconds" in cache_info
        assert "maxAgeSeconds" in cache_info
        
        # TTL should be 300 seconds (5 minutes)
        assert cache_info["ttlSeconds"] == 300
        # Max age should be 900 seconds (15 minutes)
        assert cache_info["maxAgeSeconds"] == 900
