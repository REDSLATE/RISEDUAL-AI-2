"""Tests for trading-mode guards — require_live_mode / require_paper_mode.

These guards live at every order-execution route and reject requests
that bypass the navbar pill (direct API calls, stale frontend caches).

The guard returns the user dict on success and raises HTTPException
with a structured ``detail.code = "wrong_mode"`` on mismatch so the
UI can branch on the code without parsing free-form messages.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient

import routes.auth as auth_module
from services.trading_mode_guards import require_live_mode, require_paper_mode
from services.trading_mode_service import set_db as set_tm_db


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    test_db = client[os.environ["DB_NAME"] + "_test_trading_mode_guards"]
    await test_db.users.delete_many({})
    set_tm_db(test_db)
    yield test_db
    await test_db.users.delete_many({})
    client.close()


async def _make_user(db, mode: str = "paper") -> str:
    res = await db.users.insert_one({
        "email": f"trader-{mode}@example.com",
        "password_hash": "x",
        "name": f"Trader-{mode}",
        "role": "user",
        "trading_mode": mode,
        "created_at": datetime.now(timezone.utc),
    })
    return str(res.inserted_id)


def _fake_request(user_id: str) -> Request:
    """Build the most stripped-down Request object the guards need.

    The guards call ``get_current_user(request)`` first, so we monkey-
    patch that in each test to return the right user dict — the
    Request object itself never gets touched.
    """
    return Request({
        "type": "http", "method": "GET", "path": "/", "headers": [],
        "query_string": b"", "client": ("test", 0),
    })


@pytest.mark.asyncio
async def test_require_live_passes_for_live_user(db, monkeypatch):
    user_id = await _make_user(db, mode="live")
    user_doc = {"_id": ObjectId(user_id), "email": "trader-live@example.com"}

    async def fake(_req):
        return user_doc

    monkeypatch.setattr(auth_module, "get_current_user", fake)
    # Re-import the guards module so it picks up the patched reference
    import services.trading_mode_guards as g
    monkeypatch.setattr(g, "get_current_user", fake)

    result = await require_live_mode(_fake_request(user_id))
    assert result is user_doc


@pytest.mark.asyncio
async def test_require_live_rejects_paper_user(db, monkeypatch):
    user_id = await _make_user(db, mode="paper")
    user_doc = {"_id": ObjectId(user_id), "email": "trader-paper@example.com"}

    async def fake(_req):
        return user_doc

    import services.trading_mode_guards as g
    monkeypatch.setattr(g, "get_current_user", fake)

    with pytest.raises(HTTPException) as exc:
        await require_live_mode(_fake_request(user_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "wrong_mode"
    assert exc.value.detail["required_mode"] == "live"
    assert exc.value.detail["current_mode"] == "paper"


@pytest.mark.asyncio
async def test_require_paper_passes_for_paper_user(db, monkeypatch):
    user_id = await _make_user(db, mode="paper")
    user_doc = {"_id": ObjectId(user_id), "email": "trader-paper@example.com"}

    async def fake(_req):
        return user_doc

    import services.trading_mode_guards as g
    monkeypatch.setattr(g, "get_current_user", fake)

    result = await require_paper_mode(_fake_request(user_id))
    assert result is user_doc


@pytest.mark.asyncio
async def test_require_paper_rejects_live_user(db, monkeypatch):
    user_id = await _make_user(db, mode="live")
    user_doc = {"_id": ObjectId(user_id), "email": "trader-live@example.com"}

    async def fake(_req):
        return user_doc

    import services.trading_mode_guards as g
    monkeypatch.setattr(g, "get_current_user", fake)

    with pytest.raises(HTTPException) as exc:
        await require_paper_mode(_fake_request(user_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "wrong_mode"
    assert exc.value.detail["required_mode"] == "paper"
    assert exc.value.detail["current_mode"] == "live"


@pytest.mark.asyncio
async def test_default_mode_is_paper_so_live_guard_rejects_new_users(db, monkeypatch):
    """A brand-new user with no trading_mode field defaults to paper.
    The live guard must reject them."""
    res = await db.users.insert_one({
        "email": "fresh@example.com",
        "password_hash": "x",
        "role": "user",
        "created_at": datetime.now(timezone.utc),
    })
    user_id = str(res.inserted_id)
    user_doc = {"_id": res.inserted_id, "email": "fresh@example.com"}

    async def fake(_req):
        return user_doc

    import services.trading_mode_guards as g
    monkeypatch.setattr(g, "get_current_user", fake)

    with pytest.raises(HTTPException) as exc:
        await require_live_mode(_fake_request(user_id))
    assert exc.value.detail["current_mode"] == "paper"
