"""Tests for Phase F.2: MooMoo L2 wire-up + broker selector wiring.

Focus: L2 remains a non-blocking modifier. Missing / off-source MUST
resolve to None so ``Level2Confirmation.score(None) == 0.50``.
"""
from __future__ import annotations

import pytest

from services import alpha_day_trader as adt


def test_l2_source_defaults_to_none(monkeypatch):
    monkeypatch.delenv("RISEDUAL_ALPHA_L2_SOURCE", raising=False)
    assert adt._l2_source() == "none"


def test_l2_fetch_returns_none_when_source_off(monkeypatch):
    monkeypatch.delenv("RISEDUAL_ALPHA_L2_SOURCE", raising=False)
    # Off-source path must not touch MooMoo at all.
    called = {"moomoo": False}

    def _explode(*_args, **_kw):
        called["moomoo"] = True
        raise AssertionError("moomoo adapter must not be called when source=none")

    import services.moomoo_market_data_adapter as md
    monkeypatch.setattr(md, "to_level2_snapshot", _explode)
    assert adt._fetch_l2_snapshot("AAPL") is None
    assert called["moomoo"] is False


def test_l2_fetch_moomoo_source_returns_none_when_adapter_down(monkeypatch):
    monkeypatch.setenv("RISEDUAL_ALPHA_L2_SOURCE", "moomoo")
    import services.moomoo_market_data_adapter as md
    monkeypatch.setattr(md, "to_level2_snapshot", lambda _sym: None)
    # Adapter down → None → Level2Confirmation returns neutral 0.50
    assert adt._fetch_l2_snapshot("AAPL") is None


def test_l2_fetch_moomoo_source_returns_snapshot(monkeypatch):
    monkeypatch.setenv("RISEDUAL_ALPHA_L2_SOURCE", "moomoo")
    import services.moomoo_market_data_adapter as md
    monkeypatch.setattr(md, "to_level2_snapshot", lambda _sym: {
        "symbol": "AAPL", "bid_size": 1000, "ask_size": 500,
        "book_imbalance": 0.33, "tape_delta": 0.0,
        "cancel_rate_bid": 0.0, "cancel_rate_ask": 0.0,
        "imbalance_persistence_ms": 0,
    })
    snap = adt._fetch_l2_snapshot("AAPL")
    assert snap is not None
    assert snap.symbol == "AAPL"
    assert snap.bid_size == 1000
    # Level2Confirmation with a positive imbalance should bump above 0.50
    from services.alpha_day_trader import Level2Confirmation
    score = Level2Confirmation().score(snap)
    assert score > 0.50


def test_l2_confirmation_still_neutral_for_none():
    """Critical safety property: L2 never blocks. None → 0.50."""
    from services.alpha_day_trader import Level2Confirmation
    assert Level2Confirmation().score(None) == 0.50


# ── broker router with per-bot config ────────────────

@pytest.mark.asyncio
async def test_router_reads_bot_config_from_mongo():
    from services.broker_router import resolve_broker

    class _FakeCursor:
        def __init__(self, doc):
            self._doc = doc

        def __await__(self):
            async def _fn():
                return self._doc
            return _fn().__await__()

    class _Bots:
        async def find_one(self, _q, _p=None):
            return {"broker": "moomoo"}

    class _DB:
        bots = _Bots()

    result = await resolve_broker(_DB(), {"bot_id": "abc123"})
    assert result == "moomoo"


@pytest.mark.asyncio
async def test_router_falls_back_when_bot_missing():
    from services.broker_router import resolve_broker

    class _Bots:
        async def find_one(self, _q, _p=None):
            return None

    class _DB:
        bots = _Bots()

    result = await resolve_broker(_DB(), {"bot_id": "does-not-exist"})
    assert result == "public"
