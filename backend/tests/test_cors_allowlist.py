"""Tests for CORS allowlist middleware (SEC-001 fix, 2026-02).

Guardrails:
* Only allowlisted origins get ``Access-Control-Allow-*`` headers
* Non-allowlisted origins get NO Allow-Origin (browser rejects)
* Requests with no ``Origin`` header (server-to-server) are unaffected
* ``Vary: Origin`` always emitted so shared caches don't leak headers
* Preflight (OPTIONS) mirrors the same allowlist behaviour
* Localhost is off by default and only on with ``CORS_ALLOW_LOCALHOST=1``
* Env is re-read each request so runtime changes take effect
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    """Small isolated app that mounts ONLY the CORS middleware."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://algo-trader-ai-1.emergent.host,https://risedual-trading.preview.emergentagent.com",
    )
    monkeypatch.delenv("CORS_ALLOW_LOCALHOST", raising=False)
    # Import inside fixture so env is captured on first use.
    # Reload the module so the middleware picks up fresh env.
    import importlib
    import server as server_mod  # noqa: F401  — trigger app import
    from server import DynamicCORSMiddleware  # class factory
    test_app = FastAPI()

    @test_app.get("/api/ping")
    def ping():
        return {"pong": True}

    @test_app.post("/api/mutate")
    def mutate():
        return {"ok": True}

    test_app.add_middleware(DynamicCORSMiddleware)
    return test_app


def _client(app):
    return TestClient(app)


# ─── allowlist enforcement ──────────────────────────────────────


def test_allowlisted_origin_gets_cors_headers(app):
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "https://algo-trader-ai-1.emergent.host"},
    )
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == "https://algo-trader-ai-1.emergent.host"
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"
    assert "Origin" in (r.headers.get("Vary") or "")


def test_non_allowlisted_origin_gets_no_cors_headers(app):
    """This is the SEC-001 fix — evil.com must NOT get Allow-Origin."""
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "https://evil.com"},
    )
    assert r.status_code == 200  # request still succeeds; browser
                                  # rejects on missing header
    assert "Access-Control-Allow-Origin" not in r.headers
    assert "Access-Control-Allow-Credentials" not in r.headers


def test_no_origin_header_is_unaffected(app):
    """Server-to-server / curl calls with no Origin header still work."""
    r = _client(app).get("/api/ping")
    assert r.status_code == 200
    assert "Access-Control-Allow-Origin" not in r.headers


def test_trailing_slash_in_origin_is_normalised(app):
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "https://algo-trader-ai-1.emergent.host/"},
    )
    # Reflected value keeps the incoming form (browsers won't send
    # trailing slash normally; if they do, we still recognise it).
    assert r.headers.get("Access-Control-Allow-Origin") is not None


# ─── preflight (OPTIONS) ────────────────────────────────────────


def test_preflight_allowlisted_returns_204_with_headers(app):
    r = _client(app).options(
        "/api/mutate",
        headers={
            "Origin": "https://algo-trader-ai-1.emergent.host",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "https://algo-trader-ai-1.emergent.host"
    assert r.headers["Access-Control-Allow-Credentials"] == "true"
    assert "POST" in r.headers["Access-Control-Allow-Methods"]
    assert "Origin" in r.headers["Vary"]


def test_preflight_disallowed_origin_gets_no_allow_origin(app):
    r = _client(app).options(
        "/api/mutate",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Preflight still returns 204 but WITHOUT the Allow-Origin
    # header → browser refuses to send the actual request.
    assert r.status_code == 204
    assert "Access-Control-Allow-Origin" not in r.headers
    assert "Access-Control-Allow-Credentials" not in r.headers
    # Vary must still be present for cache correctness
    assert r.headers.get("Vary") == "Origin"


# ─── localhost dev mode ──────────────────────────────────────────


def test_localhost_disabled_by_default(app, monkeypatch):
    # No CORS_ALLOW_LOCALHOST — localhost should be rejected
    monkeypatch.delenv("CORS_ALLOW_LOCALHOST", raising=False)
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "http://localhost:3000"},
    )
    assert "Access-Control-Allow-Origin" not in r.headers


def test_localhost_allowed_when_flag_set(app, monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_LOCALHOST", "1")
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "http://localhost:3000"},
    )
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


def test_localhost_ipv4_allowed_when_flag_set(app, monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_LOCALHOST", "1")
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    assert r.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:3000"


def test_localhost_flag_does_not_open_other_origins(app, monkeypatch):
    """Setting CORS_ALLOW_LOCALHOST=1 must not incidentally allow
    other origins."""
    monkeypatch.setenv("CORS_ALLOW_LOCALHOST", "1")
    r = _client(app).get(
        "/api/ping",
        headers={"Origin": "https://evil.com"},
    )
    assert "Access-Control-Allow-Origin" not in r.headers


# ─── FRONTEND_URL fallback ──────────────────────────────────────


def test_frontend_url_fallback_when_allowlist_env_absent(monkeypatch):
    """If CORS_ALLOWED_ORIGINS is unset, FRONTEND_URL still works
    (backwards-compat with older deploys)."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://only-this.example")
    from server import DynamicCORSMiddleware
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"pong": True}

    app.add_middleware(DynamicCORSMiddleware)
    r = TestClient(app).get(
        "/api/ping",
        headers={"Origin": "https://only-this.example"},
    )
    assert r.headers.get("Access-Control-Allow-Origin") == "https://only-this.example"


# ─── Vary header ────────────────────────────────────────────────


def test_vary_origin_always_present(app):
    """Vary: Origin must be on every response, allowed or not, so
    shared caches don't cross-contaminate origin-specific headers."""
    for origin in ("https://algo-trader-ai-1.emergent.host",
                    "https://evil.com",
                    ""):
        headers = {"Origin": origin} if origin else {}
        r = _client(app).get("/api/ping", headers=headers)
        assert "Origin" in (r.headers.get("Vary") or ""), (
            f"missing Vary: Origin for origin={origin!r}"
        )
