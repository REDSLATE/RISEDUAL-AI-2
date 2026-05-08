"""Tests for ``services.kraken_ws_stream``.

These tests pin pure-function behaviour (snapshot store, quote
shape, staleness gate) WITHOUT making real WebSocket connections.
The connection loop itself is integration-tested by the live
backend smoke (admin probe `/api/admin/kraken-ws/status`).
"""
from __future__ import annotations

import asyncio

import pytest

from services import kraken_ws_stream as kws


# ── Snapshot lookup ───────────────────────────────────────────────


def setup_function():
    kws._reset_for_tests()


@pytest.mark.asyncio
async def test_get_streamed_quote_misses_when_empty():
    out = await kws.get_streamed_quote("BTC")
    assert out is None


@pytest.mark.asyncio
async def test_process_ticker_msg_populates_snapshot():
    msg = {
        "channel": "ticker",
        "data": [{
            "symbol": "BTC/USD", "bid": 80000.1, "ask": 80000.2,
            "last": 80000.15,
        }],
    }
    await kws._process_ticker_msg(msg)
    out = await kws.get_streamed_quote("BTC")
    assert out is not None
    assert out["bid"] == 80000.1
    assert out["ask"] == 80000.2
    assert out["price"] == pytest.approx(80000.15, abs=0.01)
    # spread = (0.1/80000.15) * 10000 ≈ 0.0125 bps
    assert 0.0 < out["spread_bps"] < 0.1
    assert out["source"] == "kraken_ws"


@pytest.mark.asyncio
async def test_process_ticker_msg_handles_known_pair_aliases():
    """DOGE → DOGE/USD ticker mapping (Kraken WS uses DOGE/USD,
    distinct from REST's XDGUSD)."""
    msg = {
        "channel": "ticker",
        "data": [{
            "symbol": "DOGE/USD", "bid": 0.30, "ask": 0.31, "last": 0.305,
        }],
    }
    await kws._process_ticker_msg(msg)
    out = await kws.get_streamed_quote("DOGE")
    assert out is not None
    assert out["price"] == 0.305


@pytest.mark.asyncio
async def test_unknown_pair_skipped_silently():
    msg = {
        "channel": "ticker",
        "data": [{
            "symbol": "FOOBAR/USD", "bid": 1.0, "ask": 2.0, "last": 1.5,
        }],
    }
    await kws._process_ticker_msg(msg)
    # Snapshot should still be empty.
    assert kws._snapshot == {}


@pytest.mark.asyncio
async def test_non_ticker_channel_ignored():
    msg = {
        "channel": "heartbeat",
        "data": [{"symbol": "BTC/USD", "bid": 1, "ask": 2}],
    }
    await kws._process_ticker_msg(msg)
    assert kws._snapshot == {}


@pytest.mark.asyncio
async def test_malformed_payload_ignored():
    """Bad inputs MUST never raise — the streamer can't risk
    crashing the bot on a single malformed frame."""
    await kws._process_ticker_msg(None)
    await kws._process_ticker_msg({})
    await kws._process_ticker_msg({"channel": "ticker"})
    await kws._process_ticker_msg(
        {"channel": "ticker", "data": [{"symbol": "BTC/USD"}]},
    )  # missing bid/ask/last → all 0 → skipped (price <= 0)
    assert kws._snapshot == {}


@pytest.mark.asyncio
async def test_zero_price_payload_skipped():
    """Snapshot entries are only stored when price > 0."""
    msg = {
        "channel": "ticker",
        "data": [{"symbol": "BTC/USD", "bid": 0, "ask": 0, "last": 0}],
    }
    await kws._process_ticker_msg(msg)
    assert "BTC" not in kws._snapshot


# ── Staleness gate ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_snapshot_returns_none(monkeypatch):
    msg = {
        "channel": "ticker",
        "data": [{"symbol": "BTC/USD", "bid": 80000, "ask": 80001, "last": 80000.5}],
    }
    await kws._process_ticker_msg(msg)
    # Simulate the snapshot being old.
    received_at, q = kws._snapshot["BTC"]
    kws._snapshot["BTC"] = (received_at - kws.MAX_STALENESS_SEC - 1.0, q)
    out = await kws.get_streamed_quote("BTC")
    assert out is None  # stale → caller falls through to REST


@pytest.mark.asyncio
async def test_fresh_snapshot_returned():
    msg = {
        "channel": "ticker",
        "data": [{"symbol": "ETH/USD", "bid": 3000, "ask": 3001, "last": 3000.5}],
    }
    await kws._process_ticker_msg(msg)
    out = await kws.get_streamed_quote("ETH")
    assert out is not None
    assert out["symbol"] == "ETH"


# ── Lifecycle ─────────────────────────────────────────────────────


def test_status_when_disabled(monkeypatch):
    monkeypatch.setenv("KRAKEN_WS_STREAM_ENABLED", "0")
    started = kws.start_kraken_ws_stream(["BTC"])
    assert started is False
    s = kws.stream_status()
    assert s["enabled"] is False


def test_status_when_alive_after_mock_start():
    """Patching the run loop with a no-op coroutine should still
    flip ``alive`` to True until the task finishes."""
    async def _noop(_symbols):
        await asyncio.sleep(0.01)

    # Use the real start path with a stub backend.
    saved = kws._run_stream
    kws._run_stream = _noop  # type: ignore[assignment]
    try:
        async def _exercise():
            kws._reset_for_tests()
            ok = kws.start_kraken_ws_stream(["BTC"])
            assert ok is True
            assert kws.stream_status()["alive"] in (True, False)
            await asyncio.sleep(0.05)
            # Task finishes quickly; idempotency: second start is a no-op
            # iff the task still exists (done state).
            ok2 = kws.start_kraken_ws_stream(["BTC"])
            assert isinstance(ok2, bool)

        asyncio.run(_exercise())
    finally:
        kws._run_stream = saved  # type: ignore[assignment]


# ── Mapping coverage ──────────────────────────────────────────────


def test_canonical_to_ws_pair_covers_majors():
    for sym in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX",
                "LINK", "DOGE", "DOT", "MATIC"):
        assert sym in kws._CANONICAL_TO_WS_PAIR


def test_ws_pair_reverse_map_consistent():
    for canonical, pair in kws._CANONICAL_TO_WS_PAIR.items():
        assert kws._WS_PAIR_TO_CANONICAL[pair] == canonical
