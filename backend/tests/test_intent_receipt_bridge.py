"""Pytest coverage for the honesty-receipt-to-intent bridge.

Locks in the 2026-05-15 doctrine wiring:

  * Brains POST intents to MC's `/api/intents` with the full receipt
    attached. A brain that omits the receipt forfeits its audit trail
    (the exact failure mode: `total_intents=1552, blocked_directional=0`).
  * The doctrine receipt's percent-scale confidences map to MC's
    unit-scale schema.
  * The 4-brain council weights map to MC's 5-slot role schema
    positionally (alpha→strategist, camaro→auditor, chevelle→commander,
    redeye→regime, memory holds at 1.0 until local-memory layer wires in).
"""
from __future__ import annotations

import pytest

from sovereign.intent_receipt import consensus_receipt_to_intent_fields
from sovereign.mc_client import (
    MCContractError,
    build_intent_body,
    intents_url,
)


# ── URL helper ─────────────────────────────────────────────────────────


def test_intents_url_appends_correct_path():
    assert intents_url("https://mission.risedual.ai") == (
        "https://mission.risedual.ai/api/intents"
    )
    # trailing slash tolerated
    assert intents_url("https://mission.risedual.ai/") == (
        "https://mission.risedual.ai/api/intents"
    )


# ── core intent body validation ────────────────────────────────────────


def test_build_intent_body_core_minimum():
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=10.0, confidence=0.68,
    )
    assert body == {
        "symbol": "NVDA", "side": "BUY", "qty": 10.0,
        "confidence": 0.68, "notes": "",
    }


def test_build_intent_body_rejects_missing_symbol():
    with pytest.raises(MCContractError):
        build_intent_body(symbol="", side="BUY", qty=10.0, confidence=0.7)


def test_build_intent_body_rejects_bad_side():
    with pytest.raises(MCContractError):
        build_intent_body(symbol="NVDA", side="BOO", qty=10.0, confidence=0.7)


def test_build_intent_body_rejects_nonpositive_qty():
    with pytest.raises(MCContractError):
        build_intent_body(symbol="NVDA", side="BUY", qty=0, confidence=0.7)


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf")])
def test_build_intent_body_rejects_out_of_range_confidence(bad):
    with pytest.raises(MCContractError):
        build_intent_body(symbol="NVDA", side="BUY", qty=1, confidence=bad)


# ── honesty receipt fields land in the body ────────────────────────────


def test_build_intent_body_with_full_receipt():
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=10, confidence=0.68,
        raw_action="BUY", raw_confidence=0.73,
        market_decision="BUY", execution_decision="BLOCK",
        display_action="HOLD",
        hold_reason="MIN_CONFIDENCE_TO_TRADE",
        blocked_by=["MIN_CONFIDENCE_TO_TRADE", "FEATURE_HEALTH_CLAMP"],
        would_have_traded_without_gates=True,
        pre_weight_confidence=0.73, post_weight_confidence=0.68,
        council_penalty=-0.08,
        strategist_weight=1.12, auditor_weight=0.94,
        commander_weight=1.05, regime_weight=0.88, memory_weight=1.02,
    )
    # Every honesty key must be present.
    for k in (
        "raw_action", "raw_confidence", "market_decision",
        "execution_decision", "display_action",
        "hold_reason", "blocked_by", "would_have_traded_without_gates",
        "pre_weight_confidence", "post_weight_confidence",
        "council_penalty",
        "strategist_weight", "auditor_weight", "commander_weight",
        "regime_weight", "memory_weight",
    ):
        assert k in body, f"honesty field missing: {k}"


def test_build_intent_body_rejects_bad_execution_decision():
    with pytest.raises(MCContractError):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1, confidence=0.7,
            execution_decision="MAYBE",
        )


def test_build_intent_body_rejects_bad_raw_action():
    with pytest.raises(MCContractError):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1, confidence=0.7,
            raw_action="MAYBE",
        )


def test_build_intent_body_rejects_council_penalty_out_of_range():
    with pytest.raises(MCContractError):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1, confidence=0.7,
            council_penalty=-2.0,
        )


@pytest.mark.parametrize("field", [
    "strategist_weight", "auditor_weight", "commander_weight",
    "regime_weight", "memory_weight",
])
def test_build_intent_body_rejects_weight_out_of_range(field):
    with pytest.raises(MCContractError):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1, confidence=0.7,
            **{field: 5.0},
        )


def test_build_intent_body_partial_receipt_during_rollout():
    """During the rollout window a brain may ship only some receipt
    fields. The validator must accept partial sets — what we don't
    want is the LEGACY shape (zero honesty fields), but ANY honesty
    field is better than none."""
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1, confidence=0.7,
        raw_action="BUY", raw_confidence=0.7,  # only 2 fields
    )
    assert body["raw_action"] == "BUY"
    assert body["raw_confidence"] == 0.7
    # No other honesty fields present, no crash.
    assert "strategist_weight" not in body


# ── receipt → intent fields bridge ─────────────────────────────────────


def _full_receipt() -> dict:
    return {
        "raw_action": "BUY",
        "raw_confidence": 73,            # percent-scale
        "final_action": "HOLD",
        "display_action": "HOLD",
        "market_decision": "BUY",
        "execution_decision": "OBSERVE_ONLY",
        "hold_reason": "DIRECTIONAL_FLOOR_NOT_CLEARED",
        "blocked_by": ["DIRECTIONAL_FLOOR"],
        "would_have_traded_without_gates": True,
        "pre_weight_confidence": 73,    # percent
        "post_weight_confidence": 68,   # percent
        "council_penalty": -8.0,         # percentage-point delta
        "disagreement_kind": "HOLD_DISSENT",
        "individual_weights": {
            "alpha":    1.12,
            "camaro":   0.94,
            "chevelle": 1.05,
            "redeye":   0.88,
        },
    }


def test_bridge_converts_percent_confidence_to_unit():
    out = consensus_receipt_to_intent_fields(_full_receipt())
    assert out["raw_confidence"] == pytest.approx(0.73)
    assert out["pre_weight_confidence"] == pytest.approx(0.73)
    assert out["post_weight_confidence"] == pytest.approx(0.68)


def test_bridge_converts_council_penalty_pp_to_unit():
    out = consensus_receipt_to_intent_fields(_full_receipt())
    # -8.0 pp → -0.08 unit
    assert out["council_penalty"] == pytest.approx(-0.08)


def test_bridge_maps_brain_weights_to_role_slots():
    out = consensus_receipt_to_intent_fields(_full_receipt())
    assert out["strategist_weight"] == pytest.approx(1.12)  # alpha
    assert out["auditor_weight"] == pytest.approx(0.94)     # camaro
    assert out["commander_weight"] == pytest.approx(1.05)   # chevelle
    assert out["regime_weight"] == pytest.approx(0.88)      # redeye
    assert out["memory_weight"] == pytest.approx(1.0)       # default


def test_bridge_no_memory_weight_default_without_any_brain_weights():
    """If individual_weights is missing entirely, we MUST NOT inject a
    fake memory_weight=1.0 — that would be telling MC the council
    is balanced when in fact we have no weight signal at all."""
    receipt = _full_receipt()
    receipt.pop("individual_weights")
    out = consensus_receipt_to_intent_fields(receipt)
    assert "memory_weight" not in out
    assert "strategist_weight" not in out


def test_bridge_passes_through_action_and_blocked_fields():
    out = consensus_receipt_to_intent_fields(_full_receipt())
    assert out["raw_action"] == "BUY"
    assert out["market_decision"] == "BUY"
    assert out["display_action"] == "HOLD"
    assert out["execution_decision"] == "OBSERVE_ONLY"
    assert out["hold_reason"] == "DIRECTIONAL_FLOOR_NOT_CLEARED"
    assert out["blocked_by"] == ["DIRECTIONAL_FLOOR"]
    assert out["would_have_traded_without_gates"] is True


def test_bridge_handles_empty_receipt():
    assert consensus_receipt_to_intent_fields({}) == {}
    assert consensus_receipt_to_intent_fields(None) == {}  # type: ignore[arg-type]


def test_bridge_drops_noisy_inputs_silently():
    """A receipt with garbage in numeric fields must not blow up the
    intent POST — bad fields are dropped, good ones kept."""
    receipt = {
        "raw_action": "BUY",
        "raw_confidence": "not-a-number",
        "council_penalty": float("nan"),
        "individual_weights": {"alpha": "garbage", "camaro": 1.1},
    }
    out = consensus_receipt_to_intent_fields(receipt)
    assert out["raw_action"] == "BUY"
    assert "raw_confidence" not in out
    assert "council_penalty" not in out
    # alpha→strategist dropped (bad), camaro→auditor kept
    assert "strategist_weight" not in out
    assert out["auditor_weight"] == pytest.approx(1.1)


def test_bridge_output_round_trips_through_build_intent_body():
    """The whole reason this bridge exists: receipt → fields →
    build_intent_body must validate clean. No MCContractError."""
    fields = consensus_receipt_to_intent_fields(_full_receipt())
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=10, confidence=0.68,
        **fields,
    )
    # Spot-check that the receipt-derived fields actually landed.
    assert body["raw_action"] == "BUY"
    assert body["display_action"] == "HOLD"
    assert body["council_penalty"] == pytest.approx(-0.08)
    assert body["strategist_weight"] == pytest.approx(1.12)
    assert body["would_have_traded_without_gates"] is True


def test_bridge_falls_back_to_final_action_when_display_action_absent():
    """Some older receipts only have ``final_action``. Make sure the
    bridge still produces a ``display_action`` field for MC."""
    receipt = {"final_action": "HOLD", "raw_action": "BUY"}
    out = consensus_receipt_to_intent_fields(receipt)
    assert out["display_action"] == "HOLD"
    assert out["raw_action"] == "BUY"
