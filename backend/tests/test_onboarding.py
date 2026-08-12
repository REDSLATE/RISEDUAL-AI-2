"""Post-signup onboarding endpoints."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


class _FakeUsers:
    def __init__(self):
        self.docs: dict = {}

    async def find_one(self, filt, _proj=None):
        # Accept queries by either _id or email.
        if "_id" in filt:
            for d in self.docs.values():
                if str(d.get("_id")) == str(filt["_id"]):
                    return d
        elif "email" in filt:
            return self.docs.get(filt["email"])
        return None

    async def update_one(self, filt, update):
        for email, d in list(self.docs.items()):
            if str(d.get("_id")) == str(filt.get("_id")):
                d.update(update.get("$set", {}))
                return MagicMock(matched_count=1, modified_count=1)
        return MagicMock(matched_count=0, modified_count=0)


class _EmptyCursor:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeBrokerConnections:
    def __init__(self, rows=None):
        self.rows = rows or []

    def find(self, _q, _proj=None):
        rows = list(self.rows)

        class _Iter:
            def __aiter__(self_inner):
                return self_inner

            async def __anext__(self_inner):
                if not rows:
                    raise StopAsyncIteration
                return rows.pop(0)
        return _Iter()


class _FakeDB:
    def __init__(self, users, connections=None):
        self.users = users
        self.broker_connections = connections or _FakeBrokerConnections()


@pytest.fixture()
def wire_fake_db(monkeypatch):
    """Replace the onboarding module's db + auth's get_current_user
    so the endpoints hit an in-memory user store."""
    from bson import ObjectId
    from routes import onboarding as onboarding_mod

    uid = ObjectId()
    users = _FakeUsers()
    users.docs["u@example.com"] = {
        "_id": uid,
        "email": "u@example.com",
        "name": "Fresh Trader",
        "role": "user",
        "onboarding_completed": False,
    }
    fake_db = _FakeDB(users)
    monkeypatch.setattr(onboarding_mod, "_db", fake_db)

    async def _fake_user(_req):
        return users.docs["u@example.com"]

    monkeypatch.setattr(onboarding_mod, "get_current_user", _fake_user)
    return fake_db, str(uid)


@pytest.mark.asyncio
async def test_status_reports_not_completed_and_no_brokers(wire_fake_db):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/onboarding/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["completed"] is False
    assert data["brokers"]["public_connected"] is False
    assert data["brokers"]["moomoo_connected"] is False
    assert data["brokers"]["any_connected"] is False


@pytest.mark.asyncio
async def test_status_reports_connected_brokers(wire_fake_db, monkeypatch):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    from routes import onboarding as onboarding_mod
    fake_db, _ = wire_fake_db
    fake_db.broker_connections = _FakeBrokerConnections([
        {"broker_id": "public", "status": "connected"},
        {"broker_id": "moomoo", "status": "active"},
    ])
    monkeypatch.setattr(onboarding_mod, "_db", fake_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/onboarding/status")
    data = r.json()
    assert data["brokers"]["public_connected"] is True
    assert data["brokers"]["moomoo_connected"] is True
    assert data["brokers"]["any_connected"] is True


@pytest.mark.asyncio
async def test_complete_marks_user_and_is_idempotent(wire_fake_db):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    fake_db, uid = wire_fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.post("/api/onboarding/complete",
                             json={"stage": "brokers_connected"})
        r2 = await ac.post("/api/onboarding/complete",
                             json={"stage": "brokers_connected"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["completed"] is True
    # User doc was updated.
    doc = fake_db.users.docs["u@example.com"]
    assert doc["onboarding_completed"] is True
    assert doc["onboarding_stage"] == "brokers_connected"
    assert "onboarding_completed_at" in doc


@pytest.mark.asyncio
async def test_complete_defaults_stage_when_body_empty(wire_fake_db):
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    fake_db, _ = wire_fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/onboarding/complete", json={})
    assert r.status_code == 200
    doc = fake_db.users.docs["u@example.com"]
    assert doc["onboarding_stage"] == "completed"


@pytest.mark.asyncio
async def test_status_survives_broker_read_failure(wire_fake_db, monkeypatch):
    """If the broker_connections read blows up, onboarding must still open."""
    os.environ.setdefault("JWT_SECRET", "test-secret")
    from server import app
    from routes import onboarding as onboarding_mod
    fake_db, _ = wire_fake_db

    class _Explode:
        def find(self, *_a, **_kw):
            raise RuntimeError("mongo down")
    fake_db.broker_connections = _Explode()
    monkeypatch.setattr(onboarding_mod, "_db", fake_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/onboarding/status")
    assert r.status_code == 200
    data = r.json()
    assert data["brokers"]["any_connected"] is False
    assert data["brokers"]["connections"] == []
