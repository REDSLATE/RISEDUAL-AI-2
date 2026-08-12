"""Google (Emergent) auth session-exchange endpoint tests.

We mock the outbound HTTP call to Emergent's /session-data endpoint so
the tests are hermetic. The endpoint under test upserts users, issues
first-party JWT cookies, and returns the same shape as email/password
login.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture()
def emergent_ok_response():
    m = MagicMock()
    m.status_code = 200
    m.json = MagicMock(return_value={
        "id": "google-sub-abc123",
        "email": "someone@example.com",
        "name": "Some One",
        "picture": "https://example.com/pic.png",
        "session_token": "emergent-session-tok",
    })
    return m


@pytest.fixture()
def emergent_401_response():
    m = MagicMock()
    m.status_code = 401
    m.json = MagicMock(return_value={"detail": "invalid session"})
    return m


class _FakeAsyncClient:
    def __init__(self, response, **_kw):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _url, headers=None):
        return self._response


@pytest.mark.asyncio
async def test_missing_session_id_returns_400():
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/auth/google/session")
    assert r.status_code == 400
    assert "session_id" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invalid_session_id_returns_401(emergent_401_response):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    transport = ASGITransport(app=app)
    with patch("routes.auth.httpx.AsyncClient",
               lambda **kw: _FakeAsyncClient(emergent_401_response, **kw)):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/auth/google/session",
                headers={"X-Session-ID": "bogus"},
            )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_new_user_upsert_and_jwt_issued(emergent_ok_response, monkeypatch):
    """First-time Google sign-in creates a user, returns JWT, marks is_new_user."""
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    from routes import auth as auth_mod

    # Fake mongo layer scoped to this test.
    fake_users_state: dict = {}

    class _FakeCol:
        async def find_one(self, q, *_a, **_kw):
            email = q.get("email")
            return fake_users_state.get(email)

        async def insert_one(self, doc):
            fake_users_state[doc["email"]] = {**doc, "_id": "fake-id-xyz"}
            m = MagicMock()
            m.inserted_id = "fake-id-xyz"
            return m

        async def update_one(self, *_a, **_kw):
            m = MagicMock()
            m.matched_count = 1
            return m

    class _FakeDB:
        users = _FakeCol()

    monkeypatch.setattr(auth_mod, "db", _FakeDB())
    # Neutralize the signup-credit grant so we don't touch the real
    # credit service in this test.
    monkeypatch.setattr(
        "services.credit_service.grant_signup_bonus",
        AsyncMock(return_value=None),
    )
    transport = ASGITransport(app=app)
    with patch("routes.auth.httpx.AsyncClient",
               lambda **kw: _FakeAsyncClient(emergent_ok_response, **kw)):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/auth/google/session",
                headers={"X-Session-ID": "good-session"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "someone@example.com"
    assert body["is_new_user"] is True
    assert body["access_token"]
    assert body["refresh_token"]
    # Verify a user row was created.
    assert "someone@example.com" in fake_users_state
    created = fake_users_state["someone@example.com"]
    assert created["auth_provider"] == "google"
    assert created["google_id"] == "google-sub-abc123"


@pytest.mark.asyncio
async def test_existing_user_by_email_is_linked_not_duplicated(
    emergent_ok_response, monkeypatch,
):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    from routes import auth as auth_mod
    from bson import ObjectId

    existing_id = ObjectId()
    fake_users_state = {
        "someone@example.com": {
            "_id": existing_id,
            "email": "someone@example.com",
            "name": "Legacy Name",
            "role": "admin",
            "subscription_status": "pro",
            "password_hash": "$2b$…",
        },
    }

    class _FakeCol:
        async def find_one(self, q, *_a, **_kw):
            email = q.get("email")
            return fake_users_state.get(email)

        async def update_one(self, filt, update):
            # Merge $set into the stored doc.
            for email, doc in list(fake_users_state.items()):
                if doc["_id"] == filt.get("_id"):
                    doc.update(update.get("$set", {}))
                    break
            m = MagicMock()
            m.matched_count = 1
            return m

        async def insert_one(self, doc):
            raise AssertionError("must NOT insert — user already exists")

    class _FakeDB:
        users = _FakeCol()

    monkeypatch.setattr(auth_mod, "db", _FakeDB())
    monkeypatch.setattr(
        "services.credit_service.grant_signup_bonus",
        AsyncMock(side_effect=AssertionError("signup bonus must not fire on link")),
    )
    transport = ASGITransport(app=app)
    with patch("routes.auth.httpx.AsyncClient",
               lambda **kw: _FakeAsyncClient(emergent_ok_response, **kw)):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/auth/google/session",
                headers={"X-Session-ID": "good-session"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_new_user"] is False
    # Existing role must be preserved (never demoted by Google login).
    assert body["role"] == "admin"
    assert body["subscription_status"] == "pro"
    linked = fake_users_state["someone@example.com"]
    assert linked["google_id"] == "google-sub-abc123"
    # password_hash preserved for future email/password login.
    assert linked["password_hash"] == "$2b$…"


@pytest.mark.asyncio
async def test_missing_email_from_provider_returns_400(monkeypatch):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app

    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.json = MagicMock(return_value={"id": "x", "email": ""})
    transport = ASGITransport(app=app)
    with patch("routes.auth.httpx.AsyncClient",
               lambda **kw: _FakeAsyncClient(bad_resp, **kw)):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/auth/google/session",
                headers={"X-Session-ID": "good"},
            )
    assert r.status_code == 400
    assert "email" in r.json()["detail"].lower()
