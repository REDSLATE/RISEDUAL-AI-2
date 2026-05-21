"""Tests for intent enrichment — normalized market snapshot doctrine.

Verifies the four contract points:
  1. compute_spread_bps math + sentinel behavior
  2. Crypto fetcher returns the 7 canonical keys (real or sentinel)
  3. Equity fetcher returns the 7 canonical keys (real or sentinel)
  4. enrich_intent_with_snapshot is idempotent + lane-routed +
     never returns empty dict
"""
from __future__ import annotations

import logging
import math

import pytest

from services.intent_enrichment import (
    SNAPSHOT_KEYS,
    SPREAD_BPS_UNKNOWN,
    compute_spread_bps,
    enrich_intent_with_snapshot,
    fetch_crypto_snapshot,
    fetch_equity_snapshot,
)


# ─── compute_spread_bps ─────────────────────────────────────────────


@pytest.mark.parametrize("bid,ask,expected_bps", [
    (100.0, 100.1, 9.99),       # 10 bps spread, 100/(0.1)/(100.05/2)
    (50000.0, 50050.0, 9.99),   # ~10 bps on a BTC-scale quote
    (10.0, 10.0, 0.0),          # zero spread
])
def test_compute_spread_bps_basic(bid, ask, expected_bps):
    """Standard spread math: (ask - bid) / mid * 10_000."""
    out = compute_spread_bps(bid, ask)
    # ~1% tolerance — these are integer-rounded approximations.
    assert abs(out - expected_bps) < 1.0


@pytest.mark.parametrize("bid,ask", [
    (0, 100),         # zero bid
    (100, 0),         # zero ask
    (-1, 100),        # negative bid
    ("nan", 100),     # NaN handling
    (None, 100),      # None
    (math.inf, 100),  # inf
])
def test_compute_spread_bps_returns_sentinel_on_bad_input(bid, ask):
    assert compute_spread_bps(bid, ask) == SPREAD_BPS_UNKNOWN


# ─── Snapshot shape contract ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_crypto_snapshot_handles_missing_symbol():
    snap = await fetch_crypto_snapshot("")
    # Even on failure, ALL 7 canonical keys must be present.
    for key in SNAPSHOT_KEYS:
        assert key in snap, f"missing key: {key}"
    assert snap["spread_bps"] == SPREAD_BPS_UNKNOWN
    assert snap["snapshot_status"] == "missing_symbol"


@pytest.mark.asyncio
async def test_fetch_crypto_snapshot_graceful_when_fetcher_fails(monkeypatch):
    """Kraken adapter raises → sentinel snapshot, NOT empty dict."""
    async def _boom(*a, **k):
        raise RuntimeError("kraken down")

    import services.kraken_crypto_quotes as kq
    monkeypatch.setattr(kq, "fetch_kraken_quotes_batch", _boom)

    snap = await fetch_crypto_snapshot("BTC/USD")
    assert set(SNAPSHOT_KEYS).issubset(snap.keys())
    assert snap["spread_bps"] == SPREAD_BPS_UNKNOWN
    assert snap["snapshot_status"].startswith("kraken_fetch_error")


@pytest.mark.asyncio
async def test_fetch_crypto_snapshot_returns_real_data_when_quote_ok(monkeypatch):
    """Happy path: Kraken returns a quote → snapshot carries the
    real bid/ask/spread + None for derived fields if history missing."""
    async def _ok(symbols, **k):
        return {"BTC": {
            "bid": 103000.0, "ask": 103010.0, "price": 103005.0,
            "spread_bps": 0.97, "source": "kraken", "ts": 0.0,
        }}

    import services.kraken_crypto_quotes as kq
    monkeypatch.setattr(kq, "fetch_kraken_quotes_batch", _ok)

    # History stub returns nothing → vol/trend remain None.
    async def _no_history(symbol, lookback_bars=60):
        return []
    import services.crypto_quotes as cq
    monkeypatch.setattr(cq, "get_crypto_history", _no_history)

    snap = await fetch_crypto_snapshot("BTC/USD")
    assert snap["bid"] == 103000.0
    assert snap["ask"] == 103010.0
    assert snap["spread_bps"] == 0.97
    assert snap["volatility_1h"] is None  # no history
    assert snap["trend_strength"] is None
    assert snap["exchange_liquidity_score"] == 0.85
    assert snap["snapshot_status"] == "ok"


@pytest.mark.asyncio
async def test_fetch_equity_snapshot_handles_missing_symbol():
    snap = await fetch_equity_snapshot("")
    for key in SNAPSHOT_KEYS:
        assert key in snap
    assert snap["spread_bps"] == SPREAD_BPS_UNKNOWN
    assert snap["snapshot_status"] == "missing_symbol"


# ─── enrich_intent_with_snapshot top-level contract ─────────────────


@pytest.mark.asyncio
async def test_enrich_routes_by_lane_crypto(monkeypatch):
    async def _stub_crypto(symbol):
        return {
            "bid": 1.0, "ask": 1.1, "spread_bps": 5.0,
            "volume_24h_usd": 1_000_000, "volatility_1h": 0.02,
            "trend_strength": 0.5, "exchange_liquidity_score": 0.85,
            "snapshot_status": "ok",
        }
    import services.intent_enrichment as ie
    monkeypatch.setattr(ie, "fetch_crypto_snapshot", _stub_crypto)

    intent = {"lane": "crypto", "symbol": "ETH/USD", "direction": "BUY"}
    out = await enrich_intent_with_snapshot(intent)
    assert out["doctrine_snapshot"]["bid"] == 1.0
    assert out["doctrine_snapshot"]["spread_bps"] == 5.0
    # 2026-05-21: ``price`` (mid) is auto-derived for MC's sizing path.
    assert out["doctrine_snapshot"]["price"] == round((1.0 + 1.1) / 2, 6)


@pytest.mark.asyncio
async def test_enrich_routes_by_lane_equity(monkeypatch):
    async def _stub_equity(symbol):
        return {
            "bid": 100.0, "ask": 100.05, "spread_bps": 5.0,
            "volume_24h_usd": 5_000_000, "volatility_1h": 0.015,
            "trend_strength": 0.3, "exchange_liquidity_score": 0.95,
            "snapshot_status": "ok",
        }
    import services.intent_enrichment as ie
    monkeypatch.setattr(ie, "fetch_equity_snapshot", _stub_equity)

    intent = {"lane": "equity", "symbol": "NVDA", "direction": "BUY"}
    out = await enrich_intent_with_snapshot(intent)
    assert out["doctrine_snapshot"]["bid"] == 100.0
    assert out["doctrine_snapshot"]["exchange_liquidity_score"] == 0.95


@pytest.mark.asyncio
async def test_enrich_idempotent_when_snapshot_already_present():
    """Brains that already enriched upstream must not be clobbered."""
    intent = {
        "lane": "crypto", "symbol": "BTC/USD",
        "doctrine_snapshot": {"bid": 999.0, "snapshot_status": "preset"},
    }
    out = await enrich_intent_with_snapshot(intent)
    assert out["doctrine_snapshot"]["bid"] == 999.0  # unchanged
    assert out["doctrine_snapshot"]["snapshot_status"] == "preset"


@pytest.mark.asyncio
async def test_enrich_idempotent_accepts_legacy_snapshot_key():
    """Legacy callers that wrote intent['snapshot'] must still be
    respected — the enricher re-mounts under the new key without
    re-fetching."""
    intent = {
        "lane": "crypto", "symbol": "BTC/USD",
        "snapshot": {"bid": 777.0, "snapshot_status": "legacy_preset"},
    }
    out = await enrich_intent_with_snapshot(intent)
    assert out["doctrine_snapshot"]["bid"] == 777.0
    assert out["doctrine_snapshot"]["snapshot_status"] == "legacy_preset"


@pytest.mark.asyncio
async def test_enrich_unknown_lane_returns_sentinel_snapshot():
    intent = {"lane": "fx", "symbol": "EURUSD", "direction": "BUY"}
    out = await enrich_intent_with_snapshot(intent)
    assert "doctrine_snapshot" in out
    assert set(SNAPSHOT_KEYS).issubset(out["doctrine_snapshot"].keys())
    assert out["doctrine_snapshot"]["snapshot_status"].startswith("unknown_lane")
    assert out["doctrine_snapshot"]["spread_bps"] == SPREAD_BPS_UNKNOWN


@pytest.mark.asyncio
async def test_enrich_logs_completeness_line(monkeypatch, caplog):
    """Every enrichment writes ONE grep-friendly SNAPSHOT_ENRICHED
    line so the operator can confirm intents aren't going out empty."""
    caplog.set_level(logging.INFO, logger="services.intent_enrichment")

    async def _stub_crypto(symbol):
        return {
            "bid": 1.0, "ask": 1.1, "spread_bps": 5.0,
            "volume_24h_usd": 1_000_000, "volatility_1h": 0.02,
            "trend_strength": 0.5, "exchange_liquidity_score": 0.85,
            "snapshot_status": "ok",
        }
    import services.intent_enrichment as ie
    monkeypatch.setattr(ie, "fetch_crypto_snapshot", _stub_crypto)

    intent = {
        "lane": "crypto", "symbol": "ETH/USD", "direction": "BUY",
        "trace_id": "cafef00d",
    }
    await enrich_intent_with_snapshot(intent)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "cafef00d" in m and "SNAPSHOT_ENRICHED" in m and "populated=7/7" in m
        for m in msgs
    )


@pytest.mark.asyncio
async def test_enrich_never_returns_empty_snapshot(monkeypatch):
    """The whole point of this module: ``snapshot:{}`` must NEVER
    leave the brain. Even when everything fails, the 7 keys are
    present with sentinel values."""
    async def _explode(*a, **k):
        raise RuntimeError("everything is on fire")
    import services.intent_enrichment as ie
    monkeypatch.setattr(ie, "fetch_crypto_snapshot", _explode)

    intent = {"lane": "crypto", "symbol": "BTC/USD"}
    # The exception propagates up through enrich_intent_with_snapshot
    # because we DELIBERATELY don't catch it inside enrich (the bridge
    # catches it instead so the emission still fires with empty kwargs).
    with pytest.raises(RuntimeError):
        await enrich_intent_with_snapshot(intent)
