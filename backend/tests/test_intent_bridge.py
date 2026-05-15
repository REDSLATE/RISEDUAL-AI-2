"""Pytest coverage for the intent_bridge emission helper.

Locks in three doctrine guard rails the helper enforces:

  1. Only directional verdicts (BUY / SELL / SHORT / COVER) trigger
     a POST. HOLD / NEUTRAL / ALL_HOLD pass through silently — that
     is "no opinion", not an intent.
  2. ``execution_decision`` is stamped ``OBSERVE_ONLY`` automatically
     so MC can never confuse RISEDUAL's advisory output for an
     execution claim (Doctrine V3 invariant).
  3. MC POST failures are logged but never raised — a flaky MC must
     not block the next consensus tick.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from sovereign.intent_bridge import (
    _build_emission_kwargs,
    emit_intent_from_consensus,
    emit_intent_sync,
)
from sovereign.mc_client import MCClientError


def _receipt(**over):
    base = {
        "symbol": "NVDA",
        "raw_action": "BUY",
        "final_confidence": 68,
        "raw_confidence": 73,
        "market_decision": "BUY",
        "display_action": "BUY",
        "pre_weight_confidence": 73,
        "post_weight_confidence": 68,
        "council_penalty": -5.0,
        "individual_weights": {
            "alpha": 1.1, "camaro": 1.0, "chevelle": 0.95, "redeye": 0.9,
        },
    }
    base.update(over)
    return base


# ── doctrine guard rails ───────────────────────────────────────────────


def test_build_kwargs_returns_none_for_hold():
    """ALL_HOLD / HOLD is an opinion, not an intent. The bridge must
    skip emission silently — emitting it would pollute MC's intent
    feed with no-ops, which is the exact failure mode we're fixing."""
    assert _build_emission_kwargs(
        _receipt(raw_action="HOLD"), qty=1.0, notes="",
    ) is None


def test_build_kwargs_returns_none_for_unknown_action():
    assert _build_emission_kwargs(
        _receipt(raw_action="MAYBE"), qty=1.0, notes="",
    ) is None


def test_build_kwargs_returns_none_for_empty_symbol():
    assert _build_emission_kwargs(
        _receipt(symbol=""), qty=1.0, notes="",
    ) is None


@pytest.mark.parametrize("action", ["BUY", "SELL", "SHORT", "COVER"])
def test_build_kwargs_emits_for_all_directional_verdicts(action):
    out = _build_emission_kwargs(
        _receipt(raw_action=action, market_decision=action, display_action=action),
        qty=1.0, notes="",
    )
    assert out is not None
    assert out["side"] == action


def test_build_kwargs_stamps_observe_only_under_doctrine_v3():
    """Even if upstream forgot to set it, the bridge must stamp
    execution_decision = OBSERVE_ONLY. RISEDUAL never claims
    execution authority (Doctrine V3)."""
    receipt = _receipt()
    receipt.pop("execution_decision", None)
    out = _build_emission_kwargs(receipt, qty=1.0, notes="")
    assert out["execution_decision"] == "OBSERVE_ONLY"


def test_build_kwargs_does_not_override_explicit_exec_decision():
    """If MC's executor seat ever lives on this brain (it doesn't
    today, but the schema allows it), an upstream caller can set
    execution_decision and the bridge must honor it."""
    out = _build_emission_kwargs(
        _receipt(execution_decision="ALLOW"), qty=1.0, notes="",
    )
    assert out["execution_decision"] == "ALLOW"


def test_build_kwargs_clamps_confidence_to_unit_scale():
    """Receipt carries percent; wire wants unit. 0-100 should map to 0-1."""
    out = _build_emission_kwargs(
        _receipt(final_confidence=85), qty=1.0, notes="",
    )
    assert out["confidence"] == pytest.approx(0.85)


def test_build_kwargs_handles_missing_final_confidence_gracefully():
    receipt = _receipt()
    receipt.pop("final_confidence")
    receipt["confidence"] = 60  # fallback path
    out = _build_emission_kwargs(receipt, qty=1.0, notes="")
    assert out["confidence"] == pytest.approx(0.60)


def test_build_kwargs_round_trips_through_post_intent_validator():
    """The whole reason this exists: the kwargs the bridge produces
    must validate clean inside build_intent_body."""
    from sovereign.mc_client import build_intent_body
    out = _build_emission_kwargs(_receipt(), qty=2.5, notes="test")
    body = build_intent_body(**out)
    assert body["symbol"] == "NVDA"
    assert body["side"] == "BUY"
    assert body["qty"] == 2.5
    assert body["execution_decision"] == "OBSERVE_ONLY"
    assert "strategist_weight" in body  # bridge → alpha → strategist


# ── sync emit ──────────────────────────────────────────────────────────


def test_emit_intent_sync_skips_non_directional():
    client = MagicMock()
    out = emit_intent_sync(client, _receipt(raw_action="HOLD"))
    assert out is None
    client.post_intent.assert_not_called()


def test_emit_intent_sync_calls_post_intent_for_directional():
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-1", "status": "ok"}
    out = emit_intent_sync(client, _receipt(), qty=10, notes="alpha tick")
    assert out == {"id": "ix-1", "status": "ok"}
    client.post_intent.assert_called_once()
    kwargs = client.post_intent.call_args.kwargs
    assert kwargs["symbol"] == "NVDA"
    assert kwargs["side"] == "BUY"
    assert kwargs["qty"] == 10
    assert kwargs["notes"] == "alpha tick"


def test_emit_intent_sync_propagates_mc_errors():
    """Sync path raises on MC error so test code can assert; the
    async wrapper is the one that swallows."""
    client = MagicMock()
    client.post_intent.side_effect = MCClientError("422 bad payload")
    with pytest.raises(MCClientError):
        emit_intent_sync(client, _receipt())


# ── async fire-and-forget ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_intent_from_consensus_swallows_mc_failures():
    """A flaky MC must never block a brain from producing the next
    consensus. The async helper swallows MCClientError and returns
    None so the caller can move on."""
    client = MagicMock()
    client.post_intent.side_effect = MCClientError("upstream offline")
    out = await emit_intent_from_consensus(client, _receipt())
    assert out is None
    client.post_intent.assert_called_once()


@pytest.mark.asyncio
async def test_emit_intent_from_consensus_returns_mc_response_on_success():
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-99", "status": "queued"}
    out = await emit_intent_from_consensus(client, _receipt())
    assert out == {"id": "ix-99", "status": "queued"}


@pytest.mark.asyncio
async def test_emit_intent_from_consensus_skips_non_directional_without_mc_call():
    client = MagicMock()
    out = await emit_intent_from_consensus(client, _receipt(raw_action="HOLD"))
    assert out is None
    client.post_intent.assert_not_called()


@pytest.mark.asyncio
async def test_emit_intent_from_consensus_default_qty_is_unit():
    """Alpha is advisor/decider, not sizer. Default qty=1.0 means
    "unit signal, MC sizes it" — the doctrinally correct default
    until we wire local sizing."""
    client = MagicMock()
    client.post_intent.return_value = {"ok": True}
    await emit_intent_from_consensus(client, _receipt())
    qty = client.post_intent.call_args.kwargs["qty"]
    assert qty == 1.0
