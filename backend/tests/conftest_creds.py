"""Shared test configuration — loads credentials from environment or defaults.
All test files must import credentials from here instead of hardcoding them."""
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")

OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "managingdirector@redslateholdings.com")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "RedSlate2026!")

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@risedual.ai")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "RiseDual2026!")

FREE_USER_EMAIL = os.environ.get("FREE_USER_EMAIL", "freeuser_test@test.com")
FREE_USER_PASSWORD = os.environ.get("FREE_USER_PASSWORD", "Test1234!")
