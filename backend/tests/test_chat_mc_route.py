"""Tests for the chat ↔ MC slash command dispatcher."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.chat_mc import router as chat_mc_router, set_db


@pytest.fixture
def app_with_router(monkeypatch):
    app = FastAPI()
    app.include_router(chat_mc_router)
    return app


def _mock_owner(monkeypatch):
    from routes import chat_mc as mod
    monkeypatch.setattr(
        mod, "get_current_user",
        AsyncMock(return_value={"role": "owner", "email": "admin@risedual.ai"}),
    )
    monkeypatch.setattr(
        mod, "get_optional_user",
        AsyncMock(return_value={"role": "owner", "email": "admin@risedual.ai"}),
    )


def _mock_non_owner(monkeypatch):
    from routes import chat_mc as mod
    from fastapi import HTTPException

    async def _raise(_req):
        raise HTTPException(status_code=401, detail="not signed in")

    monkeypatch.setattr(mod, "get_current_user", AsyncMock(side_effect=_raise))
    monkeypatch.setattr(mod, "get_optional_user", AsyncMock(return_value=None))


def test_help_card_is_public(app_with_router):
    """`/mc help` returns the catalog without requiring auth."""
    client = TestClient(app_with_router)
    r = client.post("/api/chat/mc/dispatch", json={"command": "help", "args": []})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_help"
    assert any(c["cmd"] == "/mc status" for c in body["commands"])


def test_status_requires_owner(app_with_router, monkeypatch):
    """Non-owners get a graceful card error, not a 4xx, so the chat
    can render a friendly bubble."""
    _mock_non_owner(monkeypatch)
    client = TestClient(app_with_router)
    r = client.post("/api/chat/mc/dispatch", json={"command": "status", "args": []})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_status"
    assert "Owner-only" in body["error"]


def test_status_owner_returns_sidecar_state(app_with_router, monkeypatch):
    """When the sidecar's status() returns a state dict, the route
    flattens it into the chat card shape."""
    _mock_owner(monkeypatch)
    set_db(MagicMock())

    fake_status = {
        "status": "running",
        "heartbeat_task_alive": True,
        "contribution_task_alive": True,
        "watchdog_task_alive": True,
        "last_heartbeat_at": "2026-05-17T12:00:00+00:00",
        "last_contribution_at": "2026-05-17T12:00:00+00:00",
        "last_error": None,
        "watchdog": {"liveness_age_s": 5.4},
        "identity": {"name": "alpha", "version": "1.6"},
    }
    with patch("services.mc_sidecar.status", new=AsyncMock(return_value=fake_status)):
        client = TestClient(app_with_router)
        r = client.post(
            "/api/chat/mc/dispatch",
            json={"command": "status", "args": []},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_status"
    assert body["running"] is True
    assert body["heartbeat_alive"] is True
    assert body["liveness_age_s"] == pytest.approx(5.4)
    set_db(None)  # cleanup module state


def test_mirror_unconfigured_when_base_unset(app_with_router, monkeypatch):
    """No MC_BASE_URL → return a card error explaining why."""
    _mock_owner(monkeypatch)
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    client = TestClient(app_with_router)
    r = client.post("/api/chat/mc/dispatch", json={"command": "mirror", "args": []})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_mirror"
    assert "MC_BASE_URL" in body["error"]


def test_intents_empty_when_collection_missing(app_with_router, monkeypatch):
    """A fresh db with no audit collection still returns a structured
    empty card with the doctrine note attached."""
    _mock_owner(monkeypatch)
    monkeypatch.delenv("RISEDUAL_EMIT_INTENTS_TO_MC", raising=False)

    # Fake motor-like collection that yields no docs.
    class _Cursor:
        def sort(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def __aiter__(self):
            async def gen():
                for _ in ():
                    yield _
            return gen()

    fake_db = MagicMock()
    fake_db.__getitem__.return_value.find.return_value = _Cursor()
    set_db(fake_db)

    client = TestClient(app_with_router)
    r = client.post("/api/chat/mc/dispatch", json={"command": "intents", "args": []})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_intents"
    assert body["count"] == 0
    assert body["emission_enabled"] is False
    assert "off" in body["note"].lower()
    set_db(None)


def test_opine_rejects_bad_symbol(app_with_router, monkeypatch):
    """Symbol that doesn't match the strict regex → card error."""
    _mock_owner(monkeypatch)
    client = TestClient(app_with_router)
    r = client.post(
        "/api/chat/mc/dispatch",
        json={"command": "opine", "args": ["not-a-ticker!"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_opine"
    assert "invalid symbol" in body["error"].lower()


def test_opine_requires_login(app_with_router, monkeypatch):
    """Anonymous users get a friendly sign-in nudge, not a 401."""
    _mock_non_owner(monkeypatch)
    client = TestClient(app_with_router)
    r = client.post(
        "/api/chat/mc/dispatch",
        json={"command": "opine", "args": ["NVDA"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_opine"
    assert "Sign in" in body["error"]


def test_unknown_command_falls_back_to_help(app_with_router):
    """Garbage verbs return the help card with an error hint."""
    client = TestClient(app_with_router)
    r = client.post(
        "/api/chat/mc/dispatch",
        json={"command": "foobar", "args": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_help"
    assert "Unknown command" in body["error"]


def test_recent_intents_get_wrapper(app_with_router, monkeypatch):
    """GET /intents/recent is the same payload as POST dispatch
    `intents` — used by the right pane on first render."""
    _mock_owner(monkeypatch)
    monkeypatch.setenv("RISEDUAL_EMIT_INTENTS_TO_MC", "1")

    class _Cursor:
        def sort(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def __aiter__(self):
            async def gen():
                for _ in ():
                    yield _
            return gen()

    fake_db = MagicMock()
    fake_db.__getitem__.return_value.find.return_value = _Cursor()
    set_db(fake_db)

    client = TestClient(app_with_router)
    r = client.get("/api/chat/mc/intents/recent?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "mc_intents"
    assert body["count"] == 0
    assert body["emission_enabled"] is True
    set_db(None)
