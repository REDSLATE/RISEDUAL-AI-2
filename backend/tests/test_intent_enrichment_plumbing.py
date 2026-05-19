"""Tests for the two enrichment plumbing fixes:

1. Kraken's ticker parser now extracts ``volume_24h_base`` from the
   `v` field so the snapshot can compute ``volume_24h_usd`` properly.

2. Equity vol/trend derivation now points at the real Alpaca history
   adapter (was importing a non-existent ``price_provider.get_equity_history``).
"""
from __future__ import annotations

import pytest

from services.intent_enrichment import (
    fetch_crypto_snapshot,
    fetch_equity_snapshot,
)
from services.kraken_crypto_quotes import _parse_ticker_row


# ── Kraken parser: 24h volume extraction ────────────────────────────


def test_kraken_parser_extracts_24h_base_volume():
    """Per Kraken's ticker spec, ``v`` is
    ``[today_volume, last_24h_volume]`` in base units. We want the
    second element."""
    row = {
        "b": ["103000.0", "1", "1.0"],
        "a": ["103010.0", "1", "1.0"],
        "c": ["103005.0", "0.5"],
        "v": ["50.0", "420.123"],
    }
    out = _parse_ticker_row(row)
    assert out is not None
    assert out["volume_24h_base"] == pytest.approx(420.123)


def test_kraken_parser_omits_volume_when_field_missing():
    """If ``v`` is absent or malformed, the parser still produces a
    valid quote — just without ``volume_24h_base``."""
    row = {
        "b": ["100.0", "1", "1.0"],
        "a": ["100.5", "1", "1.0"],
        "c": ["100.25", "0.5"],
        # no `v`
    }
    out = _parse_ticker_row(row)
    assert out is not None
    assert "volume_24h_base" not in out


def test_kraken_parser_omits_volume_on_garbage_v_field():
    row = {
        "b": ["100.0", "1", "1.0"],
        "a": ["100.5", "1", "1.0"],
        "c": ["100.25", "0.5"],
        "v": "not a list",
    }
    out = _parse_ticker_row(row)
    assert out is not None
    assert "volume_24h_base" not in out


def test_kraken_parser_skips_negative_volume():
    row = {
        "b": ["100.0", "1", "1.0"],
        "a": ["100.5", "1", "1.0"],
        "c": ["100.25", "0.5"],
        "v": ["50.0", "-1.0"],
    }
    out = _parse_ticker_row(row)
    assert out is not None
    assert "volume_24h_base" not in out


# ── Snapshot now produces volume_24h_usd ────────────────────────────


@pytest.mark.asyncio
async def test_crypto_snapshot_computes_volume_24h_usd(monkeypatch):
    """Kraken returns base volume + mid price → enrichment multiplies
    to produce USD notional. This was always None before the parser
    fix."""
    async def _ok(symbols, **k):
        return {"BTC": {
            "bid": 103000.0, "ask": 103010.0, "price": 103005.0,
            "last": 103005.0, "spread_bps": 0.97,
            "volume_24h_base": 420.123,
            "source": "kraken", "ts": 0.0,
        }}

    import services.kraken_crypto_quotes as kq
    monkeypatch.setattr(kq, "fetch_kraken_quotes_batch", _ok)

    async def _no_history(symbol, lookback_bars=60):
        return []
    import services.crypto_quotes as cq
    monkeypatch.setattr(cq, "get_crypto_history", _no_history)

    snap = await fetch_crypto_snapshot("BTC/USD")
    expected_usd = 103005.0 * 420.123
    assert snap["volume_24h_usd"] == pytest.approx(expected_usd, rel=1e-3)


# ── Equity vol/trend now wires to the real adapter ─────────────────


@pytest.mark.asyncio
async def test_equity_snapshot_uses_alpaca_history_adapter(monkeypatch):
    """Equity vol/trend was importing a non-existent
    `price_provider.get_equity_history`. After the fix it calls
    `alpaca_equity_quotes.get_alpaca_equity_history` directly with
    a 1Hour timeframe."""
    # Stub the alpaca quote so the rest of the snapshot path returns.
    async def _quote_stub(symbol):
        return {
            "bid": 100.0, "ask": 100.05, "spread_bps": 5.0,
        }
    import services.alpaca_equity_quotes as aq
    monkeypatch.setattr(aq, "get_alpaca_equity_quote", _quote_stub)

    # Stub the history adapter — capture the call args to assert the
    # wire goes to the right place with the right params.
    seen = {}

    async def _hist_stub(symbol, *, lookback_bars=60, timeframe="1Day", client=None):
        seen["symbol"] = symbol
        seen["lookback_bars"] = lookback_bars
        seen["timeframe"] = timeframe
        return [100.0, 100.5, 101.0, 100.8, 101.5, 102.0]

    monkeypatch.setattr(aq, "get_alpaca_equity_history", _hist_stub)

    snap = await fetch_equity_snapshot("NVDA")

    # The fix calls Alpaca's history adapter, not some non-existent
    # price_provider import.
    assert seen["symbol"] == "NVDA"
    assert seen["timeframe"] == "1Hour"  # hourly bars, not daily
    assert seen["lookback_bars"] == 60

    # And vol/trend land as real numbers, not None.
    assert snap["volatility_1h"] is not None
    assert snap["volatility_1h"] > 0
    assert snap["trend_strength"] is not None
    assert -1.0 <= snap["trend_strength"] <= 1.0


@pytest.mark.asyncio
async def test_equity_snapshot_degrades_to_none_when_history_fails(monkeypatch):
    """If Alpaca history raises, vol/trend stay None and the snapshot
    still ships (with the remaining 5 fields populated)."""
    async def _quote_stub(symbol):
        return {
            "bid": 100.0, "ask": 100.05, "spread_bps": 5.0,
        }
    import services.alpaca_equity_quotes as aq
    monkeypatch.setattr(aq, "get_alpaca_equity_quote", _quote_stub)

    async def _boom(*a, **k):
        raise RuntimeError("alpaca down")
    monkeypatch.setattr(aq, "get_alpaca_equity_history", _boom)

    snap = await fetch_equity_snapshot("NVDA")
    assert snap["volatility_1h"] is None
    assert snap["trend_strength"] is None
    # Other fields still populated.
    assert snap["bid"] == 100.0
    assert snap["spread_bps"] == 5.0
    assert snap["snapshot_status"] == "ok"
