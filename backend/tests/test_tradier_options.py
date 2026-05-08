"""Tests for `services.brokers.tradier_options`.

Scope: `fetch_tradier_option_quote` + `TradierOptionsAdapter`.
Mocks httpx at the boundary — we don't hit the live Tradier API.

Pins:
  * quote fetcher returns None without a token (no network call)
  * quote fetcher parses Tradier's nested `quotes.quote` envelope
  * missing symbol / HTTP errors return None (never raise)
  * spread calculation = max(ask - bid, 0)
  * adapter reports enabled=True iff token is set (level=0 for
    quote-only mode)
  * all order-placement methods raise BrokerNotImplementedError
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.brokers.options_adapter import BrokerNotImplementedError
from services.brokers.tradier_options import (
    TradierOptionsAdapter,
    _tradier_symbol,
    fetch_tradier_option_quote,
)


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body if json_body is not None else {})
    resp.text = "mock body"
    resp.content = b"{}"
    return resp


# ── _tradier_symbol ────────────────────────────────────────────────

def test_tradier_symbol_strips_root_padding():
    canonical = "AAPL  261218C00200000"
    assert _tradier_symbol(canonical) == "AAPL261218C00200000"


def test_tradier_symbol_passthrough_non_canonical():
    # Non-21-char inputs are returned unchanged — paranoid guard.
    assert _tradier_symbol("SHORTER") == "SHORTER"


# ── fetch_tradier_option_quote ─────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_quote_no_token_returns_none(monkeypatch):
    monkeypatch.delenv("TRADIER_API_TOKEN", raising=False)
    result = await fetch_tradier_option_quote("AAPL  261218C00200000")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_quote_happy_path():
    body = {
        "quotes": {
            "quote": {
                "symbol": "AAPL261218C00200000",
                "bid": 5.10,
                "ask": 5.35,
                "last": 5.22,
                "volume": 1234,
                "open_interest": 5678,
            }
        }
    }
    with patch("httpx.AsyncClient") as client_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=_mock_response(200, body))
        client_cls.return_value = client
        result = await fetch_tradier_option_quote(
            "AAPL  261218C00200000", token="test-token",
        )
    assert result is not None
    assert result["bid"] == 5.10
    assert result["ask"] == 5.35
    assert result["spread"] == pytest.approx(0.25, abs=0.01)
    assert result["mid"] == pytest.approx(5.225, abs=0.01)


@pytest.mark.asyncio
async def test_fetch_quote_http_error_returns_none():
    with patch("httpx.AsyncClient") as client_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=_mock_response(500, {}))
        client_cls.return_value = client
        result = await fetch_tradier_option_quote(
            "AAPL  261218C00200000", token="test-token",
        )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_quote_network_error_returns_none():
    with patch("httpx.AsyncClient") as client_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        client_cls.return_value = client
        result = await fetch_tradier_option_quote(
            "AAPL  261218C00200000", token="test-token",
        )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_quote_missing_symbol_returns_none():
    body = {"quotes": "null"}  # Tradier's literal-string-null quirk
    with patch("httpx.AsyncClient") as client_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=_mock_response(200, body))
        client_cls.return_value = client
        result = await fetch_tradier_option_quote(
            "AAPL  261218C00200000", token="test-token",
        )
    assert result is None


# ── TradierOptionsAdapter ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_adapter_enabled_only_with_token(monkeypatch):
    monkeypatch.delenv("TRADIER_API_TOKEN", raising=False)
    st = await TradierOptionsAdapter().is_options_enabled()
    assert st.enabled is False
    assert "not set" in st.details.lower()


@pytest.mark.asyncio
async def test_adapter_enabled_with_token():
    adapter = TradierOptionsAdapter(token="x")
    st = await adapter.is_options_enabled()
    assert st.enabled is True
    assert st.level == 0  # quote-only
    assert "quote" in st.details.lower()


@pytest.mark.asyncio
async def test_adapter_try_get_spread_uses_fetch():
    adapter = TradierOptionsAdapter(token="x")
    with patch(
        "services.brokers.tradier_options.fetch_tradier_option_quote",
        AsyncMock(return_value={"spread": 0.15, "bid": 1.0, "ask": 1.15}),
    ):
        spread = await adapter.try_get_spread("AAPL  261218C00200000")
    assert spread == 0.15


@pytest.mark.asyncio
async def test_adapter_try_get_spread_returns_none_on_quote_failure():
    adapter = TradierOptionsAdapter(token="x")
    with patch(
        "services.brokers.tradier_options.fetch_tradier_option_quote",
        AsyncMock(return_value=None),
    ):
        spread = await adapter.try_get_spread("AAPL  261218C00200000")
    assert spread is None


@pytest.mark.asyncio
async def test_adapter_order_methods_raise_not_implemented():
    adapter = TradierOptionsAdapter(token="x")
    with pytest.raises(BrokerNotImplementedError):
        await adapter.get_options_buying_power()
    with pytest.raises(BrokerNotImplementedError):
        await adapter.get_option_positions()
    with pytest.raises(BrokerNotImplementedError):
        await adapter.place_option_order([])
    with pytest.raises(BrokerNotImplementedError):
        await adapter.cancel_option_order("x")
