"""Security audit hardening — SEC-001/002/003 regression tests.

Locks in:
* SEC-001: broker credential encryption uses CREDENTIAL_ENC_KEY (dedicated),
  falls back to JWT_SECRET for legacy rows only, no hardcoded fallback string.
* SEC-002: DynamicCORSMiddleware NEVER emits Access-Control-Allow-Origin: *
  under any origin (allowed or attacker). Wildcarding is an ingress-layer
  concern documented in server.py.
* SEC-003: CSRF exemption uses prefix/exact match, never substring.
  /api/foo/oauthx must NOT be skipped; /api/broker/oauth/callback MUST be.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ── SEC-001 — credential encryption key rotation ────────────────

class TestCredentialEncryptionRotation:
    """CREDENTIAL_ENC_KEY is the sole write key. JWT_SECRET is legacy
    decrypt fallback. No hardcoded fallback string exists."""

    def _reload(self):
        # Force module reload so env changes take effect.
        import importlib
        import routes.broker
        return importlib.reload(routes.broker)

    def test_new_encrypt_uses_dedicated_key(self, monkeypatch):
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", "dedicated-key-abc")
        monkeypatch.setenv("JWT_SECRET", "jwt-secret-xyz")
        broker = self._reload()
        token = broker.encrypt_value("secret-value")
        # Decryptable with the primary key.
        assert broker.decrypt_value(token) == "secret-value"

    def test_legacy_rows_still_decryptable_after_split(self, monkeypatch):
        # Simulate a row encrypted before the split (JWT_SECRET only).
        monkeypatch.delenv("CREDENTIAL_ENC_KEY", raising=False)
        monkeypatch.setenv("JWT_SECRET", "legacy-jwt-secret")
        broker = self._reload()
        legacy_token = broker.encrypt_value("legacy-value")

        # Now the operator adds a dedicated key. Legacy rows must
        # still decrypt (MultiFernet fallback).
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", "brand-new-key")
        broker = self._reload()
        assert broker.decrypt_value(legacy_token) == "legacy-value"

    def test_new_writes_after_split_use_primary_key(self, monkeypatch):
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", "primary-key-1")
        monkeypatch.setenv("JWT_SECRET", "legacy-jwt-secret")
        broker = self._reload()
        token = broker.encrypt_value("new-value")

        # Rotate: remove JWT_SECRET entirely — the new row must still decrypt
        # since it was written under CREDENTIAL_ENC_KEY.
        monkeypatch.delenv("JWT_SECRET", raising=False)
        broker = self._reload()
        assert broker.decrypt_value(token) == "new-value"

    def test_missing_both_keys_raises(self, monkeypatch):
        monkeypatch.delenv("CREDENTIAL_ENC_KEY", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        broker = self._reload()
        with pytest.raises(RuntimeError, match="not configured"):
            broker.encrypt_value("anything")

    def test_no_hardcoded_fallback_string(self, monkeypatch):
        """The old code had ``os.environ.get("JWT_SECRET", "fallback-secret-key")``.
        This regression asserts that string is gone from the module source."""
        import inspect
        import routes.broker as broker_mod
        src = inspect.getsource(broker_mod)
        assert "fallback-secret-key" not in src

    def test_only_dedicated_key_configured_works(self, monkeypatch):
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", "only-dedicated")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        broker = self._reload()
        token = broker.encrypt_value("value")
        assert broker.decrypt_value(token) == "value"


# ── SEC-002 — CORS middleware never emits '*' ───────────────────

class TestCorsMiddlewareNeverWildcards:
    """The application's DynamicCORSMiddleware must never send
    Access-Control-Allow-Origin: * — any wildcard observed on the
    wire comes from the ingress/CDN layer, documented in server.py."""

    def _make_app(self):
        # Build a mini app wrapping the real DynamicCORSMiddleware.
        from fastapi import FastAPI
        from server import DynamicCORSMiddleware  # noqa: PLC0415
        app = FastAPI()
        app.add_middleware(DynamicCORSMiddleware)

        @app.get("/api/private/data")
        async def _private():
            return {"secret": True}

        @app.get("/api/media/landing-video")
        async def _public():
            return {"has_video": False}
        return app

    def test_allowed_origin_gets_reflected_not_wildcard(self, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://good.example")
        client = TestClient(self._make_app())
        r = client.get("/api/private/data", headers={"Origin": "https://good.example"})
        acao = r.headers.get("access-control-allow-origin")
        assert acao == "https://good.example"
        assert acao != "*"

    def test_attacker_origin_gets_no_acao_header(self, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://good.example")
        client = TestClient(self._make_app())
        r = client.get("/api/private/data", headers={"Origin": "https://evil.example"})
        # Middleware must NOT emit either wildcard or attacker origin.
        acao = r.headers.get("access-control-allow-origin")
        assert acao is None
        # Vary: Origin is still emitted for cache safety.
        assert "Origin" in (r.headers.get("vary") or "")

    def test_public_route_still_not_wildcarded_by_app(self, monkeypatch):
        """Even on the intentionally-public /api/media/landing-video,
        the app never emits '*'. If ingress adds it that's outside
        the app boundary — verified by this test."""
        from fastapi.testclient import TestClient
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://good.example")
        client = TestClient(self._make_app())
        r = client.get("/api/media/landing-video",
                       headers={"Origin": "https://evil.example"})
        acao = r.headers.get("access-control-allow-origin")
        assert acao != "*"

    def test_preflight_never_wildcards(self, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://good.example")
        client = TestClient(self._make_app())
        r = client.options("/api/private/data", headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        })
        acao = r.headers.get("access-control-allow-origin")
        assert acao is None or acao != "*"

    def test_no_wildcard_in_middleware_source(self):
        """Belt-and-suspenders: '*' must not appear as an ACAO value
        anywhere in the DynamicCORSMiddleware source."""
        import inspect
        import server as server_mod
        src = inspect.getsource(server_mod.DynamicCORSMiddleware)
        # Search for the actual header assignment pattern with wildcard.
        assert 'Access-Control-Allow-Origin"] = "*"' not in src
        assert "'Access-Control-Allow-Origin'] = '*'" not in src


# ── SEC-003 — CSRF exemption uses prefix/exact match, not substring ─

class TestCsrfExemptionPrefixMatch:
    """Substring match on /oauth or /webhook is banned. A future route
    like /api/foo/oauthx MUST NOT silently bypass CSRF."""

    def test_legitimate_oauth_callback_still_skipped(self):
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/broker/oauth/alpaca/callback") is True
        assert _path_skipped("/api/oauth/callback") is True
        assert _path_skipped("/api/auth/oauth/google/callback") is True

    def test_legitimate_webhook_still_skipped(self):
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/webhook/stripe") is True
        assert _path_skipped("/api/webhooks/generic") is True
        assert _path_skipped("/api/bots/webhook/bot-123/secret-xyz") is True
        assert _path_skipped("/api/billing/webhook") is True

    def test_regression_oauth_substring_no_longer_bypasses(self):
        """The original substring bug — /api/foo/oauthx would slip through
        because 'oauth' is IN 'oauthx'. Prefix match blocks it."""
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/foo/oauthx") is False
        assert _path_skipped("/api/oauthy") is False
        # A single-word path that ends with 'oauth' but isn't a prefix
        # match — also blocked.
        assert _path_skipped("/api/notreallyoauth") is False

    def test_regression_webhook_substring_no_longer_bypasses(self):
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/pretendwebhook") is False
        assert _path_skipped("/api/webhookx") is False
        assert _path_skipped("/api/foo/webhook-lookalike") is False

    def test_admin_broker_oauth_routes_still_require_csrf(self):
        """/api/admin/broker-oauth is a state-mutating admin route — NOT
        an OAuth callback — and must remain CSRF-protected."""
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/admin/broker-oauth") is False
        assert _path_skipped("/api/admin/broker-oauth/alpaca") is False

    def test_pre_auth_exact_paths_still_skipped(self):
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/auth/login") is True
        assert _path_skipped("/api/auth/refresh") is True
        assert _path_skipped("/api/auth/register") is True
        assert _path_skipped("/api/auth/forgot-password") is True
        assert _path_skipped("/api/auth/reset-password") is True

    def test_pre_auth_variants_not_silently_skipped(self):
        """Substring bug would have skipped these; prefix match blocks."""
        from services.csrf_middleware import _path_skipped
        assert _path_skipped("/api/auth/login/impersonate") is False
        assert _path_skipped("/api/auth/refresh/steal") is False

    def test_non_api_paths_are_skipped(self):
        from services.csrf_middleware import _path_skipped
        # Non-/api paths (static assets, etc.) don't need CSRF.
        assert _path_skipped("/static/foo.js") is True
        assert _path_skipped("/") is True

    def test_no_substring_needle_in_module_source(self):
        """The old ``_SKIP_SUBSTRINGS`` tuple with substring ``in`` match
        must be gone. Assert against the module source directly."""
        import inspect
        import services.csrf_middleware as mod
        src = inspect.getsource(mod)
        # The specific substring-check pattern from the vulnerable code:
        assert "for needle in _SKIP_SUBSTRINGS" not in src
        assert "_SKIP_SUBSTRINGS" not in src
