"""Public-access middleware — contract tests.

Pins:
  * Default state is OPEN (PUBLIC_ACCESS_ENABLED missing → true).
  * /api/health, /api/auth/*, /api/system/access ALWAYS reachable
    even with the lockout on.
  * Non-/api/* paths pass through (frontend must render its own
    lockout view).
  * Admin emails (per ADMIN_EMAILS allowlist) bypass the lockout.
  * Anonymous traffic to other /api/* paths gets 503 with stable
    JSON shape.
  * Middleware FAILS OPEN on internal errors (DB hiccup → no hard
    lock-out).
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.public_access_middleware import (
    PublicAccessMiddleware,
    _admin_email_allowlist,
    _path_is_bypassed,
)


# ── _path_is_bypassed unit tests ──────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("/api/health", True),
    ("/api/health/db", True),
    ("/api/auth/login", True),
    ("/api/auth/refresh", True),
    ("/api/auth/me", True),
    ("/api/system/access", True),
    ("/api/system/status", False),
    ("/api/admin/ml/v2/safety/heartbeat", False),
    ("/api/dashboard", False),
    ("/api/users/me", False),
])
def test_path_bypass_table(path, expected):
    assert _path_is_bypassed(path) is expected


# ── ADMIN_EMAILS allowlist ────────────────────────────────────────────────────


def test_admin_allowlist_default(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert "admin@risedual.ai" in _admin_email_allowlist()


def test_admin_allowlist_comma_separated(monkeypatch):
    monkeypatch.setenv(
        "ADMIN_EMAILS",
        "alice@example.com, BOB@Example.com ,carol@example.com",
    )
    al = _admin_email_allowlist()
    assert "alice@example.com" in al
    assert "bob@example.com" in al  # lowercased
    assert "carol@example.com" in al


# ── End-to-end middleware contract ────────────────────────────────────────────


def _make_app() -> FastAPI:
    """Synthetic FastAPI with the middleware mounted + a couple of
    representative endpoints."""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/auth/me")
    async def auth_me():
        return {"user": "stub"}

    @app.get("/api/system/access")
    async def system_access():
        return {"public_access": True, "is_admin": False}

    @app.get("/api/dashboard")
    async def dashboard():
        return {"data": "secret"}

    @app.get("/")
    async def root():
        return {"app": "frontend"}

    app.add_middleware(PublicAccessMiddleware)
    return app


@pytest.fixture
def app_locked(monkeypatch):
    """App with the lockout flipped ON via the resolver mock."""
    async def _locked():
        return False
    monkeypatch.setattr(
        "routes.system_access.is_public_access_enabled", _locked,
    )
    return _make_app()


@pytest.fixture
def app_open(monkeypatch):
    """App with the lockout OFF — every request passes through."""
    async def _open():
        return True
    monkeypatch.setattr(
        "routes.system_access.is_public_access_enabled", _open,
    )
    return _make_app()


def _admin_jwt(monkeypatch, email: str = "admin@risedual.ai") -> str:
    """Mint an access JWT that the middleware will accept."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use")
    return jwt.encode(
        {"sub": "x", "email": email, "type": "access"},
        "test-secret-do-not-use",
        algorithm="HS256",
    )


def test_open_state_passes_anonymous_traffic(app_open):
    client = TestClient(app_open)
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    assert r.json() == {"data": "secret"}


def test_locked_state_blocks_anonymous_dashboard(app_locked):
    client = TestClient(app_locked)
    r = client.get("/api/dashboard")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "service_unavailable"
    assert body["reason"] == "scheduled_maintenance"
    assert "message" in body


def test_locked_state_allows_health(app_locked):
    client = TestClient(app_locked)
    r = client.get("/api/health")
    assert r.status_code == 200


def test_locked_state_allows_auth_endpoints(app_locked):
    client = TestClient(app_locked)
    r = client.get("/api/auth/me")
    assert r.status_code == 200


def test_locked_state_allows_system_access(app_locked):
    client = TestClient(app_locked)
    r = client.get("/api/system/access")
    assert r.status_code == 200


def test_locked_state_passes_frontend_assets(app_locked):
    """Non-/api/* paths must pass through so the SPA can render
    its own lockout page."""
    client = TestClient(app_locked)
    r = client.get("/")
    assert r.status_code == 200


def test_admin_jwt_bypasses_lockout(app_locked, monkeypatch):
    token = _admin_jwt(monkeypatch)
    client = TestClient(app_locked)
    r = client.get(
        "/api/dashboard",
        cookies={"access_token": token},
    )
    assert r.status_code == 200


def test_admin_via_authorization_header_bypasses_lockout(
    app_locked, monkeypatch,
):
    token = _admin_jwt(monkeypatch)
    client = TestClient(app_locked)
    r = client.get(
        "/api/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_non_admin_jwt_does_not_bypass(app_locked, monkeypatch):
    """A JWT for an email NOT in the allowlist is still locked
    out — even if it's a perfectly valid token."""
    token = _admin_jwt(monkeypatch, email="random@example.com")
    client = TestClient(app_locked)
    r = client.get(
        "/api/dashboard",
        cookies={"access_token": token},
    )
    assert r.status_code == 503


def test_invalid_jwt_does_not_bypass(app_locked, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use")
    client = TestClient(app_locked)
    r = client.get(
        "/api/dashboard",
        cookies={"access_token": "garbage.token.value"},
    )
    assert r.status_code == 503


def test_refresh_token_does_not_bypass(app_locked, monkeypatch):
    """Refresh tokens (type='refresh') must NOT count as admin
    auth — only access tokens carry the admin email claim."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use")
    token = jwt.encode(
        {"sub": "x", "email": "admin@risedual.ai", "type": "refresh"},
        "test-secret-do-not-use",
        algorithm="HS256",
    )
    client = TestClient(app_locked)
    r = client.get(
        "/api/dashboard",
        cookies={"access_token": token},
    )
    assert r.status_code == 503


# ── Fail-open behaviour ───────────────────────────────────────────────────────


def test_resolver_exception_fails_open(monkeypatch):
    """If ``is_public_access_enabled`` itself raises (e.g., DB
    hiccup), the middleware must FAIL OPEN — never hard-lock the
    site on transient infrastructure errors."""
    async def _raising():
        raise RuntimeError("synthetic DB failure")

    monkeypatch.setattr(
        "routes.system_access.is_public_access_enabled", _raising,
    )
    app = _make_app()
    client = TestClient(app)
    r = client.get("/api/dashboard")
    # Fail OPEN — request passes through.
    assert r.status_code == 200


# ── env_default fallback ──────────────────────────────────────────────────────


def test_env_default_truthy_tokens(monkeypatch):
    """Verify the env-default helper accepts the stable truthy set."""
    from routes.system_access import _env_default
    for tok in ("true", "TRUE", "1", "yes", "ON"):
        monkeypatch.setenv("PUBLIC_ACCESS_ENABLED", tok)
        assert _env_default() is True
    for tok in ("false", "0", "no", "off", "anything_else"):
        monkeypatch.setenv("PUBLIC_ACCESS_ENABLED", tok)
        assert _env_default() is False


def test_env_default_missing_is_open(monkeypatch):
    """Default must be OPEN so dev/preview keep working without
    explicit configuration."""
    from routes.system_access import _env_default
    monkeypatch.delenv("PUBLIC_ACCESS_ENABLED", raising=False)
    assert _env_default() is True


# ── 503 body shape (frontend pattern-matches) ─────────────────────────────────


def test_503_body_is_stable_json(app_locked):
    client = TestClient(app_locked)
    r = client.get("/api/dashboard")
    assert r.status_code == 503
    body = r.json()
    # Stable keys the frontend pattern-matches on.
    assert set(body.keys()) >= {"error", "reason", "message"}
    assert body["error"] == "service_unavailable"
    assert body["reason"] == "scheduled_maintenance"
    assert isinstance(body["message"], str)
    assert len(body["message"]) > 0
