"""Survival-layer ↔ live-flow wiring tests.

Verifies the two end-to-end paths:

  1. Brain side: ``intent_bridge._build_emission_kwargs`` runs the
     survival pre-flight and either attaches a receipt (success) or
     short-circuits when enforce mode is on (failure).

  2. Broker side: lane executors verify ``signal["mc_receipt"]`` via
     the survival layer and skip when require-receipt mode is on
     and the receipt is missing/tampered.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, patch

import pytest

from sovereign.intent_bridge import _build_emission_kwargs


def _receipt(symbol="NVDA", action="BUY", conf=80):
    return {
        "symbol": symbol,
        "raw_action": action,
        "market_decision": action,
        "display_action": action,
        "final_confidence": conf,
        "execution_decision": "ALLOW",
    }


# ── Brain side: survival pre-flight is wired into the bridge ────────


def test_bridge_attaches_mc_receipt_on_success():
    """Happy path: directional receipt → survival kernel approves →
    bridge stamps the signed receipt onto the outgoing kwargs."""
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="")
    assert "mc_receipt" in out
    assert out["mc_receipt"]["accepted"] is True
    assert out["mc_receipt"]["final_verdict"] == "APPROVED"
    assert out["mc_receipt"]["lane"] == "equity"
    assert out["mc_receipt"]["symbol"] == "NVDA"


def test_bridge_attaches_mc_receipt_for_crypto():
    out = _build_emission_kwargs(_receipt(symbol="BTC/USD"), qty=1.0, notes="")
    assert out["mc_receipt"]["lane"] == "crypto"
    # Symbol came through normalized (we don't munge it for crypto).
    assert out["mc_receipt"]["symbol"] == "BTC/USD"


def test_bridge_logs_soft_deny_below_floor(monkeypatch, caplog):
    """Confidence below the lane floor produces a SOFT_DENY warning
    but the emission still proceeds (default soft mode)."""
    import logging
    caplog.set_level(logging.WARNING, logger="sovereign.intent_bridge")
    monkeypatch.setenv("RISEDUAL_CRYPTO_CONFIDENCE_FLOOR", "0.90")
    monkeypatch.delenv("RISEDUAL_SURVIVAL_ENFORCE", raising=False)

    out = _build_emission_kwargs(
        _receipt(symbol="BTC/USD", conf=50), qty=1.0, notes="",
    )
    # Emission still fires in soft mode.
    assert out is not None
    assert out["mc_receipt"]["accepted"] is False
    msgs = [r.getMessage() for r in caplog.records]
    assert any("SURVIVAL_PREFLIGHT_SOFT_DENY" in m for m in msgs)
    assert any("CONFIDENCE_BELOW_FLOOR" in m for m in msgs)


def test_bridge_hard_blocks_when_enforce_on(monkeypatch, caplog):
    """``RISEDUAL_SURVIVAL_ENFORCE=1`` flips soft-warn to hard-block.
    Below-floor → return None → no MC POST happens."""
    import logging
    caplog.set_level(logging.WARNING, logger="sovereign.intent_bridge")
    monkeypatch.setenv("RISEDUAL_CRYPTO_CONFIDENCE_FLOOR", "0.90")
    monkeypatch.setenv("RISEDUAL_SURVIVAL_ENFORCE", "1")

    # Need to reload the bridge module so it picks up the flag.
    import sovereign.intent_bridge as ib
    importlib.reload(ib)

    out = ib._build_emission_kwargs(
        _receipt(symbol="BTC/USD", conf=50), qty=1.0, notes="",
    )
    assert out is None
    msgs = [r.getMessage() for r in caplog.records]
    assert any("SURVIVAL_PREFLIGHT_BLOCK" in m for m in msgs)

    # Restore default so other tests are unaffected.
    monkeypatch.delenv("RISEDUAL_SURVIVAL_ENFORCE")
    importlib.reload(ib)


# ── Broker side: lane executors verify the receipt ──────────────────


@pytest.mark.asyncio
async def test_crypto_executor_accepts_valid_receipt(monkeypatch):
    """Receipt present + secret set + signature valid → proceeds."""
    monkeypatch.setenv("RISEDUAL_MC_RECEIPT_SECRET", "wiring-secret")
    monkeypatch.setenv("RISEDUAL_CRYPTO_CONFIDENCE_FLOOR", "0.20")

    # Reload modules so they pick up the env.
    import shared.runtime.platform_survival as ps
    import services.executors.crypto_executor as cr
    importlib.reload(ps)
    importlib.reload(cr)

    # Build a real signed receipt via the survival kernel.
    envelope = ps.sidecar_build_intent(
        brain_id="alpha", lane="crypto", symbol="BTC/USD",
        direction="BUY", confidence=0.80, room_id="alpha",
    )
    verdict = ps.mc_canonical_gate(envelope)
    assert verdict["accepted"]

    async def _fake_core(**kw):
        return {"order": "ok", "lane": kw["lane"]}

    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _fake_core,
    )
    result = await cr.execute_crypto_signal(
        signal={
            "symbol": "BTC/USD", "direction": "BUY", "confidence": 0.80,
            "mc_receipt": verdict["receipt"],
        },
        market_data={}, tier3_readiness={}, config={"trade_size": 100},
    )
    assert result["order"] == "ok"
    assert "skipped" not in result or result.get("skipped") is None


@pytest.mark.asyncio
async def test_crypto_executor_skips_on_tampered_receipt_when_required(monkeypatch):
    """ENFORCE on + signature broken → broker refuses submit."""
    monkeypatch.setenv("RISEDUAL_MC_RECEIPT_SECRET", "wiring-secret")
    monkeypatch.setenv("RISEDUAL_REQUIRE_MC_RECEIPT", "1")
    monkeypatch.setenv("RISEDUAL_CRYPTO_CONFIDENCE_FLOOR", "0.20")

    import shared.runtime.platform_survival as ps
    import services.executors.crypto_executor as cr
    importlib.reload(ps)
    importlib.reload(cr)

    envelope = ps.sidecar_build_intent(
        brain_id="alpha", lane="crypto", symbol="BTC/USD",
        direction="BUY", confidence=0.80, room_id="alpha",
    )
    receipt = ps.mc_canonical_gate(envelope)["receipt"]
    # Tamper.
    receipt["symbol"] = "ETH/USD"

    # If the executor reaches the core, this fails the test.
    async def _must_not_run(**kw):
        raise AssertionError("core executor must not run on tampered receipt")
    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _must_not_run,
    )

    result = await cr.execute_crypto_signal(
        signal={
            "symbol": "BTC/USD", "direction": "BUY", "confidence": 0.80,
            "mc_receipt": receipt,
        },
        market_data={}, tier3_readiness={}, config={"trade_size": 100},
    )
    assert result["skipped"] is True
    assert result["reason"].startswith("RECEIPT_")
    assert "BAD_MC_RECEIPT_SIGNATURE" in result["reason"]

    monkeypatch.delenv("RISEDUAL_REQUIRE_MC_RECEIPT")
    importlib.reload(cr)


@pytest.mark.asyncio
async def test_crypto_executor_skips_missing_receipt_when_required(monkeypatch):
    """ENFORCE on + receipt absent → broker refuses submit."""
    monkeypatch.setenv("RISEDUAL_REQUIRE_MC_RECEIPT", "1")

    import services.executors.crypto_executor as cr
    importlib.reload(cr)

    async def _must_not_run(**kw):
        raise AssertionError("core executor must not run without receipt")
    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _must_not_run,
    )

    result = await cr.execute_crypto_signal(
        signal={
            "symbol": "BTC/USD", "direction": "BUY", "confidence": 0.80,
            # NO mc_receipt
        },
        market_data={}, tier3_readiness={}, config={"trade_size": 100},
    )
    assert result["skipped"] is True
    assert result["reason"] == "RECEIPT_MISSING"

    monkeypatch.delenv("RISEDUAL_REQUIRE_MC_RECEIPT")
    importlib.reload(cr)


@pytest.mark.asyncio
async def test_crypto_executor_graceful_degrade_when_not_required(monkeypatch):
    """Default (REQUIRE off): missing receipt logs but proceeds.
    Lets the wiring roll out without breaking flow."""
    monkeypatch.delenv("RISEDUAL_REQUIRE_MC_RECEIPT", raising=False)

    import services.executors.crypto_executor as cr
    importlib.reload(cr)

    async def _fake_core(**kw):
        return {"order": "ok", "lane": kw["lane"]}
    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _fake_core,
    )

    result = await cr.execute_crypto_signal(
        signal={
            "symbol": "BTC/USD", "direction": "BUY", "confidence": 0.80,
            # NO mc_receipt — but require flag is off, so we proceed
        },
        market_data={}, tier3_readiness={}, config={"trade_size": 100},
    )
    assert result["order"] == "ok"


# ── End-to-end: bridge → wire → broker ─────────────────────────────


@pytest.mark.asyncio
async def test_e2e_bridge_emits_receipt_broker_verifies(monkeypatch):
    """The full chain: bridge produces a receipt, wire body carries
    it, broker re-verifies and submits."""
    monkeypatch.setenv("RISEDUAL_MC_RECEIPT_SECRET", "e2e-secret")
    monkeypatch.setenv("RISEDUAL_CRYPTO_CONFIDENCE_FLOOR", "0.20")

    import shared.runtime.platform_survival as ps
    import sovereign.intent_bridge as ib
    import services.executors.crypto_executor as cr
    importlib.reload(ps)
    importlib.reload(ib)
    importlib.reload(cr)

    # Step 1: bridge builds emission kwargs (attaches signed receipt).
    kwargs = ib._build_emission_kwargs(
        _receipt(symbol="BTC/USD", conf=80), qty=1.0, notes="",
    )
    assert kwargs["mc_receipt"]["accepted"] is True

    # Step 2: simulate the wire — the receipt rides on the signal that
    # eventually reaches the broker. (In live flow this happens via
    # MC; here we just pass it through.)
    signal = {
        "symbol": "BTC/USD",
        "direction": "BUY",
        "confidence": 0.80,
        "trace_id": kwargs["trace_id"],
        "mc_receipt": kwargs["mc_receipt"],
    }

    # Step 3: broker accepts.
    async def _fake_core(**kw):
        return {"order": "submitted", "lane": kw["lane"], "qty": 1.0}
    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _fake_core,
    )
    result = await cr.execute_crypto_signal(
        signal=signal, market_data={}, tier3_readiness={},
        config={"trade_size": 100},
    )
    assert result["order"] == "submitted"
    assert result["trace_id"] == kwargs["trace_id"]
