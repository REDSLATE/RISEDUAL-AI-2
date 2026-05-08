"""Shared test configuration — loads credentials from environment or defaults.
All test files must import credentials from here instead of hardcoding them.

As of Feb 2026 the historical Red Slate owner account was removed (see
`seed_admin()` cleanup in routes/auth.py). Owner and admin now refer to the
SAME unified `admin@risedual.ai` account. Both aliases remain so any legacy
test that imports `OWNER_EMAIL` still works.
"""
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@risedual.ai")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "RiseDual2026!")

# Legacy alias — owner and admin are the same unified account now.
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", ADMIN_EMAIL)
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", ADMIN_PASSWORD)

FREE_USER_EMAIL = os.environ.get("FREE_USER_EMAIL", "freeuser_test@test.com")
FREE_USER_PASSWORD = os.environ.get("FREE_USER_PASSWORD", "Test1234!")

# Ephemeral test user password for registration tests
TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "TestPass2026!")

# Dummy broker test fixtures (not real secrets)
TEST_BROKER_CLIENT_ID = os.environ.get("TEST_BROKER_CLIENT_ID", "TEST_CLIENT_ID_12345678")
TEST_BROKER_CLIENT_SECRET = os.environ.get("TEST_BROKER_CLIENT_SECRET", "TEST_CLIENT_SECRET_ABCDEFGH")
