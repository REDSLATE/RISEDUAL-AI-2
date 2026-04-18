"""
Test suite for Forgot Password feature
Tests: POST /api/auth/forgot-password, POST /api/auth/reset-password
"""
import pytest
import requests
import os
import uuid
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, TEST_USER_PASSWORD

# MongoDB connection for direct token verification
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'risedual_db')

TEST_EMAIL_NONEXISTENT = "nonexistent_user_test@example.com"


@pytest.fixture(scope="module")
def mongo_client():
    """MongoDB client for direct database verification"""
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


class TestForgotPasswordEndpoint:
    """Tests for POST /api/auth/forgot-password"""
    
    def test_forgot_password_existing_email_returns_success(self, api_client):
        """Forgot password with existing email should return success message"""
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        
        # Status assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertion - should return generic message (no email enumeration)
        data = response.json()
        assert "message" in data
        assert "reset link" in data["message"].lower() or "email" in data["message"].lower()
        print(f"✓ Forgot password for existing email returned: {data['message']}")
    
    def test_forgot_password_nonexistent_email_returns_same_success(self, api_client):
        """Forgot password with non-existent email should return same message (security)"""
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": TEST_EMAIL_NONEXISTENT
        })
        
        # Status assertion - should still return 200 (no email enumeration)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertion - same generic message
        data = response.json()
        assert "message" in data
        print(f"✓ Forgot password for non-existent email returned: {data['message']}")
    
    def test_forgot_password_creates_token_in_mongodb(self, api_client, mongo_client):
        """Forgot password should create a token document in password_reset_tokens collection"""
        # First, clean up any existing tokens for admin
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        if admin_user:
            mongo_client.password_reset_tokens.delete_many({"user_id": admin_user["_id"]})
        
        # Request password reset
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        assert response.status_code == 200
        
        # Verify token was created in MongoDB
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        assert admin_user is not None, "Admin user should exist"
        
        token_record = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })
        
        assert token_record is not None, "Token record should be created in MongoDB"
        assert "token" in token_record, "Token record should have 'token' field"
        assert "expires_at" in token_record, "Token record should have 'expires_at' field"
        assert token_record["used"] is False, "Token should not be marked as used"
        
        # Verify expiration is ~1 hour from now
        expires_at = token_record["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        time_diff = (expires_at - now).total_seconds()
        assert 3500 < time_diff < 3700, f"Token should expire in ~1 hour, got {time_diff}s"
        
        print(f"✓ Token created in MongoDB with expiration in {time_diff:.0f}s")
        
        # Return token for use in other tests
        return token_record["token"]


class TestResetPasswordEndpoint:
    """Tests for POST /api/auth/reset-password"""
    
    @pytest.fixture
    def valid_reset_token(self, api_client, mongo_client):
        """Generate a valid reset token for testing"""
        # Clean up existing tokens
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        if admin_user:
            mongo_client.password_reset_tokens.delete_many({"user_id": admin_user["_id"]})
        
        # Request new token
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        assert response.status_code == 200
        
        # Get token from MongoDB
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        token_record = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })
        return token_record["token"]
    
    def test_reset_password_with_valid_token(self, api_client, mongo_client, valid_reset_token):
        """Reset password with valid token should succeed"""
        new_password = TEST_USER_PASSWORD
        
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": valid_reset_token,
            "new_password": new_password
        })
        
        # Status assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "message" in data
        assert "success" in data["message"].lower()
        print(f"✓ Password reset successful: {data['message']}")
        
        # Verify token is now marked as used
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        token_record = mongo_client.password_reset_tokens.find_one({"token": valid_reset_token})
        assert token_record["used"] is True, "Token should be marked as used after reset"
        print("✓ Token marked as used in MongoDB")
        
        # Reset password back to original
        mongo_client.password_reset_tokens.delete_many({"user_id": admin_user["_id"]})
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        new_token = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })["token"]
        
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": new_token,
            "new_password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, "Failed to reset password back to original"
        print("✓ Password reset back to original")
    
    def test_reset_password_with_invalid_token(self, api_client):
        """Reset password with invalid token should return 400"""
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": "invalid_token_12345",
            "new_password": "NewPassword123!"
        })
        
        # Status assertion
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "detail" in data
        assert "invalid" in data["detail"].lower() or "expired" in data["detail"].lower()
        print(f"✓ Invalid token correctly rejected: {data['detail']}")
    
    def test_reset_password_with_used_token(self, api_client, mongo_client, valid_reset_token):
        """Reset password with already-used token should return 400"""
        # First use the token
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": valid_reset_token,
            "new_password": "TempPassword123!"
        })
        assert response.status_code == 200, "First reset should succeed"
        
        # Try to use the same token again
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": valid_reset_token,
            "new_password": "AnotherPassword123!"
        })
        
        # Status assertion
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "detail" in data
        print(f"✓ Used token correctly rejected: {data['detail']}")
        
        # Reset password back to original
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        mongo_client.password_reset_tokens.delete_many({"user_id": admin_user["_id"]})
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        new_token = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })["token"]
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": new_token,
            "new_password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
    
    def test_reset_password_with_short_password(self, api_client, mongo_client, valid_reset_token):
        """Reset password with password < 6 chars should return 400"""
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": valid_reset_token,
            "new_password": "12345"  # Only 5 characters
        })
        
        # Status assertion
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "detail" in data
        assert "6 characters" in data["detail"] or "password" in data["detail"].lower()
        print(f"✓ Short password correctly rejected: {data['detail']}")
    
    def test_reset_password_with_expired_token(self, api_client, mongo_client):
        """Reset password with expired token should return 400"""
        # Create an expired token directly in MongoDB
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        expired_token = f"expired_test_token_{uuid.uuid4().hex[:8]}"
        
        # Insert expired token (expired 1 hour ago)
        mongo_client.password_reset_tokens.insert_one({
            "token": expired_token,
            "user_id": admin_user["_id"],
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "used": False
        })
        
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": expired_token,
            "new_password": "NewPassword123!"
        })
        
        # Status assertion
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "detail" in data
        assert "invalid" in data["detail"].lower() or "expired" in data["detail"].lower()
        print(f"✓ Expired token correctly rejected: {data['detail']}")
        
        # Cleanup
        mongo_client.password_reset_tokens.delete_one({"token": expired_token})


class TestLoginAfterPasswordReset:
    """Test that user can login with new password after reset"""
    
    def test_login_with_new_password_after_reset(self, api_client, mongo_client):
        """After password reset, user should be able to login with new password"""
        new_password = TEST_USER_PASSWORD
        
        # Step 1: Request password reset
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        assert response.status_code == 200
        
        # Step 2: Get token from MongoDB
        admin_user = mongo_client.users.find_one({"email": ADMIN_EMAIL})
        token_record = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })
        reset_token = token_record["token"]
        
        # Step 3: Reset password
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": reset_token,
            "new_password": new_password
        })
        assert response.status_code == 200, f"Password reset failed: {response.text}"
        
        # Step 4: Try to login with new password
        response = api_client.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": new_password
        })
        
        # Status assertion
        assert response.status_code == 200, f"Login with new password failed: {response.text}"
        
        # Data assertion
        data = response.json()
        assert "access_token" in data, "Login should return access_token"
        assert data["email"] == ADMIN_EMAIL
        print("✓ Successfully logged in with new password")
        
        # Step 5: Reset password back to original
        mongo_client.password_reset_tokens.delete_many({"user_id": admin_user["_id"]})
        response = api_client.post(f"{BASE_URL}/api/auth/forgot-password", json={
            "email": ADMIN_EMAIL
        })
        new_token = mongo_client.password_reset_tokens.find_one({
            "user_id": admin_user["_id"],
            "used": False
        })["token"]
        
        response = api_client.post(f"{BASE_URL}/api/auth/reset-password", json={
            "token": new_token,
            "new_password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, "Failed to reset password back to original"
        
        # Verify original password works
        response = api_client.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, "Login with original password failed after reset"
        print(f"✓ Password reset back to original: {ADMIN_PASSWORD}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
