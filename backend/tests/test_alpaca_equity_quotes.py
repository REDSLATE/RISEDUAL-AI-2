"""Tests for ``services.alpaca_equity_quotes``.

Pins these invariants:

1. Symbol normalisation — case + whitespace.
2. Quote shape with both bid/ask populated → mid + spread_bps.
3. After-hours pattern (ask=0) → fall back to last trade,
   spread_bps=None.
4. Missing credentials → ``None`` / ``{}`` (never raises).
5. HTTP errors → ``{}`` / ``None``.
6. Short-TTL cache dedupes calls within the window.
7. Bypass flag forces a fresh fetch.
8. OHLC helper trims to ``lookback_bars``.
"""
from __future__ import annotations

import pytest

from services import alpaca_equity_quotes as ap


# ── Fake HTTP client ──────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:400]

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payloads_by_path):
        self._payloads = payloads_by_path
        self.calls = []

    async def get(self, url, params=None, headers=None):  # noqa: ARG002
        path = url.split("/v2/")[-1].split("?")[0]
        self.calls.append((path, dict(params or {})))
        item = self._payloads.get(path, {"status": 200, "payload": {}})
        return _FakeResp(item["status"], item["payload"])

    async def aclose(self):
        return None


# ── Symbol normalisation ──────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [("AAPL", "AAPL"), ("aapl", "AAPL"), ("  spy  ", "SPY"), ("", ""), (None, "")],
)
def test_normalise_symbol(raw, expected):
    assert ap._normalise_symbol(raw) == expected


# ── Parse quote block ────────────────────────────────────────────


def test_parse_with_both_bid_ask_returns_mid():
    out = ap._parse_quote_block(
        latest_quote={"ap": 200.0, "bp": 199.0},
        latest_trade={"p": 199.5},
    )
    assert out["bid"] == 199.0
    assert out["ask"] == 200.0
    assert out["last"] == 199.5
    assert out["price"] == 199.5  # mid
    # spread = (200-199)/199.5 * 10000 ≈ 50.13 bps
    assert 50.0 < out["spread_bps"] < 50.5
    assert out["source"] == "alpaca"


def test_parse_after_hours_zero_ask_falls_back_to_last_trade():
    """The common after-hours pattern: ap=0, bp set, last trade
    populated. Should fall back to last trade with None spread."""
    out = ap._parse_quote_block(
        latest_quote={"ap": 0, "bp": 265.18},
        latest_trade={"p": 276.69},
    )
    assert out["price"] == 276.69
    assert out["spread_bps"] is None
    # bid is still reported (it's real data) but ask drops to None
    assert out["bid"] == 265.18
    assert out["ask"] is None
    assert out["last"] == 276.69


def test_parse_returns_none_when_nothing_usable():
    """Both quote and trade empty → None (never fabricate a price)."""
    out = ap._parse_quote_block(
        latest_quote={"ap": 0, "bp": 0},
        latest_trade={"p": 0},
    )
    assert out is None


def test_parse_handles_missing_blocks():
    assert ap._parse_quote_block(latest_quote=None, latest_trade=None) is None
    assert ap._parse_quote_block(
        latest_quote=None, latest_trade={"p": 100.0},
    )["price"] == 100.0
    assert ap._parse_quote_block(
        latest_quote={"ap": 100.0, "bp": 99.0}, latest_trade=None,
    )["price"] == 99.5


# ── Credential gate ───────────────────────────────────────────────


def test_headers_none_when_creds_missing(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert ap._alpaca_headers() is None


def test_headers_built_when_creds_set(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK_test")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    h = ap._alpaca_headers()
    assert h["APCA-API-KEY-ID"] == "PK_test"
    assert h["APCA-API-SECRET-KEY"] == "secret"


# ── Batch fetch ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_happy_path(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    payload = {
        "AAPL": {
            "latestQuote": {"ap": 200.5, "bp": 200.4},
            "latestTrade": {"p": 200.45},
        },
        "SPY": {
            "latestQuote": {"ap": 0, "bp": 717.7},
            "latestTrade": {"p": 717.8},
        },
    }
    client = _FakeClient({"stocks/snapshots": {"status": 200, "payload": payload}})
    out = await ap.fetch_alpaca_equity_quotes_batch(["AAPL", "SPY"], client=client)
    assert out["AAPL"]["price"] == 200.45
    assert out["SPY"]["price"] == 717.8  # fell back to latestTrade
    # Symbols sent in request as upper-case + sorted.
    _, params = client.calls[0]
    assert params["symbols"] == "AAPL,SPY"


@pytest.mark.asyncio
async def test_batch_no_creds_returns_empty(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    client = _FakeClient({})
    out = await ap.fetch_alpaca_equity_quotes_batch(["AAPL"], client=client)
    assert out == {}
    # Never even made the HTTP call.
    assert client.calls == []


@pytest.mark.asyncio
async def test_batch_empty_list_short_circuits(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    out = await ap.fetch_alpaca_equity_quotes_batch([], client=_FakeClient({}))
    assert out == {}


@pytest.mark.asyncio
async def test_batch_http_error_returns_empty(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    client = _FakeClient(
        {"stocks/snapshots": {"status": 401, "payload": {"message": "bad creds"}}},
    )
    out = await ap.fetch_alpaca_equity_quotes_batch(["AAPL"], client=client)
    assert out == {}


@pytest.mark.asyncio
async def test_batch_drops_unparseable_symbols(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    payload = {
        "AAPL": {"latestQuote": {"ap": 100, "bp": 99}, "latestTrade": {"p": 99.5}},
        "DEAD": {"latestQuote": {"ap": 0, "bp": 0}, "latestTrade": {"p": 0}},
    }
    client = _FakeClient({"stocks/snapshots": {"status": 200, "payload": payload}})
    out = await ap.fetch_alpaca_equity_quotes_batch(["AAPL", "DEAD"], client=client)
    assert "AAPL" in out
    assert "DEAD" not in out


# ── Single-symbol cache ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_quote_uses_cache(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    ap.invalidate_cache()
    call_count = {"n": 0}

    async def _fake_batch(symbols, *, client=None):  # noqa: ARG001
        call_count["n"] += 1
        return {
            "AAPL": {
                "symbol": "AAPL", "price": 100.0, "bid": 99, "ask": 101,
                "last": 100, "spread_bps": 200, "source": "alpaca", "ts": 0.0,
            }
        }

    monkeypatch.setattr(ap, "fetch_alpaca_equity_quotes_batch", _fake_batch)
    first = await ap.get_alpaca_equity_quote("AAPL")
    second = await ap.get_alpaca_equity_quote("AAPL")
    assert first["price"] == 100.0
    assert second["price"] == 100.0
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_bypass_cache_refetches(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    ap.invalidate_cache()
    call_count = {"n": 0}

    async def _fake_batch(symbols, *, client=None):  # noqa: ARG001
        call_count["n"] += 1
        return {
            "AAPL": {
                "symbol": "AAPL", "price": 100.0, "bid": 99, "ask": 101,
                "last": 100, "spread_bps": 200, "source": "alpaca", "ts": 0.0,
            }
        }

    monkeypatch.setattr(ap, "fetch_alpaca_equity_quotes_batch", _fake_batch)
    await ap.get_alpaca_equity_quote("AAPL")
    await ap.get_alpaca_equity_quote("AAPL", bypass_cache=True)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_unknown_symbol_returns_none(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    ap.invalidate_cache()

    async def _empty_batch(symbols, *, client=None):  # noqa: ARG001
        return {}

    monkeypatch.setattr(ap, "fetch_alpaca_equity_quotes_batch", _empty_batch)
    out = await ap.get_alpaca_equity_quote("FOOBAR")
    assert out is None


# ── History (bars) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bars_trims_to_lookback(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    bars = [{"c": 100 + i} for i in range(50)]
    payload = {"bars": bars, "symbol": "AAPL"}
    client = _FakeClient({"stocks/AAPL/bars": {"status": 200, "payload": payload}})
    closes = await ap.get_alpaca_equity_history(
        "AAPL", lookback_bars=10, client=client,
    )
    assert len(closes) == 10
    assert closes[-1] == 149.0
    assert closes[0] == 140.0


@pytest.mark.asyncio
async def test_bars_no_creds_returns_empty(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    closes = await ap.get_alpaca_equity_history("AAPL", client=_FakeClient({}))
    assert closes == []


@pytest.mark.asyncio
async def test_bars_http_error_returns_empty(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    client = _FakeClient(
        {"stocks/AAPL/bars": {"status": 500, "payload": {"message": "err"}}},
    )
    closes = await ap.get_alpaca_equity_history("AAPL", client=client)
    assert closes == []
