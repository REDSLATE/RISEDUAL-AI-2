"""Tests for the isolated crypto paper-trading subsystem.

Architecture this test file pins down
-------------------------------------
* Crypto fills land EXCLUSIVELY in the ``crypto_paper_trades``
  collection — the equity ``paper_trades`` collection is never
  touched by this code path.
* The crypto service uses ``get_crypto_quote`` (the same source
  ``/api/crypto/prices`` uses); the equity ``get_quote`` path is
  never invoked.
* TEST_*/MOCK_*/FAKE_*/FAKEXYZ symbols are blocked at the boundary
  (wired to yesterday's contamination-guard module).
* Non-crypto symbols (AAPL, SPY) are refused at the boundary so
  they cannot pollute the crypto collection.
* Idempotency: same (user, symbol, side, qty, price-bucket,
  minute-bucket) collapses onto the original trade_id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import crypto_paper_trading_service as cps
from services.crypto_symbols import (
    CRYPTO_SYMBOLS,
    is_crypto,
    to_alpaca_crypto,
    to_yf_crypto,
)


# ── Crypto symbol registry ────────────────────────────────────────────────────


def test_is_crypto_bare_tickers():
    assert is_crypto("BTC") is True
    assert is_crypto("eth") is True
    assert is_crypto("SOL") is True
    assert is_crypto("AAPL") is False
    assert is_crypto("SPY") is False


def test_is_crypto_handles_broker_quote_forms():
    assert is_crypto("BTC/USD") is True   # Alpaca form
    assert is_crypto("ETH/USD") is True
    assert is_crypto("BTC-USD") is True   # yfinance form
    assert is_crypto("doge-usd") is True


def test_is_crypto_handles_none_and_blank():
    assert is_crypto(None) is False
    assert is_crypto("") is False
    assert is_crypto("   ") is False


def test_to_yf_crypto_roundtrip():
    assert to_yf_crypto("BTC") == "BTC-USD"
    assert to_yf_crypto("BTC/USD") == "BTC-USD"
    assert to_yf_crypto("eth-usd") == "ETH-USD"


def test_to_alpaca_crypto_roundtrip():
    assert to_alpaca_crypto("BTC") == "BTC/USD"
    assert to_alpaca_crypto("eth-usd") == "ETH/USD"
    assert to_alpaca_crypto("SOL/USD") == "SOL/USD"


def test_canonical_set_covers_marquee_pairs():
    for sym in ("BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "LINK"):
        assert sym in CRYPTO_SYMBOLS, sym


# ── Boundary guards: test fixtures + non-crypto refused ───────────────────────


@pytest.mark.asyncio
async def test_execute_blocks_test_fixture_symbol():
    fake_db = AsyncMock()
    fake_db.crypto_paper_trades.insert_one = AsyncMock()
    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=77000.0)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="TEST_BTC", side="BUY", qty=0.01,
        )

    assert result.get("blocked") is True
    assert result.get("reason") == "test_fixture_symbol"
    fake_db.crypto_paper_trades.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_execute_blocks_fakexyz():
    fake_db = AsyncMock()
    fake_db.crypto_paper_trades.insert_one = AsyncMock()
    cps.set_db(fake_db)

    result = await cps.execute_crypto_paper_trade(
        user_id="u1", symbol="FAKEXYZ", side="BUY", qty=1,
    )

    assert result.get("blocked") is True
    fake_db.crypto_paper_trades.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_execute_blocks_non_crypto_symbol():
    """An equity ticker (AAPL, SPY) must NOT be allowed to land in
    the crypto collection. This is the architectural firewall."""
    fake_db = AsyncMock()
    fake_db.crypto_paper_trades.insert_one = AsyncMock()
    cps.set_db(fake_db)

    result = await cps.execute_crypto_paper_trade(
        user_id="u1", symbol="AAPL", side="BUY", qty=10,
    )

    assert result.get("blocked") is True
    assert result.get("reason") == "not_crypto_symbol"
    fake_db.crypto_paper_trades.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejects_invalid_side():
    fake_db = AsyncMock()
    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=77000.0)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="BTC", side="HODL", qty=0.01,
        )

    assert result.get("status") == "rejected"
    assert "Side" in result.get("error", "")


@pytest.mark.asyncio
async def test_execute_rejects_zero_qty():
    fake_db = AsyncMock()
    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=77000.0)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="BTC", side="BUY", qty=0,
        )

    assert result.get("status") == "rejected"


@pytest.mark.asyncio
async def test_execute_rejects_when_quote_unavailable():
    fake_db = AsyncMock()
    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=None)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="BTC", side="BUY", qty=0.01,
        )

    assert result.get("status") == "rejected"
    assert "live crypto price" in result.get("error", "")


# ── Happy path: crypto fill lands in crypto_paper_trades ──────────────────────


@pytest.mark.asyncio
async def test_execute_writes_to_crypto_collection_only():
    """Verify crypto fills go to ``crypto_paper_trades`` and never
    to the legacy ``paper_trades`` collection."""
    fake_db = AsyncMock()
    captured: dict = {}

    async def _capture(doc):
        captured.update(doc)
        return AsyncMock(inserted_id="x")

    # Wire BOTH collections; only the crypto one should be touched.
    fake_db.crypto_paper_trades.insert_one = _capture
    fake_db.paper_trades.insert_one = AsyncMock()
    fake_db.__getitem__ = lambda self, k: getattr(fake_db, k)

    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=77346.93)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u-alice", symbol="BTC", side="BUY", qty=0.01,
        )

    assert result["status"] == "filled"
    assert result["asset_class"] == "crypto"
    assert result["symbol"] == "BTC"
    assert captured.get("asset_class") == "crypto"
    assert captured.get("schema_version") == 1
    assert captured.get("idempotency_bucket")  # non-empty
    fake_db.paper_trades.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_execute_uses_crypto_quote_not_equity_quote():
    """Architectural pin: the crypto service must call
    ``get_crypto_quote``, never ``get_quote``."""
    from services import price_provider

    fake_db = AsyncMock()
    fake_db.crypto_paper_trades.insert_one = AsyncMock()
    fake_db.__getitem__ = lambda self, k: getattr(fake_db, k)
    cps.set_db(fake_db)

    with patch.object(price_provider, "get_crypto_quote",
                      AsyncMock(return_value={"price": 77000.0})) as mock_cq, \
         patch.object(price_provider, "get_quote",
                      AsyncMock(return_value={"price": 999.99})) as mock_eq:
        await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="BTC", side="BUY", qty=0.01,
        )

    mock_cq.assert_awaited_once_with("BTC")
    mock_eq.assert_not_awaited()


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_replay_collapses_to_original():
    """A duplicate POST within the same minute must NOT create two
    rows. The unique index trips, the service catches it, and the
    canonical first-fill record is returned to the client."""
    fake_db = AsyncMock()

    canonical = {
        "trade_id": "first-uuid",
        "user_id": "u1",
        "symbol": "BTC",
        "asset_class": "crypto",
        "side": "BUY",
        "qty": 0.01,
        "price": 77000.0,
        "notional_usd": 770.0,
        "opened_at": datetime.now(timezone.utc),
        "idempotency_bucket": "u1|BTC|BUY|0.01000000|77000.0|2026-04-25T20:00",
        "schema_version": 1,
    }

    async def _raise_dupe(_doc):
        # Mongo's pymongo.errors.DuplicateKeyError surfaces as an
        # exception whose class name contains "DuplicateKey".
        class DuplicateKeyError(Exception):
            pass
        raise DuplicateKeyError("E11000 duplicate key error")

    fake_db.crypto_paper_trades.insert_one = _raise_dupe
    fake_db.crypto_paper_trades.find_one = AsyncMock(return_value=canonical)
    fake_db.__getitem__ = lambda self, k: getattr(fake_db, k)
    cps.set_db(fake_db)

    with patch.object(cps, "_fetch_crypto_price",
                      AsyncMock(return_value=77000.0)):
        result = await cps.execute_crypto_paper_trade(
            user_id="u1", symbol="BTC", side="BUY", qty=0.01,
        )

    assert result["status"] == "filled"
    assert result["idempotent_replay"] is True
    assert result["trade_id"] == "first-uuid"


def test_idempotency_key_buckets_to_minute():
    """Two timestamps in the same UTC minute produce the same bucket;
    crossing the minute boundary produces a different one."""
    now1 = datetime(2026, 4, 25, 20, 0, 5, tzinfo=timezone.utc)
    now2 = datetime(2026, 4, 25, 20, 0, 55, tzinfo=timezone.utc)
    now3 = datetime(2026, 4, 25, 20, 1, 0, tzinfo=timezone.utc)

    k1 = cps._make_idem_key(user_id="u", symbol="BTC", side="BUY",
                            qty=0.01, price=77000.0, now=now1)
    k2 = cps._make_idem_key(user_id="u", symbol="BTC", side="BUY",
                            qty=0.01, price=77000.0, now=now2)
    k3 = cps._make_idem_key(user_id="u", symbol="BTC", side="BUY",
                            qty=0.01, price=77000.0, now=now3)

    assert k1 == k2  # same minute → collapses
    assert k1 != k3  # next minute → distinct


# ── History + position summary ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_filters_by_user_and_symbol():
    fake_db = AsyncMock()

    rows = [
        {"trade_id": "a", "user_id": "u1", "symbol": "BTC",
         "side": "BUY", "qty": 0.01, "price": 77000,
         "opened_at": datetime.now(timezone.utc), "asset_class": "crypto"},
    ]

    class _Cursor:
        def sort(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def to_list(self, length=None): return rows

    fake_db.crypto_paper_trades.find = lambda q, p=None: _Cursor()
    fake_db.__getitem__ = lambda self, k: getattr(fake_db, k)
    cps.set_db(fake_db)

    out = await cps.get_crypto_paper_history("u1", symbol="BTC", limit=10)
    assert len(out) == 1
    assert out[0]["symbol"] == "BTC"
    # opened_at must serialize for JSON
    assert isinstance(out[0]["opened_at"], str)


@pytest.mark.asyncio
async def test_position_summary_marks_to_market():
    fake_db = AsyncMock()

    fills = [
        {"symbol": "BTC", "side": "BUY", "qty": 0.02, "price": 70000.0},
        {"symbol": "BTC", "side": "SELL", "qty": 0.01, "price": 76000.0},
        {"symbol": "ETH", "side": "BUY", "qty": 1.0, "price": 2200.0},
    ]

    class _Cursor:
        async def to_list(self, length=None): return fills

    fake_db.crypto_paper_trades.find = lambda q, p=None: _Cursor()
    fake_db.__getitem__ = lambda self, k: getattr(fake_db, k)
    cps.set_db(fake_db)

    async def _mark(sym):
        return {"BTC": 80000.0, "ETH": 2300.0}.get(sym)

    with patch.object(cps, "_fetch_crypto_price", side_effect=_mark):
        summary = await cps.get_crypto_paper_position_summary("u1")

    assert summary["position_count"] == 2
    btc = next(p for p in summary["positions"] if p["symbol"] == "BTC")
    # Net BTC: 0.02 BUY - 0.01 SELL = 0.01
    assert abs(btc["qty"] - 0.01) < 1e-9
    # Mark-to-market: 0.01 * 80000 = 800
    assert btc["market_value_usd"] == 800.0
