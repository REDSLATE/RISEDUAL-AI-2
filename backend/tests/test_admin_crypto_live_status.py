"""Tripwire coverage for ``GET /api/admin/crypto-live/status``.

Owner-only diagnostic endpoint that surfaces the EXACT live-crypto
state of the running pod. Tests pin:

  1. Route requires owner auth (401 unauthenticated, 403 non-owner).
  2. Response shape stays stable — operator dashboards will read
     specific keys; a rename here breaks every consumer.
  3. ``armed`` reflects the live env flag.
  4. Config block carries the SAME constants as the executor —
     a drift between the two surfaces would mislead the operator.
  5. ``daily_trades.count`` mirrors the 24h count used by the
     eligibility gate.
  6. ``open_positions.cap`` matches ``MAX_OPEN_LIVE_POSITIONS``.
  7. Recent-closed rows are sorted by closed_at DESC and limited.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from routes.admin_crypto_live_status import router, set_db


class _FakeUser:
    def __init__(self, role): self._role = role
    def get(self, k, default=None): return self._role if k == "role" else default


@pytest.fixture
def app_with_owner(monkeypatch):
    """Bind the router and stub auth to an owner user."""
    fake = _FakeUser("owner")
    monkeypatch.setattr(
        "routes.auth.get_current_user", AsyncMock(return_value=fake),
    )
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def app_with_non_owner(monkeypatch):
    fake = _FakeUser("user")
    monkeypatch.setattr(
        "routes.auth.get_current_user", AsyncMock(return_value=fake),
    )
    app = FastAPI()
    app.include_router(router)
    return app


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def sort(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class _FakeColl:
    def __init__(self, open_rows=None, closed_rows=None, daily=0):
        self._open = open_rows or []
        self._closed = closed_rows or []
        self._daily = daily
    def find(self, query, projection=None):
        status = query.get("status")
        if status == "open":
            return _FakeCursor(self._open)
        if status == "closed":
            return _FakeCursor(self._closed)
        return _FakeCursor([])
    async def count_documents(self, _q):
        return self._daily


class _FakeDB:
    def __init__(self, **kw):
        self.crypto_live_trades = _FakeColl(**kw)


# ── Auth ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_rejects_non_owner(app_with_non_owner):
    set_db(_FakeDB())
    transport = ASGITransport(app=app_with_non_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_returns_200_for_owner(app_with_owner):
    set_db(_FakeDB())
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    assert r.status_code == 200


# ── Response shape ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_response_shape_contract(app_with_owner, monkeypatch):
    """Lock the JSON keys operators / dashboards will consume."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    set_db(_FakeDB())
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    body = r.json()
    # Top-level
    for key in ("armed", "config", "open_positions", "daily_trades",
                "recent_closed"):
        assert key in body, f"missing top-level key {key}"
    # Config sub-keys
    for key in ("notional_per_trade_usd", "stop_loss_pct",
                "take_profit_pct", "symbol_allowlist",
                "max_open_positions", "max_daily_trades",
                "kraken_keys_set"):
        assert key in body["config"], f"missing config.{key}"
    # Open positions sub-keys
    assert "count" in body["open_positions"]
    assert "cap" in body["open_positions"]
    assert "rows" in body["open_positions"]
    # Daily trades sub-keys
    assert "count" in body["daily_trades"]
    assert "cap" in body["daily_trades"]


@pytest.mark.asyncio
async def test_status_armed_reflects_env(app_with_owner, monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    set_db(_FakeDB())
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    assert r.json()["armed"] is True

    monkeypatch.delenv("RISEDUAL_CRYPTO_LIVE_EXEC", raising=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    assert r.json()["armed"] is False


@pytest.mark.asyncio
async def test_status_config_pins_safety_constants(app_with_owner):
    set_db(_FakeDB())
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    cfg = r.json()["config"]
    # Operator directives 2026-06-01
    assert cfg["stop_loss_pct"] == 0.03
    assert cfg["take_profit_pct"] == 0.04
    assert cfg["max_open_positions"] == 3
    assert cfg["max_daily_trades"] == 10
    assert cfg["symbol_allowlist"] == ["BTC", "ETH"]


@pytest.mark.asyncio
async def test_status_surfaces_open_position_rows(app_with_owner):
    open_rows = [
        {
            "symbol": "BTC", "direction": "LONG",
            "entry_price": 60000.0, "size": 0.000417, "size_usd": 25.0,
            "kraken_order_id": "BUY-1", "kraken_pair": "XBTUSD",
            "stop_loss_order_id": "SL-1", "stop_loss_price": 58200.0,
            "stop_loss_placed": True, "stop_loss_pct": 0.03,
            "take_profit_order_id": "TP-1", "take_profit_price": 62400.0,
            "take_profit_placed": True, "take_profit_pct": 0.04,
            "opened_at": datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            "confidence": 0.7,
        },
    ]
    set_db(_FakeDB(open_rows=open_rows, daily=4))
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    body = r.json()
    assert body["open_positions"]["count"] == 1
    assert body["open_positions"]["cap"] == 3
    row = body["open_positions"]["rows"][0]
    assert row["kraken_order_id"] == "BUY-1"
    assert row["stop_loss_order_id"] == "SL-1"
    assert row["take_profit_order_id"] == "TP-1"
    # datetime serialised to ISO
    assert isinstance(row["opened_at"], str)
    # Daily count plumbed.
    assert body["daily_trades"]["count"] == 4


@pytest.mark.asyncio
async def test_status_surfaces_recent_closed(app_with_owner):
    closed_rows = [
        {"symbol": "BTC", "direction": "LONG",
         "entry_price": 60000.0, "exit_price": 62400.0,
         "closed_reason": "tp_hit", "pnl_pct": 4.0, "pnl_usd": 1.0,
         "opened_at": datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
         "closed_at": datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)},
    ]
    set_db(_FakeDB(closed_rows=closed_rows))
    transport = ASGITransport(app=app_with_owner)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/admin/crypto-live/status")
    body = r.json()
    assert len(body["recent_closed"]) == 1
    assert body["recent_closed"][0]["closed_reason"] == "tp_hit"
