"""Tripwire coverage for the Public.com auth-exchange fix (2026-06-03).

RCA:
    Operator hit a 400 ("Connection failed") when entering valid
    Public.com credentials at risedual.ai's broker-connect panel.
    Root cause: ``PublicTradingService`` slapped the operator's
    secret key directly into the Authorization header. Public.com's
    API rejects that with 401 — their flow REQUIRES exchanging the
    secret for a short-lived access token first
    (POST /userapiauthservice/personal/access-tokens).

Doctrine pinned:
    1. ``PublicTradingService`` exposes the exchange endpoint
       constant so the wire contract is auditable.
    2. ``_auth_headers()`` calls the exchange before the first
       Bearer-protected request.
    3. Cached access tokens are reused; ``_REFRESH_SLACK_SECONDS``
       guarantees we never present an expired JWT.
    4. Stale ``self.headers`` attribute (the bug) is gone — no
       method may reference it.
"""
from __future__ import annotations

import inspect
import time
from unittest.mock import MagicMock, patch

from services.broker_service import PublicTradingService


def test_auth_url_is_public_personal_access_tokens():
    assert PublicTradingService.AUTH_URL == (
        "https://api.public.com/userapiauthservice/personal/access-tokens"
    )


def test_no_stale_self_dot_headers_attribute():
    """The original bug: ``self.headers`` was set in __init__ with
    the raw secret as Bearer. Every downstream method referenced it
    directly. The fix replaces every reference with ``_auth_headers()``
    so token refresh is automatic. Regression would silently reintroduce
    the 401 → 400 failure mode."""
    src = inspect.getsource(PublicTradingService)
    assert "self.headers" not in src, (
        "PublicTradingService must not cache 'self.headers' — every "
        "request must call _auth_headers() so token refresh is "
        "automatic (regression of 2026-06-03 Public.com 400 RCA)"
    )


def test_exchange_called_on_first_get_account(monkeypatch):
    """End-to-end: constructing the service and calling get_account
    must POST to AUTH_URL once with the secret, cache the returned
    token, and then GET /trading/account with Bearer <token>."""
    svc = PublicTradingService(
        api_key="my-secret-from-public-portal",
        api_secret="ACCOUNT-ID-123",
    )

    auth_post_calls: list[dict] = []
    account_get_calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        auth_post_calls.append({"url": url, "json": json})
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"accessToken":"the-jwt"}'
        resp.json.return_value = {"accessToken": "the-jwt"}
        resp.raise_for_status.return_value = None
        return resp

    def fake_get(url, headers=None, timeout=None):
        account_get_calls.append({"url": url, "headers": headers})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "accountId": "ACCOUNT-ID-123",
            "cashAvailable": 100.0,
            "buyingPower": 100.0,
            "equity": 100.0,
            "portfolioValue": 100.0,
        }
        resp.raise_for_status.return_value = None
        return resp

    with patch("services.broker_service.requests.post", side_effect=fake_post), \
         patch("services.broker_service.requests.get", side_effect=fake_get):
        account = svc.get_account()

    # Exchange happened exactly once with the right payload.
    assert len(auth_post_calls) == 1
    assert auth_post_calls[0]["url"] == PublicTradingService.AUTH_URL
    assert auth_post_calls[0]["json"]["secret"] == "my-secret-from-public-portal"
    assert "validityInMinutes" in auth_post_calls[0]["json"]

    # The trading call carried the JWT, NOT the raw secret.
    assert len(account_get_calls) == 1
    assert account_get_calls[0]["headers"]["Authorization"] == "Bearer the-jwt"
    assert account_get_calls[0]["headers"]["Authorization"] != \
        "Bearer my-secret-from-public-portal"

    # Sanity: get_account returned a parsed dict.
    assert account is not None
    assert account["id"] == "ACCOUNT-ID-123"
    assert account["cash"] == 100.0


def test_exchange_failure_surfaces_clean_none(monkeypatch):
    """If Public.com rejects the secret (real 401), we must return
    None from get_account — not raise — so the connect route can
    convert this into the 'Could not authenticate' 400."""
    svc = PublicTradingService(api_key="bad-secret", api_secret="acct")

    def fake_post(*_a, **_kw):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = '{"detail":"invalid secret"}'
        resp.content = b'{"detail":"invalid secret"}'
        return resp

    with patch("services.broker_service.requests.post", side_effect=fake_post):
        out = svc.get_account()
    assert out is None


def test_access_token_cached_between_calls():
    """Subsequent calls within the validity window must reuse the
    cached token (no extra exchange POSTs). Critical so we don't
    rate-limit Public's auth endpoint."""
    svc = PublicTradingService(api_key="s", api_secret="a")
    svc._access_token = "cached-jwt"
    svc._access_token_expires_at = time.time() + 600  # 10 min remaining

    auth_calls: list[None] = []

    def fake_post(*_a, **_kw):
        auth_calls.append(None)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"accessToken":"new"}'
        resp.json.return_value = {"accessToken": "new"}
        return resp

    with patch("services.broker_service.requests.post", side_effect=fake_post):
        headers = svc._auth_headers()
    assert headers["Authorization"] == "Bearer cached-jwt"
    assert auth_calls == []  # no exchange triggered


def test_access_token_refreshes_when_expired():
    svc = PublicTradingService(api_key="s", api_secret="a")
    svc._access_token = "old-jwt"
    svc._access_token_expires_at = time.time() - 1  # already expired

    def fake_post(*_a, **_kw):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"accessToken":"fresh"}'
        resp.json.return_value = {"accessToken": "fresh"}
        return resp

    with patch("services.broker_service.requests.post", side_effect=fake_post):
        headers = svc._auth_headers()
    assert headers["Authorization"] == "Bearer fresh"
