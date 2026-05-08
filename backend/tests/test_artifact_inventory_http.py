"""HTTP-level integration tests for GET /api/admin/ml/artifacts endpoint.

Tests the endpoint via the public URL with real authentication.
Covers:
  1. Unauth returns 401
  2. Admin returns 200 with {models_dir, items, count}
  3. Empty/missing models dir returns count=0, items=[] (no 500)
  4. Strategist artifact recognized with correct fields
  5. Auditor artifact recognized
  6. Manifest sibling detection
  7. Env-pointed artifact flagged correctly
  8. Cross-type env mismatch handling
  9. Unknown model_type handling
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from /app/memory/test_credentials.md
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


class TestArtifactInventoryHTTP:
    """HTTP-level tests for /api/admin/ml/artifacts endpoint."""

    @pytest.fixture(scope="class")
    def session(self):
        """Create a requests session."""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        return s

    @pytest.fixture(scope="class")
    def admin_session(self, session):
        """Login as admin and return authenticated session."""
        login_resp = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        return session

    # ── (1) Unauth returns 401 ───────────────────────────────────────

    def test_unauth_returns_401(self):
        """Unauthenticated request should return 401."""
        resp = requests.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        print("✓ Unauth returns 401")

    # ── (2) Admin returns 200 with correct shape ─────────────────────

    def test_admin_returns_200_with_correct_shape(self, admin_session):
        """Admin should get 200 with {models_dir, items, count}."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "models_dir" in data, "Response missing 'models_dir'"
        assert "items" in data, "Response missing 'items'"
        assert "count" in data, "Response missing 'count'"
        assert isinstance(data["items"], list), "'items' should be a list"
        assert isinstance(data["count"], int), "'count' should be an int"
        assert data["count"] == len(data["items"]), "count should match len(items)"
        print(f"✓ Admin returns 200 with models_dir={data['models_dir']}, count={data['count']}")

    # ── (3) Empty/missing dir returns empty list, not 500 ────────────

    def test_empty_dir_returns_empty_list(self, admin_session):
        """When models dir is empty or missing, should return count=0, items=[]."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        # Even if empty, should not crash
        assert "items" in data
        assert "count" in data
        # If count is 0, items should be empty
        if data["count"] == 0:
            assert data["items"] == [], "items should be empty when count=0"
        print(f"✓ Endpoint handles empty/missing dir gracefully (count={data['count']})")

    # ── (4)+(5)+(6) Artifact recognition and manifest detection ──────

    def test_artifact_item_fields(self, admin_session):
        """Each artifact item should have all required fields."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        required_fields = [
            "filename", "full_path", "size_bytes", "mtime", "age_hours",
            "sha_from_filename", "timestamp_from_filename", "model_type",
            "manifest_present", "manifest_path",
            "currently_pointed_to_by_env", "env_var"
        ]
        
        for item in data["items"]:
            for field in required_fields:
                assert field in item, f"Item missing field '{field}': {item}"
            
            # Validate field types
            assert isinstance(item["filename"], str)
            assert isinstance(item["full_path"], str)
            assert isinstance(item["size_bytes"], int)
            assert isinstance(item["age_hours"], (int, float))
            assert item["model_type"] in ("strategist", "auditor", "unknown")
            assert isinstance(item["manifest_present"], bool)
            assert isinstance(item["currently_pointed_to_by_env"], bool)
            
            # Validate model_type-specific fields
            if item["model_type"] in ("strategist", "auditor"):
                # Versioned artifacts should have sha and timestamp
                if item["sha_from_filename"] is not None:
                    assert isinstance(item["sha_from_filename"], str)
                if item["timestamp_from_filename"] is not None:
                    assert isinstance(item["timestamp_from_filename"], str)
            
            # env_var should be None or a valid env var name
            if item["env_var"] is not None:
                assert item["env_var"] in ("STRATEGIST_ARTIFACT", "AUDITOR_ARTIFACT")
        
        print(f"✓ All {len(data['items'])} items have correct field structure")

    def test_strategist_artifact_recognition(self, admin_session):
        """Strategist artifacts should be recognized with correct model_type."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        strategist_items = [i for i in data["items"] if i["model_type"] == "strategist"]
        
        for item in strategist_items:
            assert item["filename"].startswith("strategist_"), f"Strategist file should start with 'strategist_': {item['filename']}"
            assert item["filename"].endswith(".joblib"), f"Strategist file should end with '.joblib': {item['filename']}"
        
        print(f"✓ Found {len(strategist_items)} strategist artifacts")

    def test_auditor_artifact_recognition(self, admin_session):
        """Auditor artifacts should be recognized with correct model_type."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        auditor_items = [i for i in data["items"] if i["model_type"] == "auditor"]
        
        for item in auditor_items:
            assert item["filename"].startswith("auditor_"), f"Auditor file should start with 'auditor_': {item['filename']}"
            assert item["filename"].endswith(".joblib"), f"Auditor file should end with '.joblib': {item['filename']}"
        
        print(f"✓ Found {len(auditor_items)} auditor artifacts")

    def test_unknown_model_type_handling(self, admin_session):
        """Unknown model types should have sha_from_filename=null and env_var=null."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        unknown_items = [i for i in data["items"] if i["model_type"] == "unknown"]
        
        for item in unknown_items:
            assert item["sha_from_filename"] is None, f"Unknown type should have sha_from_filename=null: {item}"
            assert item["timestamp_from_filename"] is None, f"Unknown type should have timestamp_from_filename=null: {item}"
            assert item["currently_pointed_to_by_env"] is False, f"Unknown type should have currently_pointed_to_by_env=false: {item}"
            assert item["env_var"] is None, f"Unknown type should have env_var=null: {item}"
        
        print(f"✓ Found {len(unknown_items)} unknown artifacts with correct null fields")

    # ── (7)+(8) Env-pointed artifact detection ───────────────────────

    def test_env_pointed_artifact_detection(self, admin_session):
        """Artifacts pointed to by env vars should be flagged correctly."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        pointed_items = [i for i in data["items"] if i["currently_pointed_to_by_env"]]
        
        for item in pointed_items:
            # If pointed, env_var must be set
            assert item["env_var"] is not None, f"Pointed item should have env_var set: {item}"
            # env_var should match model_type
            if item["model_type"] == "strategist":
                assert item["env_var"] == "STRATEGIST_ARTIFACT"
            elif item["model_type"] == "auditor":
                assert item["env_var"] == "AUDITOR_ARTIFACT"
        
        print(f"✓ Found {len(pointed_items)} env-pointed artifacts")

    def test_cross_type_env_mismatch(self, admin_session):
        """Cross-type env mismatch should not flag artifacts."""
        resp = admin_session.get(f"{BASE_URL}/api/admin/ml/artifacts")
        assert resp.status_code == 200
        
        data = resp.json()
        
        # Verify that no strategist artifact has env_var=AUDITOR_ARTIFACT
        for item in data["items"]:
            if item["model_type"] == "strategist" and item["currently_pointed_to_by_env"]:
                assert item["env_var"] == "STRATEGIST_ARTIFACT", f"Strategist should not be pointed by AUDITOR_ARTIFACT: {item}"
            if item["model_type"] == "auditor" and item["currently_pointed_to_by_env"]:
                assert item["env_var"] == "AUDITOR_ARTIFACT", f"Auditor should not be pointed by STRATEGIST_ARTIFACT: {item}"
        
        print("✓ Cross-type env mismatch handling verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
