"""Tests for services.csrf_middleware — same-origin CSRF defense.

These tests exercise the middleware against a minimal FastAPI app
so we can lock down every branch of the decision tree without
booting the real server.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.csrf_middleware import (
    CSRFHeaderMiddleware,
    _path_skipped,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFHeaderMiddleware)

    @app.get("/api/anything")
    async def _get():
        return {"ok": True}

    @app.post("/api/anything")
    async def _post():
        return {"ok": True}

    @app.delete("/api/broker/connections/x")
    async def _del():
        return {"deleted": True}

    @app.post("/api/auth/login")
    async def _login():
        return {"user": "ok"}

    @app.post("/api/webhook/stripe")
    async def _webhook():
        return {"received": True}

    @app.get("/api/oauth/callback")
    async def _oauth():
        return {"redirect": "ok"}

    return app


@pytest.fixture(autouse=True)
def _enforce_on():
    prev = os.environ.get("CSRF_ENFORCE")
    os.environ["CSRF_ENFORCE"] = "1"
    yield
    if prev is None:
        os.environ.pop("CSRF_ENFORCE", None)
    else:
        os.environ["CSRF_ENFORCE"] = prev


def test_get_requests_pass_without_header():
    c = TestClient(_make_app())
    r = c.get("/api/anything", cookies={"access_token": "x"})
    assert r.status_code == 200


def test_post_with_cookie_and_header_passes():
    c = TestClient(_make_app())
    r = c.post(
        "/api/anything",
        cookies={"access_token": "x"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200


def test_post_with_cookie_no_header_blocked():
    """The core CSRF case — session cookie present, but no
    X-Requested-With → attacker forge. Must be 403."""
    c = TestClient(_make_app())
    r = c.post("/api/anything", cookies={"access_token": "x"})
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "csrf_header_missing"


def test_post_without_cookie_passes():
    """Bearer-token clients (curl, CLI, service-to-service) have no
    cookie, so CSRF isn't a threat. Middleware falls through."""
    c = TestClient(_make_app())
    r = c.post("/api/anything")
    assert r.status_code == 200


def test_post_with_bearer_auth_passes_without_header():
    """Bearer-auth requests explicitly bypass the CSRF check even
    if a stray cookie is present (mixed-mode integration test)."""
    c = TestClient(_make_app())
    r = c.post(
        "/api/anything",
        cookies={"access_token": "x"},
        headers={"Authorization": "Bearer some.jwt.here"},
    )
    assert r.status_code == 200


def test_delete_with_cookie_no_header_blocked():
    c = TestClient(_make_app())
    r = c.delete("/api/broker/connections/x", cookies={"access_token": "x"})
    assert r.status_code == 403


def test_auth_login_skipped():
    """Pre-auth endpoints must never require the header — the
    session cookie doesn't exist yet."""
    c = TestClient(_make_app())
    r = c.post("/api/auth/login")
    assert r.status_code == 200


def test_webhook_path_skipped():
    """Stripe / broker webhooks authenticate via HMAC signature
    header, not our session cookie. Middleware must not block."""
    c = TestClient(_make_app())
    r = c.post("/api/webhook/stripe")
    assert r.status_code == 200


def test_oauth_callback_path_skipped():
    c = TestClient(_make_app())
    r = c.get("/api/oauth/callback")
    assert r.status_code == 200


def test_non_api_path_skipped():
    """Static frontend assets under non-/api paths bypass entirely.
    We only guard the JSON API surface."""
    app = FastAPI()
    app.add_middleware(CSRFHeaderMiddleware)

    @app.post("/some/other/thing")
    async def _p():
        return {"ok": True}

    c = TestClient(app)
    r = c.post("/some/other/thing", cookies={"access_token": "x"})
    assert r.status_code == 200


def test_options_preflight_passes():
    """OPTIONS is not a mutating method — must fall through so the
    CORS layer above can answer preflight."""
    app = FastAPI()
    app.add_middleware(CSRFHeaderMiddleware)

    @app.post("/api/thing")
    async def _p():
        return {"ok": True}

    c = TestClient(app)
    r = c.options("/api/thing", cookies={"access_token": "x"})
    # FastAPI returns 405 for OPTIONS without an OPTIONS handler,
    # but the key is that we don't return 403 — CORS didn't get
    # short-circuited by us.
    assert r.status_code != 403


def test_shadow_mode_allows_but_logs(caplog):
    with patch.dict(os.environ, {"CSRF_ENFORCE": "0"}):
        c = TestClient(_make_app())
        with caplog.at_level("WARNING"):
            r = c.post("/api/anything", cookies={"access_token": "x"})
    assert r.status_code == 200
    assert any("SHADOW-BLOCK" in rec.message for rec in caplog.records)


def test_path_skipped_helper():
    assert _path_skipped("/api/auth/login")
    assert _path_skipped("/api/broker/oauth/public/callback")
    assert _path_skipped("/api/billing/webhook")
    assert _path_skipped("/static/js/app.js")
    assert not _path_skipped("/api/alpha-daytrader/tick")
    assert not _path_skipped("/api/broker/orders")


def test_header_case_insensitive():
    """HTTP header names are case-insensitive; the middleware must
    accept ``x-requested-with`` from clients that lowercase them."""
    c = TestClient(_make_app())
    r = c.post(
        "/api/anything",
        cookies={"access_token": "x"},
        headers={"x-requested-with": "xmlhttprequest"},
    )
    assert r.status_code == 200
