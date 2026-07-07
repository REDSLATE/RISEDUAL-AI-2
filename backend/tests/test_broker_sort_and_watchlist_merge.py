"""
Iteration 181 — Backend bug fixes verification.

Scope covered:
  (a) GET /api/broker/positions/{broker_id}         → sorted by symbol asc
  (b) GET /api/broker/orders/{broker_id}?status=all → sorted by (symbol, submitted_at)
  (c) GET /api/workspace/watchlist                  → best-effort merge of live
      broker holdings; graceful failure when broker call raises; persists
      newly-discovered symbols to `tickers` via $addToSet.
  (d) _sync_watchlist writes to canonical `tickers` field (not the old `symbols`).
  (e) Mongo indexes exist on trade_orders.symbol, trade_orders(user_id,symbol),
      paper_trades.symbol, paper_trades.ticker, watchlists.user_id.

Strategy:
  * HTTP tests via the public REACT_APP_BACKEND_URL cover the actual
    running server (login, watchlist round-trip, broker 404 baseline).
  * In-process tests import routes.broker / routes.workspace and stub the
    broker client so we can prove the sort + merge logic without a real
    Public.com connection.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

# Make backend importable when pytest is invoked from repo root
BACKEND_DIR = Path("/app/backend")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "risedual_db")

# -----------------------------------------------------------------------
#  HTTP fixtures
# -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    resp = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    if resp.status_code != 200:
        pytest.skip(f"Admin login failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    # user id for direct Mongo inserts
    s.user_id = data.get("id") or data.get("_id")
    return s


@pytest.fixture(scope="module")
def mongo_db():
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


# -----------------------------------------------------------------------
#  (1) HTTP baseline — endpoints reachable, 404 unchanged when no broker
# -----------------------------------------------------------------------

class TestBrokerHTTPBaseline:
    def test_positions_404_when_no_connection(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/broker/positions/public", timeout=30)
        # Either 404 (no connection) or 200 (real connection exists).
        assert r.status_code in (200, 404), f"Unexpected: {r.status_code} {r.text[:200]}"
        if r.status_code == 404:
            assert "No active" in r.json().get("detail", "")
        else:
            body = r.json()
            positions = body.get("positions", [])
            symbols = [p["symbol"] for p in positions]
            assert symbols == sorted(symbols), (
                f"positions not sorted alphabetically: {symbols}"
            )

    def test_orders_404_when_no_connection(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/broker/orders/public?status=all", timeout=30,
        )
        assert r.status_code in (200, 404), f"Unexpected: {r.status_code} {r.text[:200]}"
        if r.status_code == 404:
            assert "No active" in r.json().get("detail", "")
        else:
            body = r.json()
            orders = body.get("orders", [])
            symbols = [o["symbol"] for o in orders]
            assert symbols == sorted(symbols), (
                f"orders not sorted alphabetically: {symbols}"
            )


# -----------------------------------------------------------------------
#  (2) HTTP watchlist round-trip
# -----------------------------------------------------------------------

class TestWatchlistHTTP:
    def test_watchlist_get_returns_tickers_list(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/workspace/watchlist", timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "tickers" in body
        assert isinstance(body["tickers"], list)
        # merged list must be sorted (helper returns sorted(merged))
        assert body["tickers"] == sorted(body["tickers"]), (
            f"tickers not sorted: {body['tickers']}"
        )

    def test_add_then_read_roundtrip(self, admin_session):
        sym = f"TSTX{uuid.uuid4().hex[:4].upper()}"
        r = admin_session.post(
            f"{BASE_URL}/api/workspace/watchlist/add",
            json={"ticker": sym},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        r2 = admin_session.get(f"{BASE_URL}/api/workspace/watchlist", timeout=30)
        assert r2.status_code == 200
        assert sym in r2.json()["tickers"]
        # Cleanup
        admin_session.post(
            f"{BASE_URL}/api/workspace/watchlist/remove",
            json={"ticker": sym},
            timeout=30,
        )


# -----------------------------------------------------------------------
#  (3) HTTP — merge is fail-safe when broker client errors
# -----------------------------------------------------------------------

class TestWatchlistMergeFailsafe:
    """Insert a broker_connections doc referring to a fake broker id.
    BrokerService.get_broker_client will raise -> merge helper must
    swallow the error and still return the manual list."""

    def test_bad_broker_connection_does_not_break_watchlist(
        self, admin_session, mongo_db,
    ):
        user_id = admin_session.user_id
        assert user_id, "admin user_id missing"

        async def _run():
            # Insert a synthetic fake broker connection
            await mongo_db.broker_connections.insert_one({
                "user_id": user_id,
                "broker_id": "_test_stub_broker",
                "is_active": True,
                "status": "active",
                "api_key_enc": "not-a-real-cipher",
                "api_secret_enc": "not-a-real-cipher",
                "paper": True,
                "auth_method": "api_key",
                "_test_marker": True,
            })

        asyncio.get_event_loop().run_until_complete(_run())

        try:
            r = admin_session.get(f"{BASE_URL}/api/workspace/watchlist", timeout=30)
            assert r.status_code == 200, r.text
            body = r.json()
            assert "tickers" in body
            assert isinstance(body["tickers"], list)
            # Should still return a list even though merge broker failed
        finally:
            async def _cleanup():
                await mongo_db.broker_connections.delete_many(
                    {"_test_marker": True, "user_id": user_id},
                )
            asyncio.get_event_loop().run_until_complete(_cleanup())


# -----------------------------------------------------------------------
#  (4) In-process — sort logic in get_positions / get_orders
# -----------------------------------------------------------------------

class _StubClient:
    """Fake broker client returning positions/orders in scrambled order."""
    def __init__(self, positions=None, orders=None):
        self._positions = positions or []
        self._orders = orders or []

    def get_positions(self):
        return self._positions

    def get_orders(self, status="all"):
        return self._orders


@pytest.fixture(scope="module")
def broker_module(mongo_db):
    """Import routes.broker and give it a live db handle."""
    from routes import broker as broker_module_  # noqa: WPS433
    broker_module_.set_db(mongo_db)
    return broker_module_


def _make_request_stub(user_id: str):
    """Craft a fake Request compatible with _get_user monkeypatch."""
    # get_positions only uses request via `_get_user`, which we monkeypatch
    return SimpleNamespace(headers={}, cookies={}, state=SimpleNamespace())


class TestPositionsSortInProcess:
    def test_positions_sorted_alphabetically(self, broker_module, mongo_db, monkeypatch):
        user_id = "test-sort-user-" + uuid.uuid4().hex[:8]

        async def fake_get_user(request):
            return {"_id": user_id, "role": "owner"}

        async def fake_get_user_broker(uid, bid):
            return {"user_id": uid, "broker_id": bid, "auth_method": "api_key"}

        async def fake_get_or_refresh_client(uid, bid, conn):
            return _StubClient(positions=[
                {"symbol": "MSFT", "qty": 5, "side": "long",
                 "avg_entry_price": 300, "current_price": 310,
                 "market_value": 1550, "unrealized_pl": 50, "unrealized_plpc": 0.033},
                {"symbol": "AAPL", "qty": 10, "side": "long",
                 "avg_entry_price": 150, "current_price": 155,
                 "market_value": 1550, "unrealized_pl": 50, "unrealized_plpc": 0.033},
                {"symbol": "TSLA", "qty": 2, "side": "long",
                 "avg_entry_price": 200, "current_price": 210,
                 "market_value": 420, "unrealized_pl": 20, "unrealized_plpc": 0.05},
                {"symbol": "GOOGL", "qty": 3, "side": "long",
                 "avg_entry_price": 130, "current_price": 135,
                 "market_value": 405, "unrealized_pl": 15, "unrealized_plpc": 0.038},
            ])

        monkeypatch.setattr(broker_module, "_get_user", fake_get_user)
        monkeypatch.setattr(broker_module, "_get_user_broker", fake_get_user_broker)
        monkeypatch.setattr(
            broker_module, "_get_or_refresh_client", fake_get_or_refresh_client,
        )

        result = asyncio.get_event_loop().run_until_complete(
            broker_module.get_positions("public", _make_request_stub(user_id)),
        )

        symbols = [p["symbol"] for p in result["positions"]]
        assert symbols == ["AAPL", "GOOGL", "MSFT", "TSLA"], f"got {symbols}"
        assert result["total"] == 4

    def test_orders_sorted_by_symbol_then_submitted_at(
        self, broker_module, monkeypatch,
    ):
        async def fake_get_user(request):
            return {"_id": "u1", "role": "owner"}

        async def fake_get_user_broker(uid, bid):
            return {"user_id": uid, "broker_id": bid, "auth_method": "api_key"}

        async def fake_get_or_refresh_client(uid, bid, conn):
            return _StubClient(orders=[
                {"id": "1", "symbol": "MSFT", "side": "buy", "qty": 1,
                 "type": "market", "status": "filled", "filled_qty": 1,
                 "filled_avg_price": 300, "submitted_at": "2025-01-05",
                 "created_at": ""},
                {"id": "2", "symbol": "AAPL", "side": "buy", "qty": 1,
                 "type": "market", "status": "filled", "filled_qty": 1,
                 "filled_avg_price": 150, "submitted_at": "2025-01-03",
                 "created_at": ""},
                {"id": "3", "symbol": "AAPL", "side": "sell", "qty": 1,
                 "type": "market", "status": "filled", "filled_qty": 1,
                 "filled_avg_price": 160, "submitted_at": "2025-01-01",
                 "created_at": ""},
                {"id": "4", "symbol": "TSLA", "side": "buy", "qty": 1,
                 "type": "market", "status": "filled", "filled_qty": 1,
                 "filled_avg_price": 210, "submitted_at": "2025-01-02",
                 "created_at": ""},
            ])

        monkeypatch.setattr(broker_module, "_get_user", fake_get_user)
        monkeypatch.setattr(broker_module, "_get_user_broker", fake_get_user_broker)
        monkeypatch.setattr(
            broker_module, "_get_or_refresh_client", fake_get_or_refresh_client,
        )

        result = asyncio.get_event_loop().run_until_complete(
            broker_module.get_orders("public", _make_request_stub("u1"), "all"),
        )

        pairs = [(o["symbol"], o["submitted_at"]) for o in result["orders"]]
        assert pairs == [
            ("AAPL", "2025-01-01"),
            ("AAPL", "2025-01-03"),
            ("MSFT", "2025-01-05"),
            ("TSLA", "2025-01-02"),
        ], f"got {pairs}"


# -----------------------------------------------------------------------
#  (5) In-process — _sync_watchlist writes to 'tickers' (not 'symbols')
# -----------------------------------------------------------------------

class TestSyncWatchlistFieldName:
    def test_sync_watchlist_writes_tickers_via_addToSet(
        self, broker_module, mongo_db,
    ):
        user_id = "test-sync-" + uuid.uuid4().hex[:8]

        async def _run():
            # Legacy doc has 'symbols' populated but no 'tickers' — this is
            # exactly the residual-data shape called out in the bug report.
            await mongo_db.watchlists.delete_many({"user_id": user_id})
            await mongo_db.watchlists.insert_one({
                "user_id": user_id,
                "symbols": ["OLD1", "OLD2"],   # legacy wrong field
                "tickers": ["MANUAL_X"],
            })
            await broker_module._sync_watchlist(user_id, ["BAC", "JPM", "AAPL"])
            doc = await mongo_db.watchlists.find_one({"user_id": user_id})
            await mongo_db.watchlists.delete_one({"user_id": user_id})
            return doc

        doc = asyncio.get_event_loop().run_until_complete(_run())
        assert doc is not None
        tickers = doc.get("tickers", [])
        # Existing MANUAL_X preserved
        assert "MANUAL_X" in tickers, tickers
        # New broker symbols added
        for s in ("AAPL", "BAC", "JPM"):
            assert s in tickers, f"{s} missing from {tickers}"
        # Legacy 'symbols' field untouched by the fix (fix writes to
        # tickers only). It's fine either way — the important thing is
        # that reads land in the right place.

    def test_sync_watchlist_upserts_when_no_doc_exists(
        self, broker_module, mongo_db,
    ):
        user_id = "test-upsert-" + uuid.uuid4().hex[:8]

        async def _run():
            await mongo_db.watchlists.delete_many({"user_id": user_id})
            await broker_module._sync_watchlist(user_id, ["nvda", "amd "])
            doc = await mongo_db.watchlists.find_one({"user_id": user_id})
            await mongo_db.watchlists.delete_one({"user_id": user_id})
            return doc

        doc = asyncio.get_event_loop().run_until_complete(_run())
        assert doc is not None, "upsert didn't create a doc"
        tickers = set(doc.get("tickers", []))
        # Normalised uppercase + trimmed
        assert {"NVDA", "AMD"}.issubset(tickers), tickers


# -----------------------------------------------------------------------
#  (6) Mongo indexes exist as expected
# -----------------------------------------------------------------------

class TestMongoIndexes:
    def test_trade_orders_indexes(self, mongo_db):
        async def _idx():
            return await mongo_db.trade_orders.list_indexes().to_list(length=50)
        indexes = asyncio.get_event_loop().run_until_complete(_idx())
        names = {i["name"] for i in indexes}
        # symbol_asc + user_symbol should exist
        assert "symbol_asc" in names, f"trade_orders indexes: {names}"
        assert "user_symbol" in names, f"trade_orders indexes: {names}"

    def test_paper_trades_indexes(self, mongo_db):
        async def _idx():
            return await mongo_db.paper_trades.list_indexes().to_list(length=50)
        indexes = asyncio.get_event_loop().run_until_complete(_idx())
        names = {i["name"] for i in indexes}
        assert "symbol_asc" in names, f"paper_trades indexes: {names}"
        assert "ticker_asc" in names, f"paper_trades indexes: {names}"

    def test_watchlists_indexes(self, mongo_db):
        async def _idx():
            return await mongo_db.watchlists.list_indexes().to_list(length=50)
        indexes = asyncio.get_event_loop().run_until_complete(_idx())
        names = {i["name"] for i in indexes}
        assert "user_id_idx" in names, f"watchlists indexes: {names}"


# -----------------------------------------------------------------------
#  (7) In-process — _merge_broker_holdings unions and persists
# -----------------------------------------------------------------------

class TestMergeBrokerHoldings:
    def test_merge_unions_and_persists(self, mongo_db, monkeypatch):
        from routes import workspace as workspace_module
        from routes import broker as broker_module_

        workspace_module.set_db(mongo_db)
        broker_module_.set_db(mongo_db)

        user_id = "test-merge-" + uuid.uuid4().hex[:8]

        async def _prepare():
            await mongo_db.watchlists.delete_many({"user_id": user_id})
            await mongo_db.broker_connections.delete_many({"user_id": user_id})
            await mongo_db.broker_connections.insert_one({
                "user_id": user_id,
                "broker_id": "public",
                "is_active": True,
                "status": "active",
                "auth_method": "api_key",
                "_test_marker": True,
            })
            await mongo_db.watchlists.insert_one({
                "user_id": user_id,
                "tickers": ["AAPL", "META"],
            })

        asyncio.get_event_loop().run_until_complete(_prepare())

        async def fake_get_or_refresh_client(uid, bid, conn):
            return _StubClient(positions=[
                {"symbol": "BAC"}, {"symbol": "JPM"},
            ])

        monkeypatch.setattr(
            broker_module_, "_get_or_refresh_client", fake_get_or_refresh_client,
        )

        async def _run():
            merged = await workspace_module._merge_broker_holdings(
                user_id, ["AAPL", "META"],
            )
            doc = await mongo_db.watchlists.find_one({"user_id": user_id})
            await mongo_db.watchlists.delete_many({"user_id": user_id})
            await mongo_db.broker_connections.delete_many(
                {"_test_marker": True, "user_id": user_id},
            )
            return merged, doc

        merged, doc = asyncio.get_event_loop().run_until_complete(_run())
        assert merged == ["AAPL", "BAC", "JPM", "META"], f"got {merged}"
        # Persistence: newly-discovered symbols should be in the stored tickers
        assert doc is not None
        for s in ("AAPL", "BAC", "JPM", "META"):
            assert s in doc.get("tickers", []), doc

    def test_merge_swallows_broker_errors(self, mongo_db, monkeypatch):
        from routes import workspace as workspace_module
        from routes import broker as broker_module_

        workspace_module.set_db(mongo_db)
        broker_module_.set_db(mongo_db)

        user_id = "test-merge-err-" + uuid.uuid4().hex[:8]

        async def _prepare():
            await mongo_db.broker_connections.delete_many({"user_id": user_id})
            await mongo_db.broker_connections.insert_one({
                "user_id": user_id,
                "broker_id": "public",
                "is_active": True,
                "status": "active",
                "auth_method": "api_key",
                "_test_marker": True,
            })

        asyncio.get_event_loop().run_until_complete(_prepare())

        async def fake_get_or_refresh_client(uid, bid, conn):
            raise RuntimeError("simulated broker outage")

        monkeypatch.setattr(
            broker_module_, "_get_or_refresh_client", fake_get_or_refresh_client,
        )

        async def _run():
            merged = await workspace_module._merge_broker_holdings(
                user_id, ["AAPL", "META"],
            )
            await mongo_db.broker_connections.delete_many(
                {"_test_marker": True, "user_id": user_id},
            )
            return merged

        merged = asyncio.get_event_loop().run_until_complete(_run())
        # Broker failed → manual list returned unchanged (sorted).
        assert merged == ["AAPL", "META"], f"got {merged}"
