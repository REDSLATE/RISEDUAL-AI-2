"""Tests for ``services.kraken_crypto_quotes``.

Pins these invariants:

1. Symbol normalisation — BTC/BTC-USD/BTCUSDT all resolve to ``BTC``.
2. Response-key normalisation — Kraken's prefixed codes
   (``XXBTZUSD`` / ``XETHZUSD`` / ``XDGUSD``) map back to canonical
   symbols.
3. Bid/ask/mid/spread math is correct.
4. Network failure returns ``{}`` / ``None`` — never raises.
5. Short-TTL cache dedupes calls within the window.
6. Bypass flag skips cache.
7. OHLC helper trims to requested ``lookback_bars``.
"""
from __future__ import annotations

import pytest

from services import kraken_crypto_quotes as kq


# ── Tiny stub HTTP client ──────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:400]

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payloads_by_path: dict):
        self._payloads = payloads_by_path
        self.calls = []

    async def get(self, url, params=None):  # noqa: ARG002
        path = url.rsplit("/", 1)[-1]
        self.calls.append((path, dict(params or {})))
        item = self._payloads.get(path, {"status": 200, "payload": {"result": {}}})
        return _FakeResp(item["status"], item["payload"])

    async def aclose(self):  # noqa: D401
        return None


# ── Symbol normalisation ──────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC", "BTC"),
        ("btc", "BTC"),
        ("BTC/USD", "BTC"),
        ("BTC-USD", "BTC"),
        ("BTCUSDT", "BTC"),
        ("BTCUSDC", "BTC"),
        ("BTCUSD", "BTC"),
        ("  eth  ", "ETH"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalise_canonical(raw, expected):
    assert kq._normalise_canonical(raw) == expected


def test_canonical_to_kraken_covers_all_major_aliases():
    # Doge must map to Kraken's XDGUSD, not DOGEUSD.
    assert kq._to_kraken_pair("DOGE") == "XDGUSD"
    assert kq._to_kraken_pair("BTC") == "XBTUSD"


def test_unknown_symbol_returns_none_pair():
    assert kq._to_kraken_pair("FOOBAR") is None


# ── Response key normalisation ────────────────────────────────────


def test_kraken_response_key_prefixes_handled():
    assert kq._from_kraken_pair("XXBTZUSD") == "BTC"
    assert kq._from_kraken_pair("XETHZUSD") == "ETH"
    assert kq._from_kraken_pair("XDGUSD") == "DOGE"
    assert kq._from_kraken_pair("XXRPZUSD") == "XRP"
    assert kq._from_kraken_pair("SOLUSD") == "SOL"
    assert kq._from_kraken_pair("NOT_A_PAIR") is None


# ── Parse one Kraken ticker row ───────────────────────────────────


def test_parse_ticker_row_bid_ask_mid():
    row = {
        "a": ["81000.0", "1", "1"],
        "b": ["80000.0", "1", "1"],
        "c": ["80500.0", "0.1"],
    }
    out = kq._parse_ticker_row(row)
    assert out["bid"] == 80000.0
    assert out["ask"] == 81000.0
    assert out["last"] == 80500.0
    assert out["price"] == 80500.0  # (80000 + 81000)/2
    # spread = (81000-80000)/80500 * 10000 ≈ 124.22 bps
    assert 124.0 < out["spread_bps"] < 125.0
    assert out["source"] == "kraken"


def test_parse_ticker_row_rejects_zero_prices():
    """Zero bid/ask should produce None — never a zero-price fill."""
    assert kq._parse_ticker_row({"a": ["0.0", "1"], "b": ["0.0", "1"], "c": ["0.0", "1"]}) is None


def test_parse_ticker_row_rejects_malformed():
    assert kq._parse_ticker_row({}) is None
    assert kq._parse_ticker_row({"a": ["not_a_number"], "b": ["80000"], "c": ["80500"]}) is None


# ── Batch fetch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_batch_happy_path():
    payload = {
        "error": [],
        "result": {
            "XXBTZUSD": {"a": ["80500", "1", "1"], "b": ["80400", "1", "1"], "c": ["80450", "0.1"]},
            "SOLUSD": {"a": ["85.0", "1", "1"], "b": ["84.9", "1", "1"], "c": ["84.95", "0.1"]},
        },
    }
    client = _FakeClient({"Ticker": {"status": 200, "payload": payload}})
    out = await kq.fetch_kraken_quotes_batch(["BTC", "SOL"], client=client)
    assert out["BTC"]["price"] == 80450.0
    assert out["SOL"]["price"] == 84.95
    # Kraken saw the canonical pair codes we mapped to.
    _, params = client.calls[0]
    assert "XBTUSD" in params["pair"]
    assert "SOLUSD" in params["pair"]


@pytest.mark.asyncio
async def test_fetch_batch_unknown_symbols_filtered():
    """Unknown symbols get dropped before the request is issued."""
    payload = {"error": [], "result": {}}
    client = _FakeClient({"Ticker": {"status": 200, "payload": payload}})
    out = await kq.fetch_kraken_quotes_batch(["UNKNOWNCOIN"], client=client)
    assert out == {}
    # No HTTP call made because there were no resolvable symbols.
    assert client.calls == []


@pytest.mark.asyncio
async def test_fetch_batch_empty_list_short_circuits():
    out = await kq.fetch_kraken_quotes_batch([], client=_FakeClient({}))
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_batch_http_error_returns_empty():
    client = _FakeClient(
        {"Ticker": {"status": 503, "payload": {"error": ["server down"]}}},
    )
    out = await kq.fetch_kraken_quotes_batch(["BTC"], client=client)
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_batch_partial_result_with_errors():
    """Kraken may return ``error: [...]`` + partial ``result: {...}``
    when some requested pairs are unknown. We must keep the good
    rows and drop the bad ones silently."""
    payload = {
        "error": ["EQuery:Unknown asset pair"],
        "result": {
            "XXBTZUSD": {"a": ["80500", "1", "1"], "b": ["80400", "1", "1"], "c": ["80450", "0.1"]},
        },
    }
    client = _FakeClient({"Ticker": {"status": 200, "payload": payload}})
    out = await kq.fetch_kraken_quotes_batch(["BTC", "MATIC"], client=client)
    assert "BTC" in out
    assert "MATIC" not in out


# ── Single-symbol cache ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_single_quote_uses_cache(monkeypatch):
    kq.invalidate_cache()
    call_count = {"n": 0}

    async def _fake_batch(symbols, *, client=None):  # noqa: ARG001
        call_count["n"] += 1
        return {
            "BTC": {
                "symbol": "BTC", "price": 80000.0, "bid": 79990, "ask": 80010,
                "last": 80000, "spread_bps": 2.5, "source": "kraken", "ts": 0.0,
            }
        }

    monkeypatch.setattr(kq, "fetch_kraken_quotes_batch", _fake_batch)
    first = await kq.get_kraken_crypto_quote("BTC")
    second = await kq.get_kraken_crypto_quote("BTC")
    assert first["price"] == 80000.0
    assert second["price"] == 80000.0
    # Second call should have come from the cache, not hit the batch.
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_bypass_cache(monkeypatch):
    kq.invalidate_cache()
    call_count = {"n": 0}

    async def _fake_batch(symbols, *, client=None):  # noqa: ARG001
        call_count["n"] += 1
        return {
            "BTC": {
                "symbol": "BTC", "price": 80000.0, "bid": 79990, "ask": 80010,
                "last": 80000, "spread_bps": 2.5, "source": "kraken", "ts": 0.0,
            }
        }

    monkeypatch.setattr(kq, "fetch_kraken_quotes_batch", _fake_batch)
    await kq.get_kraken_crypto_quote("BTC")
    await kq.get_kraken_crypto_quote("BTC", bypass_cache=True)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_unknown_symbol_returns_none():
    out = await kq.get_kraken_crypto_quote("UNKNOWNCOIN")
    assert out is None


# ── OHLC helper ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ohlc_trims_to_lookback():
    # Build 100 synthetic daily rows with ascending closes.
    synth_rows = [
        [i * 86400, 0, 0, 0, float(100 + i), 0, 0, 0]
        for i in range(100)
    ]
    payload = {
        "error": [],
        "result": {"XXBTZUSD": synth_rows, "last": 99},
    }
    client = _FakeClient({"OHLC": {"status": 200, "payload": payload}})
    closes = await kq.get_kraken_crypto_history(
        "BTC", lookback_bars=30, client=client,
    )
    assert len(closes) == 30
    # The TAIL (newest 30) should be returned.
    assert closes[-1] == 199.0
    assert closes[0] == 170.0


@pytest.mark.asyncio
async def test_ohlc_unknown_symbol_returns_empty():
    closes = await kq.get_kraken_crypto_history("FOOBAR", client=_FakeClient({}))
    assert closes == []


@pytest.mark.asyncio
async def test_ohlc_http_error_returns_empty():
    client = _FakeClient(
        {"OHLC": {"status": 500, "payload": {"error": ["server down"]}}},
    )
    closes = await kq.get_kraken_crypto_history("BTC", client=client)
    assert closes == []
