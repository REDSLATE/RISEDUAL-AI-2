"""Shared test configuration — loads credentials from environment or test_credentials.md"""
import os

def _load_from_env():
    """Load test credentials from environment variables."""
    return {
        "owner_email": os.environ.get("OWNER_EMAIL", ""),
        "owner_password": os.environ.get("OWNER_PASSWORD", ""),
        "admin_email": os.environ.get("ADMIN_EMAIL", ""),
        "admin_password": os.environ.get("ADMIN_PASSWORD", ""),
    }

_creds = _load_from_env()

OWNER_EMAIL = _creds["owner_email"]
OWNER_PASSWORD = _creds["owner_password"]
ADMIN_EMAIL = _creds["admin_email"]
ADMIN_PASSWORD = _creds["admin_password"]

FREE_USER_EMAIL = "freeuser_test@test.com"
FREE_USER_PASSWORD = "Test1234!"

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
