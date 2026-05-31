"""Tests — Crypto → MC intent emitter (2026-05-31 Phase B).

Covers:
- Receipt construction maps direction → action correctly
- HOLD/non-directional verdicts short-circuit before MC contact
- Missing MC config short-circuits without raising
- Successful path routes through ``emit_intent_from_consensus``
- MC errors are swallowed (fail-soft contract)
- Env kill switch ``RISEDUAL_CRYPTO_EMIT_INTENTS=false`` disables fully
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from services.crypto_mc_intent_emitter import (
    _build_receipt,
    _to_action,
    emit_crypto_intent,
)


# ── _to_action mapping ─────────────────────────────────────────────


def test_to_action_long_maps_to_buy():
    assert _to_action("LONG") == "BUY"


def test_to_action_short_maps_to_sell():
    """Wire-level: MC's ``side`` schema is {BUY, SELL, HOLD}. SHORT
    crypto positions are SELL at the wire level (sell spot or sell
    short via margin). The original direction is preserved on the
    receipt's ``direction`` field for downstream consumers."""
    assert _to_action("SHORT") == "SELL"


def test_to_action_cover_maps_to_buy():
    assert _to_action("COVER") == "BUY"


def test_to_action_handles_lowercase():
    assert _to_action("long") == "BUY"
    assert _to_action("short") == "SELL"


def test_to_action_returns_none_for_hold():
    assert _to_action("HOLD") is None
    assert _to_action("") is None


# ── _build_receipt ────────────────────────────────────────────────


def test_build_receipt_populates_canonical_fields():
    trade = {
        "symbol": "BTC", "direction": "SHORT", "confidence": 0.733,
        "entry_price": 73811.30, "size": 0.00144829, "size_usd": 106.90,
        "trade_id": "trade-abc",
    }
    signal = {"spread_bps": 4.1, "relative_volume": 1.6, "has_news": False}
    receipt = _build_receipt(trade, signal)
    assert receipt is not None
    assert receipt["symbol"] == "BTC"
    # verdict is wire-level (BUY/SELL/HOLD); direction is semantic.
    assert receipt["verdict"] == "SELL"
    assert receipt["direction"] == "SELL"
    assert receipt["lane"] == "crypto"
    assert receipt["confidence"] == pytest.approx(0.733)
    assert receipt["entry_price"] == pytest.approx(73811.30)
    assert receipt["snapshot"]["price"] == pytest.approx(73811.30)
    assert receipt["snapshot"]["spread_bps"] == 4.1
    assert receipt["snapshot"]["has_news"] is False
    assert receipt["trade_id"] == "trade-abc"


def test_build_receipt_returns_none_for_hold():
    trade = {"symbol": "BTC", "direction": "HOLD"}
    assert _build_receipt(trade, {}) is None


def test_build_receipt_handles_missing_confidence():
    trade = {"symbol": "ETH", "direction": "LONG"}
    receipt = _build_receipt(trade, {"confidence": 0.5})
    assert receipt is not None
    assert receipt["confidence"] == pytest.approx(0.5)


# ── emit_crypto_intent ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_skips_when_kill_switch_set(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "false")
    trade = {"symbol": "BTC", "direction": "LONG", "entry_price": 100.0,
             "size": 0.1, "trade_id": "x"}
    result = await emit_crypto_intent(trade, {})
    assert result is None


@pytest.mark.asyncio
async def test_emit_skips_for_hold(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    trade = {"symbol": "BTC", "direction": "HOLD"}
    result = await emit_crypto_intent(trade, {})
    assert result is None


@pytest.mark.asyncio
async def test_emit_skips_when_missing_mc_url(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    monkeypatch.delenv("RISEDUAL_MC_URL", raising=False)
    monkeypatch.delenv("MC_URL", raising=False)
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    trade = {"symbol": "BTC", "direction": "LONG", "entry_price": 100.0,
             "size": 0.1, "trade_id": "x"}
    result = await emit_crypto_intent(trade, {})
    assert result is None


@pytest.mark.asyncio
async def test_emit_skips_when_missing_token(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.delenv("ALPHA_MC_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("ALPHA_INGEST_TOKEN", raising=False)
    trade = {"symbol": "BTC", "direction": "LONG", "entry_price": 100.0,
             "size": 0.1, "trade_id": "x"}
    result = await emit_crypto_intent(trade, {})
    assert result is None


@pytest.mark.asyncio
async def test_emit_calls_intent_bridge_on_happy_path(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")

    trade = {
        "symbol": "BTC", "direction": "SHORT", "confidence": 0.733,
        "entry_price": 73811.30, "size": 0.00144829, "size_usd": 106.90,
        "trade_id": "trade-abc",
    }
    signal = {"spread_bps": 4.1, "relative_volume": 1.6, "has_news": False,
              "confidence": 0.733}

    with patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=AsyncMock(return_value={"ok": True, "verdict": "dry_run_passed"}),
    ) as mock_emit:
        result = await emit_crypto_intent(trade, signal)

    assert result == {"ok": True, "verdict": "dry_run_passed"}
    assert mock_emit.await_count == 1
    # Receipt passed to intent_bridge MUST tag lane=crypto.
    _client_arg, receipt_arg = mock_emit.await_args.args
    assert receipt_arg["lane"] == "crypto"
    assert receipt_arg["symbol"] == "BTC"
    # SHORT direction lowered to SELL at the wire (MC schema constraint).
    assert receipt_arg["direction"] == "SELL"


@pytest.mark.asyncio
async def test_emit_swallows_bridge_exceptions(monkeypatch):
    """Fail-soft: an MC outage MUST NOT raise into the crypto bot loop."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")

    trade = {
        "symbol": "ETH", "direction": "LONG", "confidence": 0.71,
        "entry_price": 2026.0, "size": 0.05, "trade_id": "trade-2",
    }

    async def _boom(*a, **kw):
        raise RuntimeError("simulated DNS failure")

    with patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=_boom,
    ):
        # MUST NOT raise.
        result = await emit_crypto_intent(trade, {"confidence": 0.71})

    assert result is None


@pytest.mark.asyncio
async def test_emit_uses_qty_from_trade_size(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")

    trade = {
        "symbol": "SOL", "direction": "SHORT", "confidence": 0.71,
        "entry_price": 82.83, "size": 1.25184112, "trade_id": "trade-3",
    }

    captured_qty: list[float] = []

    async def _capture(client, receipt, *, qty: float = 1.0, notes: str = ""):
        captured_qty.append(qty)
        return {"ok": True}

    with patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=_capture,
    ):
        await emit_crypto_intent(trade, {})

    assert captured_qty == [pytest.approx(1.25184112)]
