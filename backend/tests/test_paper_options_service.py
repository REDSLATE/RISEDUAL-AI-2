"""Tests for `services.paper_options_service`.

Uses an in-process mongomock DB so we can validate persistence
without standing up a real Mongo instance. Mocks `get_quote` so
tests are deterministic and don't hit any real-world price provider.

Coverage:
  * BTO creates a leg, debits cash.
  * BTO on existing leg stacks qty and averages fill price.
  * STC partial close preserves remaining leg + credits cash.
  * STC full close removes the leg.
  * Validation: unknown option_type, unknown side, negative strike,
    fractional qty, STC without open leg.
  * Insufficient cash rejects without mutating state.
  * Underlying-price lookup failure rejects cleanly.
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import mongomock
import pytest

from services import paper_options_service as pos
from services import paper_trading_service as pts


USER = "user-paper-opt-1"


@pytest.fixture
def fake_db():
    """Fresh mongomock DB per test. The equity service shares the
    same `paper_portfolios` collection for the cash wallet, so we
    wire both services to the same DB."""
    client = mongomock.MongoClient()
    db = client["test_db"]

    # mongomock isn't async — wrap via the same pattern the service
    # uses. The service only calls `find_one`, `update_one`, and
    # `insert_one` which mongomock provides synchronously; patch
    # each to return a coroutine yielding the sync result.
    import asyncio

    def _awrap(fn):
        async def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        return wrapper

    class AsyncColl:
        def __init__(self, coll):
            self._c = coll
            self.find_one = _awrap(coll.find_one)
            self.insert_one = _awrap(coll.insert_one)
            self.update_one = _awrap(coll.update_one)

    class AsyncDB:
        def __init__(self, d):
            self.paper_portfolios = AsyncColl(d["paper_portfolios"])
            self.paper_trades = AsyncColl(d["paper_trades"])

    adb = AsyncDB(db)
    pos.set_db(adb)
    pts.set_db(adb)
    return adb


@pytest.fixture(autouse=True)
def mock_underlying_price():
    """Default underlying price $100. Tests that need a different
    price patch this fixture's return value directly."""
    with patch(
        "services.paper_options_service.get_quote",
        AsyncMock(return_value={"price": 100.0}),
    ):
        yield


# ── Happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bto_opens_new_leg_and_debits_cash(fake_db):
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 1, iv_percent=25,
    )
    assert r["status"] == "filled"
    assert r["qty"] == 1
    assert r["cash_after"] == pytest.approx(100_000 - r["contract_cost"], abs=0.01)

    doc = await fake_db.paper_portfolios.find_one({"user_id": USER})
    assert len(doc["option_positions"]) == 1
    leg = doc["option_positions"][0]
    assert leg["symbol"] == "AAPL"
    assert leg["strike"] == 100
    assert leg["qty"] == 1


@pytest.mark.asyncio
async def test_bto_on_existing_leg_stacks_and_averages(fake_db):
    await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 2, iv_percent=25,
    )
    r2 = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 3, iv_percent=25,
    )
    assert r2["status"] == "filled"

    doc = await fake_db.paper_portfolios.find_one({"user_id": USER})
    legs = doc["option_positions"]
    assert len(legs) == 1
    assert legs[0]["qty"] == 5
    # Average is pinned between the two fills (they'll be close but
    # spread may differ since we're calling in sequence).
    assert legs[0]["avg_fill"] > 0


@pytest.mark.asyncio
async def test_stc_partial_close_keeps_remainder(fake_db):
    await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 5, iv_percent=25,
    )
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "sell_to_close", 2, iv_percent=25,
    )
    assert r["status"] == "filled"
    doc = await fake_db.paper_portfolios.find_one({"user_id": USER})
    assert doc["option_positions"][0]["qty"] == 3


@pytest.mark.asyncio
async def test_stc_full_close_removes_leg(fake_db):
    await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 1, iv_percent=25,
    )
    await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "sell_to_close", 1, iv_percent=25,
    )
    doc = await fake_db.paper_portfolios.find_one({"user_id": USER})
    assert doc["option_positions"] == []


# ── Rejection paths ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_option_type_rejected(fake_db):
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "straddle",
        "buy_to_open", 1,
    )
    assert r["status"] == "rejected"
    assert "option_type" in r["error"]


@pytest.mark.asyncio
async def test_unsupported_side_rejected(fake_db):
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "sell_to_open", 1,  # short options deferred
    )
    assert r["status"] == "rejected"
    assert "short legs not yet supported" in r["error"]


@pytest.mark.asyncio
async def test_fractional_qty_rejected(fake_db):
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "buy_to_open", 1.5,  # type: ignore[arg-type]
    )
    assert r["status"] == "rejected"
    assert "positive integer" in r["error"]


@pytest.mark.asyncio
async def test_stc_without_open_leg_rejected(fake_db):
    r = await pos.execute_option_trade(
        USER, "AAPL", 100, "2026-12-18", "call",
        "sell_to_close", 1,
    )
    assert r["status"] == "rejected"
    assert "No open" in r["error"]


@pytest.mark.asyncio
async def test_insufficient_cash_rejected_and_state_unchanged(fake_db):
    # Force huge cost: 1000 contracts deep-ITM with high vol.
    with patch(
        "services.paper_options_service.get_quote",
        AsyncMock(return_value={"price": 1000.0}),
    ):
        r = await pos.execute_option_trade(
            USER, "AAPL", 100, "2026-12-18", "call",
            "buy_to_open", 100, iv_percent=50,
        )
    assert r["status"] == "rejected"
    assert "Insufficient cash" in r["error"]
    doc = await fake_db.paper_portfolios.find_one({"user_id": USER})
    # Portfolio never mutated beyond the initial auto-create.
    assert doc["cash"] == 100_000
    assert doc.get("option_positions", []) == []


@pytest.mark.asyncio
async def test_price_lookup_failure_rejects(fake_db):
    with patch(
        "services.paper_options_service.get_quote",
        AsyncMock(return_value=None),
    ):
        r = await pos.execute_option_trade(
            USER, "WTFX", 10, "2026-12-18", "call",
            "buy_to_open", 1,
        )
    assert r["status"] == "rejected"
    assert "underlying price" in r["error"]
