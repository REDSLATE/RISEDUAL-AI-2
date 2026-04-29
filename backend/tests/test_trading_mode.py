"""Tests for trading-mode service + routes.

Covers:
* default mode resolution (paper)
* paper→live switch with valid confirm
* paper→live switch missing confirm → 400
* live→paper switch (no confirm needed)
* cooldown rejection
* audit log row written
* user_response includes trading_mode field
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient

import routes.trading_mode as tm_routes
from routes.trading_mode import router as tm_router
from routes.auth import user_response
from services.trading_mode_service import (
    COOLDOWN_SECONDS,
    TradingModeError,
    get_mode_state,
    get_user_trading_mode,
    is_live_mode,
    set_db as set_tm_db,
    switch_user_trading_mode,
)


# ── Shared fixtures ───────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    test_db = client[os.environ["DB_NAME"] + "_test_trading_mode"]
    await test_db.users.delete_many({})
    await test_db.trading_mode_switches.delete_many({})
    set_tm_db(test_db)
    yield test_db
    await test_db.users.delete_many({})
    await test_db.trading_mode_switches.delete_many({})
    client.close()


@pytest_asyncio.fixture
async def user_id(db):
    res = await db.users.insert_one({
        "email": "trader@example.com",
        "password_hash": "x",
        "name": "Trader",
        "role": "user",
        "subscription_status": "pro",
        "created_at": datetime.now(timezone.utc),
    })
    return str(res.inserted_id)


# ── Service-level tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_mode_is_paper(db, user_id):
    mode = await get_user_trading_mode(user_id)
    assert mode == "paper"
    assert await is_live_mode(user_id) is False


@pytest.mark.asyncio
async def test_state_includes_cooldown_metadata(db, user_id):
    state = await get_mode_state(user_id)
    assert state["mode"] == "paper"
    assert state["cooldown_remaining_s"] == 0
    assert state["cooldown_total_s"] == COOLDOWN_SECONDS
    assert state["last_switch_at"] is None


@pytest.mark.asyncio
async def test_paper_to_live_requires_confirm_text(db, user_id):
    with pytest.raises(TradingModeError) as exc:
        await switch_user_trading_mode(user_id, new_mode="live")
    assert exc.value.code == "confirm_required"

    # Wrong text also rejected
    with pytest.raises(TradingModeError) as exc:
        await switch_user_trading_mode(
            user_id, new_mode="live", confirm_text="yes"
        )
    assert exc.value.code == "confirm_required"


@pytest.mark.asyncio
async def test_paper_to_live_succeeds_with_confirm(db, user_id):
    res = await switch_user_trading_mode(
        user_id, new_mode="live", confirm_text="LIVE"
    )
    assert res["ok"] is True
    assert res["mode"] == "live"
    assert res["previous_mode"] == "paper"
    assert await get_user_trading_mode(user_id) == "live"
    assert await is_live_mode(user_id) is True


@pytest.mark.asyncio
async def test_live_to_paper_does_not_require_confirm(db, user_id):
    await switch_user_trading_mode(
        user_id, new_mode="live", confirm_text="LIVE"
    )
    # Rewind so cooldown doesn't block the next switch
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {
            "$set": {
                "trading_mode_last_switch_at":
                    datetime.now(timezone.utc) - timedelta(seconds=COOLDOWN_SECONDS + 5)
            }
        },
    )
    res = await switch_user_trading_mode(user_id, new_mode="paper")
    assert res["ok"] is True
    assert res["mode"] == "paper"
    assert res["previous_mode"] == "live"


@pytest.mark.asyncio
async def test_cooldown_rejects_rapid_switch(db, user_id):
    await switch_user_trading_mode(
        user_id, new_mode="live", confirm_text="LIVE"
    )
    with pytest.raises(TradingModeError) as exc:
        await switch_user_trading_mode(user_id, new_mode="paper")
    assert exc.value.code == "cooldown"


@pytest.mark.asyncio
async def test_no_op_when_already_in_target_mode(db, user_id):
    res = await switch_user_trading_mode(user_id, new_mode="paper")
    assert res["ok"] is True
    assert res.get("no_op") is True


@pytest.mark.asyncio
async def test_invalid_mode_rejected(db, user_id):
    with pytest.raises(TradingModeError) as exc:
        await switch_user_trading_mode(user_id, new_mode="margin")
    assert exc.value.code == "invalid_mode"


@pytest.mark.asyncio
async def test_audit_row_written(db, user_id):
    await switch_user_trading_mode(
        user_id,
        new_mode="live",
        confirm_text="LIVE",
        request_meta={"ip": "1.2.3.4", "user_agent": "test-agent"},
    )
    rows = await db.trading_mode_switches.find(
        {"user_id": user_id}, {"_id": 0}
    ).to_list(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["from_mode"] == "paper"
    assert row["to_mode"] == "live"
    assert row["confirmed"] is True
    assert row["ip"] == "1.2.3.4"
    assert row["user_agent"] == "test-agent"


# ── Route-level tests (httpx AsyncClient — same event loop as fixture) ──


@pytest_asyncio.fixture
async def app_client(db, user_id, monkeypatch):
    """Build a tiny FastAPI app mounting only the trading_mode router with
    `get_current_user` stubbed to a known test user, served via httpx.

    Using httpx.AsyncClient keeps Motor on the same event loop as the
    fixture-created Mongo client (TestClient spins its own loop and
    Motor is loop-bound, which causes silent state drift).
    """
    from httpx import AsyncClient, ASGITransport

    async def fake_user(request: Request):
        return {"_id": ObjectId(user_id), "email": "trader@example.com"}

    monkeypatch.setattr(tm_routes, "get_current_user", fake_user)

    test_app = FastAPI()
    test_app.include_router(tm_router)

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_route_current_returns_paper_default(app_client):
    r = await app_client.get("/api/trading-mode/current")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "paper"
    assert body["cooldown_remaining_s"] == 0


@pytest.mark.asyncio
async def test_route_switch_to_live_without_confirm_returns_400(app_client):
    r = await app_client.post("/api/trading-mode/switch", json={"mode": "live"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "confirm_required"


@pytest.mark.asyncio
async def test_route_switch_to_live_with_confirm_succeeds_then_cooldown(app_client):
    r = await app_client.post(
        "/api/trading-mode/switch",
        json={"mode": "live", "confirm_text": "LIVE"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "live"

    # Cooldown engaged → second switch rejected with 429
    r2 = await app_client.post(
        "/api/trading-mode/switch",
        json={"mode": "paper"},
    )
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "cooldown"


@pytest.mark.asyncio
async def test_route_invalid_mode_returns_400(app_client):
    r = await app_client.post("/api/trading-mode/switch", json={"mode": "futures"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_mode"


# ── user_response surface ─────────────────────────────────────────────


def test_user_response_includes_trading_mode():
    user = {
        "_id": ObjectId(),
        "email": "x@y.com",
        "name": "X",
        "trading_mode": "live",
    }
    resp = user_response(user)
    assert resp["trading_mode"] == "live"


def test_user_response_defaults_to_paper():
    user = {"_id": ObjectId(), "email": "x@y.com"}
    resp = user_response(user)
    assert resp["trading_mode"] == "paper"
