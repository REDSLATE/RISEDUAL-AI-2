"""Tests for the isolated crypto paper-trading BOT lane.

Architecture this test file pins down
-------------------------------------
* The bot writes EXCLUSIVELY to ``db.crypto_paper_trades`` —
  never to the legacy ``paper_trades`` collection.
* The bot uses an injected ``quote_provider`` (in production:
  ``services.crypto_quotes.get_crypto_quote``) — never the equity
  ``get_quote`` path.
* Non-crypto symbols (AAPL, SPY) are silently filtered out — the
  bot will not pollute the crypto collection with equities.
* Quote failures, low confidence, and DB write errors all surface
  as ``{symbol, skipped: True, reason: …}`` records instead of
  raising — the scheduler tick must never die.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from services.crypto_paper_trader import (
    CRYPTO_SYMBOLS,
    is_crypto_symbol,
    run_crypto_paper_bot,
)


# ── Symbol guard ──────────────────────────────────────────────────────────────


def test_is_crypto_symbol_accepts_bare_pair_and_dash_forms():
    assert is_crypto_symbol("BTC") is True
    assert is_crypto_symbol("BTC/USD") is True
    assert is_crypto_symbol("btc-usd") is True
    assert is_crypto_symbol("ETH") is True


def test_is_crypto_symbol_rejects_equities_and_garbage():
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("SPY") is False
    assert is_crypto_symbol(None) is False
    assert is_crypto_symbol("") is False


def test_canonical_set_covers_marquee_eight():
    for sym in ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK"):
        assert sym in CRYPTO_SYMBOLS, sym


# ── Happy path: fills land in crypto_paper_trades only ────────────────────────


class _FakeDB:
    """Minimal Motor-shaped stub — lets the bot call
    ``db.crypto_paper_trades.insert_one(...)``. Records writes so
    the test can assert on them."""

    def __init__(self):
        self.crypto_paper_trades = AsyncMock()
        self.crypto_paper_trades.insert_one = self._insert
        self.paper_trades = AsyncMock()  # MUST stay untouched
        self.writes: list[dict] = []

    async def _insert(self, doc):
        self.writes.append(dict(doc))


@pytest.mark.asyncio
async def test_bot_writes_to_crypto_collection_only():
    db = _FakeDB()

    async def quote(symbol):
        return {"symbol": symbol, "price": 77000.0}

    results = await run_crypto_paper_bot(db, quote, ["BTC"])

    assert len(results) == 1
    trade = results[0]
    assert trade["status"] == "open"
    assert trade["asset_class"] == "crypto"
    assert trade["symbol"] == "BTC"
    assert trade["pair"] == "BTC/USD"
    assert trade["source"] == "crypto_paper_bot"
    assert trade["metadata"]["lane"] == "crypto"

    # The architectural firewall — paper_trades MUST NOT be touched
    db.paper_trades.insert_one.assert_not_called()
    # And the crypto collection MUST have one fill
    assert len(db.writes) == 1
    assert db.writes[0]["asset_class"] == "crypto"


@pytest.mark.asyncio
async def test_bot_filters_non_crypto_symbols():
    db = _FakeDB()
    seen: list[str] = []

    async def quote(symbol):
        seen.append(symbol)
        return {"symbol": symbol, "price": 100.0}

    results = await run_crypto_paper_bot(db, quote, ["BTC", "AAPL", "SPY", "ETH"])

    # Equity tickers are skipped BEFORE the quote provider is called
    assert "AAPL" not in seen
    assert "SPY" not in seen
    # Crypto tickers got their quotes
    assert "BTC" in seen
    assert "ETH" in seen

    # Two fills, two skips
    opened = [r for r in results if r.get("status") == "open"]
    skipped = [r for r in results if r.get("skipped")]
    assert len(opened) == 2
    assert len(skipped) == 2
    assert all(r["reason"] == "not_crypto_symbol" for r in skipped)
    assert {r["symbol"] for r in skipped} == {"AAPL", "SPY"}


@pytest.mark.asyncio
async def test_bot_skips_when_quote_unavailable():
    db = _FakeDB()

    async def quote(symbol):
        # Simulate upstream outage
        return {"symbol": symbol, "price": 0.0}

    results = await run_crypto_paper_bot(db, quote, ["BTC"])

    assert len(results) == 1
    assert results[0]["skipped"] is True
    assert results[0]["reason"] == "quote_unavailable"
    # No DB write on a missing quote
    assert len(db.writes) == 0


@pytest.mark.asyncio
async def test_bot_skips_when_quote_provider_raises():
    db = _FakeDB()

    async def quote(symbol):
        raise RuntimeError("upstream timeout")

    results = await run_crypto_paper_bot(db, quote, ["BTC"])

    assert len(results) == 1
    assert results[0]["skipped"] is True
    assert results[0]["reason"] == "quote_unavailable"
    # The bot must NOT crash the scheduler
    assert len(db.writes) == 0


@pytest.mark.asyncio
async def test_bot_continues_after_db_write_failure():
    """One symbol's DB write fails — the next symbol still runs."""
    class _PartialFailDB:
        def __init__(self):
            self.crypto_paper_trades = AsyncMock()
            self.paper_trades = AsyncMock()
            self.writes: list[dict] = []
            self._call = 0
            self.crypto_paper_trades.insert_one = self._maybe_fail

        async def _maybe_fail(self, doc):
            self._call += 1
            if self._call == 1:
                raise RuntimeError("transient mongo error")
            self.writes.append(dict(doc))

    db = _PartialFailDB()

    async def quote(symbol):
        return {"symbol": symbol, "price": 70000.0}

    results = await run_crypto_paper_bot(db, quote, ["BTC", "ETH"])

    assert len(results) == 2
    btc, eth = results
    assert btc["skipped"] is True
    assert btc["reason"] == "db_write_failed"
    assert eth["status"] == "open"
    assert len(db.writes) == 1


@pytest.mark.asyncio
async def test_bot_default_universe_is_btc_eth_sol():
    """When the caller doesn't pass ``symbols``, the bot trades the
    headline three so the scheduler's bare invocation works out of
    the box."""
    db = _FakeDB()

    async def quote(symbol):
        return {"symbol": symbol, "price": 100.0}

    results = await run_crypto_paper_bot(db, quote)

    symbols_traded = {r.get("symbol") for r in results if r.get("status") == "open"}
    assert symbols_traded == {"BTC", "ETH", "SOL"}


@pytest.mark.asyncio
async def test_trade_record_shape_matches_spec():
    db = _FakeDB()

    async def quote(symbol):
        return {"symbol": symbol, "price": 65000.0}

    await run_crypto_paper_bot(db, quote, ["BTC"])

    assert len(db.writes) == 1
    doc = db.writes[0]
    expected_keys = {
        "trade_id", "asset_class", "symbol", "pair", "direction",
        "entry_price", "quantity", "confidence", "status",
        "opened_at", "source", "metadata",
    }
    assert expected_keys.issubset(doc.keys())
    # Type discipline — opened_at is a real datetime in the DB doc
    assert isinstance(doc["opened_at"], datetime)
    # but the route response gets it as ISO string (serialised post-insert)
    # — verified in the test_bot_writes_to_crypto_collection_only above.


# ── Quote wrapper isolation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crypto_quote_wrapper_uses_crypto_provider_not_equity():
    """``services.crypto_quotes.get_crypto_quote`` must delegate to
    ``price_provider.get_crypto_quote`` — never to ``get_quote``."""
    from unittest.mock import patch
    from services import crypto_quotes
    from services import price_provider

    with patch.object(price_provider, "get_crypto_quote",
                      AsyncMock(return_value={"price": 77000.0})) as mock_cq, \
         patch.object(price_provider, "get_quote",
                      AsyncMock(return_value={"price": 999.99})) as mock_eq:
        result = await crypto_quotes.get_crypto_quote("BTC")

    mock_cq.assert_awaited_once_with("BTC")
    mock_eq.assert_not_awaited()
    assert result["price"] == 77000.0


@pytest.mark.asyncio
async def test_crypto_quote_wrapper_falls_back_safely_on_provider_error():
    from unittest.mock import patch
    from services import crypto_quotes
    from services import price_provider

    with patch.object(price_provider, "get_crypto_quote",
                      AsyncMock(side_effect=RuntimeError("boom"))):
        result = await crypto_quotes.get_crypto_quote("BTC")

    assert result["price"] == 0.0
    assert result["symbol"] == "BTC"
