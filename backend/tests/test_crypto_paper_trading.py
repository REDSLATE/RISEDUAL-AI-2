"""Tests for crypto symbol routing + paper trade asset_class tagging.

Locks in the 2026-04-26 fix: the equity ``TIME_SERIES_DAILY`` /
``GLOBAL_QUOTE`` paths were silently returning bogus data for crypto
tickers (e.g. ``BTC`` resolved to BIT Mining stock at ~$34 instead of
Bitcoin at $77k). Crypto must now route through
``DIGITAL_CURRENCY_DAILY`` + ``get_crypto_quote()`` — the same source
``/api/crypto/prices`` uses.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.crypto_symbols import (
    CRYPTO_SYMBOLS,
    is_crypto,
    to_alpaca_crypto,
    to_yf_crypto,
)


def test_is_crypto_bare_tickers():
    assert is_crypto("BTC") is True
    assert is_crypto("eth") is True
    assert is_crypto("SOL") is True
    assert is_crypto("AAPL") is False
    assert is_crypto("SPY") is False


def test_is_crypto_handles_broker_quote_forms():
    # Alpaca form
    assert is_crypto("BTC/USD") is True
    assert is_crypto("ETH/USD") is True
    # yfinance form
    assert is_crypto("BTC-USD") is True
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
    # Regression: make sure the headline names the bots will trade
    # against don't get accidentally dropped from the set.
    for sym in ("BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "LINK"):
        assert sym in CRYPTO_SYMBOLS, sym


@pytest.mark.asyncio
async def test_get_quote_routes_crypto_to_get_crypto_quote():
    """get_quote(BTC) should delegate to get_crypto_quote (the same
    source /api/crypto/prices uses) instead of falling back to the
    equity GLOBAL_QUOTE path which returns BIT Mining stock data."""
    from services import price_provider

    expected = {"symbol": "BTC", "price": 77346.93, "source": "alpha_vantage"}
    with patch.object(
        price_provider,
        "get_crypto_quote",
        AsyncMock(return_value=expected),
    ) as mock_cq:
        result = await price_provider.get_quote("BTC")
    mock_cq.assert_awaited_once_with("BTC")
    assert result == expected


@pytest.mark.asyncio
async def test_get_quote_does_not_route_equities_to_crypto_path():
    from services import price_provider

    with patch.object(
        price_provider,
        "get_crypto_quote",
        AsyncMock(return_value={"price": 999}),
    ) as mock_cq:
        # AAPL must not hit the crypto path.
        with patch.object(
            price_provider, "_av_quote", return_value={"symbol": "AAPL", "price": 271.06}
        ):
            result = await price_provider.get_quote("AAPL")
    mock_cq.assert_not_awaited()
    assert result and result.get("price") == 271.06


@pytest.mark.asyncio
async def test_get_daily_history_routes_crypto_to_digital_currency_endpoint():
    """Crypto symbols must hit DIGITAL_CURRENCY_DAILY (or yfinance
    -USD) — never the equity TIME_SERIES_DAILY which gives wrong data
    for ``BTC`` / ``ETH``."""
    from services import price_provider

    sentinel = [{"date": "2026-04-25", "open": 77000, "high": 78000,
                 "low": 76500, "close": 77346.93, "volume": 1234}]

    with patch.object(price_provider, "_av_crypto_daily", return_value=sentinel) as mock_crypto, \
         patch.object(price_provider, "_av_daily", return_value=[{"close": 34.37}]) as mock_equity:
        # Bypass cache by clearing
        from services.sliding_cache import price_cache
        if hasattr(price_cache, "_cache"):
            price_cache._cache.clear()
        rows = await price_provider.get_daily_history("BTC", "compact")

    # AV equity daily MUST NOT be called for a crypto ticker.
    mock_equity.assert_not_called()
    mock_crypto.assert_called_once()
    assert rows == sentinel


@pytest.mark.asyncio
async def test_get_daily_history_keeps_equity_path_for_stocks():
    from services import price_provider
    from services.sliding_cache import price_cache

    sentinel = [{"date": "2026-04-25", "open": 270, "high": 272,
                 "low": 269, "close": 271.06, "volume": 50_000_000}]

    with patch.object(price_provider, "_av_crypto_daily", return_value=[{"close": 99}]) as mock_crypto, \
         patch.object(price_provider, "_av_daily", return_value=sentinel) as mock_equity:
        if hasattr(price_cache, "_cache"):
            price_cache._cache.clear()
        # Force pool path off so we exercise the equity legacy branch.
        with patch("services.market_data_pool.market_pool") as mock_pool:
            mock_pool.available = False
            rows = await price_provider.get_daily_history("AAPL", "compact")

    mock_crypto.assert_not_called()
    mock_equity.assert_called_once()
    assert rows == sentinel


@pytest.mark.asyncio
async def test_paper_trading_execute_trade_stamps_asset_class_crypto():
    """Paper trades on crypto symbols must carry ``asset_class=crypto``
    so the labeler/closer + admin dashboards can filter equity vs
    crypto fills."""
    from services import paper_trading_service as pts

    fake_db = AsyncMock()
    fake_db.paper_portfolios.find_one = AsyncMock(return_value={
        "user_id": "u1", "cash": 100_000.0, "positions": [],
    })
    fake_db.paper_portfolios.update_one = AsyncMock()

    inserted: dict = {}

    async def _capture(doc):
        inserted.update(doc)
        return AsyncMock(inserted_id="x")

    fake_db.paper_trades.insert_one = _capture
    pts.set_db(fake_db)

    with patch.object(pts, "_get_live_price", AsyncMock(return_value=77346.93)):
        result = await pts.execute_trade("u1", "BTC", "BUY", 0.01)

    assert result["status"] == "filled"
    assert inserted.get("asset_class") == "crypto"
    assert inserted.get("symbol") == "BTC"


@pytest.mark.asyncio
async def test_paper_trading_execute_trade_stamps_asset_class_equity():
    from services import paper_trading_service as pts

    fake_db = AsyncMock()
    fake_db.paper_portfolios.find_one = AsyncMock(return_value={
        "user_id": "u1", "cash": 100_000.0, "positions": [],
    })
    fake_db.paper_portfolios.update_one = AsyncMock()

    inserted: dict = {}

    async def _capture(doc):
        inserted.update(doc)
        return AsyncMock(inserted_id="x")

    fake_db.paper_trades.insert_one = _capture
    pts.set_db(fake_db)

    with patch.object(pts, "_get_live_price", AsyncMock(return_value=271.06)):
        result = await pts.execute_trade("u1", "AAPL", "BUY", 5)

    assert result["status"] == "filled"
    assert inserted.get("asset_class") == "equity"


def test_ml_paper_trader_asset_class_helper():
    from services.ml_paper_trader import _asset_class_for

    assert _asset_class_for("BTC") == "crypto"
    assert _asset_class_for("eth") == "crypto"
    assert _asset_class_for("AAPL") == "equity"
    assert _asset_class_for("SPY") == "equity"


@pytest.mark.asyncio
async def test_paper_trade_closer_anchors_crypto_to_crypto_quote():
    """Paper trade closer must use ``get_crypto_quote`` (same source
    as ``/api/crypto/prices``) when fetching prices for crypto
    tickers — never the equity ``get_quote`` path."""
    from services import paper_trade_closer as ptc

    fake_db = AsyncMock()

    with patch("services.price_provider.get_crypto_quote",
               AsyncMock(return_value={"price": 77346.93})) as mock_cq, \
         patch("services.price_provider.get_quote",
               AsyncMock(return_value={"price": 999})) as mock_gq:
        price = await ptc._fetch_price("BTC", fake_db)

    mock_cq.assert_awaited_once_with("BTC")
    mock_gq.assert_not_awaited()
    assert price == 77346.93


@pytest.mark.asyncio
async def test_paper_trade_closer_keeps_equity_path_for_stocks():
    from services import paper_trade_closer as ptc

    fake_db = AsyncMock()

    with patch("services.price_provider.get_crypto_quote",
               AsyncMock(return_value={"price": 999})) as mock_cq, \
         patch("services.price_provider.get_quote",
               AsyncMock(return_value={"price": 271.06})) as mock_gq:
        price = await ptc._fetch_price("AAPL", fake_db)

    mock_cq.assert_not_awaited()
    mock_gq.assert_awaited_once_with("AAPL")
    assert price == 271.06
