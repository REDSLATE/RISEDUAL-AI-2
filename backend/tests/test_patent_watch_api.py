"""API Integration Tests for Patent Watch Admin Feature.

Tests all /api/admin/patents/* endpoints:
- GET /queries - list saved queries (admin only)
- POST /queries - create a new query
- DELETE /queries/{id} - delete a query
- POST /refresh/{id} - trigger USPTO fetch (expects missing_api_key error)
- GET /results - list cached patent results
- GET /config - check API key configuration status

All endpoints require admin/owner role.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestPatentWatchAuth:
    """Test that all patent watch endpoints require admin role."""

    def test_queries_requires_auth(self):
        """GET /api/admin/patents/queries returns 401 without auth."""
        res = requests.get(f"{BASE_URL}/api/admin/patents/queries")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        print("PASS: GET /queries returns 401 without auth")

    def test_config_requires_auth(self):
        """GET /api/admin/patents/config returns 401 without auth."""
        res = requests.get(f"{BASE_URL}/api/admin/patents/config")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        print("PASS: GET /config returns 401 without auth")

    def test_results_requires_auth(self):
        """GET /api/admin/patents/results returns 401 without auth."""
        res = requests.get(f"{BASE_URL}/api/admin/patents/results")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        print("PASS: GET /results returns 401 without auth")


@pytest.fixture(scope="module")
def admin_session():
    """Create an authenticated session as admin."""
    session = requests.Session()
    login_res = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert login_res.status_code == 200, f"Admin login failed: {login_res.text}"
    print(f"Admin login successful: {ADMIN_EMAIL}")
    return session


class TestPatentWatchConfig:
    """Test /api/admin/patents/config endpoint."""

    def test_config_returns_api_key_status(self, admin_session):
        """GET /config returns api_key_configured=false since USPTO_API_KEY is not set."""
        res = admin_session.get(f"{BASE_URL}/api/admin/patents/config")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        
        # Verify expected fields
        assert "api_key_configured" in data, "Missing api_key_configured field"
        assert "api_base_url" in data, "Missing api_base_url field"
        assert "setup_url" in data, "Missing setup_url field"
        
        # USPTO_API_KEY is intentionally not set
        assert data["api_key_configured"] is False, "Expected api_key_configured=false"
        assert "uspto" in data["api_base_url"].lower(), "api_base_url should contain 'uspto'"
        assert "data.uspto.gov" in data["setup_url"], "setup_url should point to USPTO getting-started"
        
        # Verify key is NOT leaked
        assert "api_key" not in str(data).lower() or data.get("api_key") is None, "API key should not be leaked"
        
        print(f"PASS: GET /config returns correct structure: {data}")


class TestPatentWatchQueries:
    """Test /api/admin/patents/queries CRUD endpoints."""

    def test_list_queries_returns_200(self, admin_session):
        """GET /queries returns 200 with queries array."""
        res = admin_session.get(f"{BASE_URL}/api/admin/patents/queries")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "queries" in data, "Response should have 'queries' key"
        assert isinstance(data["queries"], list), "queries should be a list"
        print(f"PASS: GET /queries returns {len(data['queries'])} queries")

    def test_create_query_validation_empty_criteria(self, admin_session):
        """POST /queries with no criteria returns 400."""
        res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json={"label": "Empty Test", "assignee": "", "inventor_last": "", "keyword": ""},
        )
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"
        print("PASS: POST /queries with empty criteria returns 400")

    def test_create_query_success(self, admin_session):
        """POST /queries with valid data creates a query."""
        payload = {
            "label": "TEST_OpenAI_Patents",
            "assignee": "OpenAI",
            "inventor_last": "",
            "keyword": "",
        }
        res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json=payload,
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        
        # Verify response structure
        assert "query" in data, "Response should have 'query' key"
        query = data["query"]
        
        # Verify all expected fields
        assert "id" in query, "Missing 'id' field"
        assert "label" in query, "Missing 'label' field"
        assert "assignee" in query, "Missing 'assignee' field"
        assert "inventor_last" in query, "Missing 'inventor_last' field"
        assert "keyword" in query, "Missing 'keyword' field"
        assert "created_at" in query, "Missing 'created_at' field"
        assert "last_fetch_at" in query, "Missing 'last_fetch_at' field"
        assert "last_fetch_count" in query, "Missing 'last_fetch_count' field"
        assert "last_error" in query, "Missing 'last_error' field"
        
        # Verify values
        assert query["label"] == "TEST_OpenAI_Patents"
        assert query["assignee"] == "OpenAI"
        assert query["last_fetch_at"] is None
        assert query["last_fetch_count"] == 0
        assert query["last_error"] is None
        
        # Verify _id is NOT in response (MongoDB ObjectId stripped)
        assert "_id" not in query, "MongoDB _id should be stripped from response"
        
        print(f"PASS: POST /queries created query with id={query['id']}")
        return query["id"]

    def test_create_query_with_all_criteria(self, admin_session):
        """POST /queries with all criteria fields."""
        payload = {
            "label": "TEST_Full_Criteria",
            "assignee": "Google",
            "inventor_last": "Smith",
            "keyword": "machine learning",
        }
        res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json=payload,
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        query = res.json()["query"]
        assert query["assignee"] == "Google"
        assert query["inventor_last"] == "Smith"
        assert query["keyword"] == "machine learning"
        print(f"PASS: POST /queries with all criteria created query id={query['id']}")
        return query["id"]


class TestPatentWatchRefresh:
    """Test /api/admin/patents/refresh/{id} endpoint."""

    def test_refresh_returns_missing_api_key_error(self, admin_session):
        """POST /refresh/{id} returns error='missing_api_key' since USPTO_API_KEY is not set."""
        # First create a query to refresh
        create_res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json={"label": "TEST_Refresh_Query", "assignee": "Microsoft"},
        )
        assert create_res.status_code == 200
        query_id = create_res.json()["query"]["id"]
        
        # Now refresh it
        refresh_res = admin_session.post(f"{BASE_URL}/api/admin/patents/refresh/{query_id}")
        assert refresh_res.status_code == 200, f"Expected 200, got {refresh_res.status_code}"
        data = refresh_res.json()
        
        # Verify graceful error response
        assert "fetched" in data, "Missing 'fetched' field"
        assert "error" in data, "Missing 'error' field"
        assert data["fetched"] == 0, f"Expected fetched=0, got {data['fetched']}"
        assert data["error"] == "missing_api_key", f"Expected error='missing_api_key', got {data['error']}"
        
        print(f"PASS: POST /refresh/{query_id} returns {{fetched:0, error:'missing_api_key'}}")
        return query_id

    def test_refresh_unknown_query_returns_error(self, admin_session):
        """POST /refresh/{unknown_id} returns error='query_not_found'."""
        res = admin_session.post(f"{BASE_URL}/api/admin/patents/refresh/nonexistent-id-12345")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert data["error"] == "query_not_found"
        print("PASS: POST /refresh/unknown returns error='query_not_found'")


class TestPatentWatchResults:
    """Test /api/admin/patents/results endpoint."""

    def test_results_returns_empty_for_fresh_query(self, admin_session):
        """GET /results?query_id=X returns empty results for a fresh query."""
        # Create a fresh query
        create_res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json={"label": "TEST_Results_Query", "keyword": "quantum"},
        )
        assert create_res.status_code == 200
        query_id = create_res.json()["query"]["id"]
        
        # Get results for this query
        res = admin_session.get(f"{BASE_URL}/api/admin/patents/results?query_id={query_id}&limit=5")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        
        assert "results" in data, "Response should have 'results' key"
        assert isinstance(data["results"], list), "results should be a list"
        assert len(data["results"]) == 0, f"Expected empty results, got {len(data['results'])}"
        
        print(f"PASS: GET /results?query_id={query_id}&limit=5 returns empty results")
        return query_id

    def test_results_all_queries(self, admin_session):
        """GET /results without query_id returns all cached results."""
        res = admin_session.get(f"{BASE_URL}/api/admin/patents/results?limit=50")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "results" in data
        print(f"PASS: GET /results returns {len(data['results'])} total cached patents")


class TestPatentWatchDelete:
    """Test DELETE /api/admin/patents/queries/{id} endpoint."""

    def test_delete_query_success(self, admin_session):
        """DELETE /queries/{id} returns {deleted:true}."""
        # Create a query to delete
        create_res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json={"label": "TEST_Delete_Query", "assignee": "Tesla"},
        )
        assert create_res.status_code == 200
        query_id = create_res.json()["query"]["id"]
        
        # Delete it
        delete_res = admin_session.delete(f"{BASE_URL}/api/admin/patents/queries/{query_id}")
        assert delete_res.status_code == 200, f"Expected 200, got {delete_res.status_code}"
        data = delete_res.json()
        assert data.get("deleted") is True, f"Expected deleted=true, got {data}"
        
        print(f"PASS: DELETE /queries/{query_id} returns {{deleted:true}}")
        return query_id

    def test_delete_same_query_twice_returns_404(self, admin_session):
        """DELETE /queries/{id} twice returns 404 on second attempt."""
        # Create a query
        create_res = admin_session.post(
            f"{BASE_URL}/api/admin/patents/queries",
            json={"label": "TEST_Double_Delete", "keyword": "blockchain"},
        )
        assert create_res.status_code == 200
        query_id = create_res.json()["query"]["id"]
        
        # First delete - should succeed
        delete1 = admin_session.delete(f"{BASE_URL}/api/admin/patents/queries/{query_id}")
        assert delete1.status_code == 200
        
        # Second delete - should return 404
        delete2 = admin_session.delete(f"{BASE_URL}/api/admin/patents/queries/{query_id}")
        assert delete2.status_code == 404, f"Expected 404 on second delete, got {delete2.status_code}"
        
        print(f"PASS: Second DELETE /queries/{query_id} returns 404")


class TestPatentWatchCleanup:
    """Cleanup test data after all tests."""

    def test_cleanup_test_queries(self, admin_session):
        """Delete all TEST_ prefixed queries created during testing."""
        res = admin_session.get(f"{BASE_URL}/api/admin/patents/queries")
        assert res.status_code == 200
        queries = res.json().get("queries", [])
        
        deleted_count = 0
        for q in queries:
            if q.get("label", "").startswith("TEST_"):
                del_res = admin_session.delete(f"{BASE_URL}/api/admin/patents/queries/{q['id']}")
                if del_res.status_code == 200:
                    deleted_count += 1
        
        print(f"CLEANUP: Deleted {deleted_count} test queries")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
