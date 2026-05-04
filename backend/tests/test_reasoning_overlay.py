"""
Tests for ``services.decision_reasoning_overlay`` — pin the
read-only contract so accidental mutation of action / confidence /
sizing fields is caught at CI time.

The overlay's invariant: it READS a decision dict and RETURNS a
reasoning dict. It must never mutate the input. Every assertion below
either pins the expected reasoning shape or pins that "input is
unchanged after overlay computes".
"""
from __future__ import annotations

import copy

from services.decision_reasoning_overlay import build_reasoning_overlay


# ── User's smoke test (verbatim from spec) ────────────────────────


def test_reasoning_overlay_basic():
    decision = {
        "symbol": "NVDA",
        "action": "STRONG_BUY",
        "confidence": 0.56,
        "calibrated_confidence": 0.91,
        "regime": "TRENDING_BULL",
        "passed_gates": ["rr_check"],
        "failed_gates": [],
        "risk_adjustments": ["small_sample_discount_0.75"],
        "commander_shadow": {
            "action": "HOLD",
            "confidence": 0.72,
            "authority": "SHADOW_ONLY",
        },
    }

    reasoning = build_reasoning_overlay(decision)

    assert "summary" in reasoning
    assert "reason_codes" in reasoning
    assert "COMMANDER_DISAGREES" in reasoning["reason_codes"]


# ── Read-only contract: input must not mutate ─────────────────────


def test_overlay_does_not_mutate_input():
    decision = {
        "symbol": "NVDA",
        "action": "STRONG_BUY",
        "confidence": 0.56,
        "calibrated_confidence": 0.91,
        "regime": "TRENDING_BULL",
        "passed_gates": ["rr_check"],
        "failed_gates": [],
        "risk_adjustments": ["small_sample_discount_0.75"],
        "commander_shadow": {
            "action": "HOLD",
            "confidence": 0.72,
            "authority": "SHADOW_ONLY",
        },
    }
    snapshot = copy.deepcopy(decision)
    _ = build_reasoning_overlay(decision)
    assert decision == snapshot, (
        "build_reasoning_overlay must not mutate its input — it is a "
        "READ-ONLY overlay and consumers depend on that invariant."
    )


# ── reason_code dispatch ──────────────────────────────────────────


def test_long_action_sets_long_regime_code():
    out = build_reasoning_overlay({"action": "STRONG_BUY", "confidence": 0.6})
    assert "REGIME_SUPPORTS_LONG" in out["reason_codes"]
    assert "REGIME_SUPPORTS_SHORT" not in out["reason_codes"]


def test_short_action_sets_short_regime_code():
    out = build_reasoning_overlay({"action": "STRONG_SELL", "confidence": 0.6})
    assert "REGIME_SUPPORTS_SHORT" in out["reason_codes"]
    assert "REGIME_SUPPORTS_LONG" not in out["reason_codes"]


def test_hold_action_sets_neutral_code():
    out = build_reasoning_overlay({"action": "HOLD", "confidence": 0.5})
    assert "NEUTRAL_ACTION" in out["reason_codes"]


def test_underconfident_when_calibrated_higher_than_raw():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.55, "calibrated_confidence": 0.91,
    })
    assert "UNDERCONFIDENT_MODEL" in out["reason_codes"]
    assert "OVERCONFIDENT_MODEL" not in out["reason_codes"]


def test_overconfident_when_calibrated_lower_than_raw():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.95, "calibrated_confidence": 0.62,
    })
    assert "OVERCONFIDENT_MODEL" in out["reason_codes"]
    assert "UNDERCONFIDENT_MODEL" not in out["reason_codes"]


def test_no_calibration_field_means_no_calibration_code():
    out = build_reasoning_overlay({"action": "BUY", "confidence": 0.6})
    assert "UNDERCONFIDENT_MODEL" not in out["reason_codes"]
    assert "OVERCONFIDENT_MODEL" not in out["reason_codes"]


def test_failed_gates_flagged():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "failed_gates": ["regime_check"],
    })
    assert "FAILED_ONE_OR_MORE_GATES" in out["reason_codes"]
    assert "PASSED_ALL_GATES" not in out["reason_codes"]


def test_no_failed_gates_means_passed_all():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6, "failed_gates": [],
    })
    assert "PASSED_ALL_GATES" in out["reason_codes"]


def test_small_sample_adjustment_flagged():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "risk_adjustments": ["small_sample_discount_0.75"],
    })
    assert "SMALL_SAMPLE_DISCOUNT" in out["reason_codes"]


def test_integrity_adjustment_flagged():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "risk_adjustments": ["integrity_mitigation_clamp"],
    })
    assert "INTEGRITY_MITIGATION_ACTIVE" in out["reason_codes"]


def test_commander_agrees_when_actions_match():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "commander_shadow": {
            "action": "BUY", "confidence": 0.7, "authority": "ACTIVE",
        },
    })
    assert "COMMANDER_AGREES" in out["reason_codes"]
    assert "COMMANDER_DISAGREES" not in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" not in out["reason_codes"]


def test_commander_no_authority_flagged_when_shadow_only():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "commander_shadow": {
            "action": "BUY", "confidence": 0.7, "authority": "SHADOW_ONLY",
        },
    })
    assert "COMMANDER_AGREES" in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" in out["reason_codes"]


def test_no_commander_means_no_commander_codes():
    out = build_reasoning_overlay({"action": "BUY", "confidence": 0.6})
    cc = [c for c in out["reason_codes"] if "COMMANDER" in c]
    assert cc == []


# ── Output envelope shape ─────────────────────────────────────────


def test_envelope_has_required_top_level_keys():
    out = build_reasoning_overlay({"action": "BUY", "confidence": 0.6})
    for key in (
        "summary", "bull_case", "bear_case",
        "what_would_change_decision", "reason_codes", "meta",
    ):
        assert key in out, f"missing key: {key}"


def test_meta_carries_input_fields():
    out = build_reasoning_overlay({
        "symbol": "NVDA", "action": "BUY", "confidence": 0.56,
        "calibrated_confidence": 0.91,
    })
    assert out["meta"]["symbol"] == "NVDA"
    assert out["meta"]["action"] == "BUY"
    assert out["meta"]["confidence"] == 0.56
    assert out["meta"]["calibrated_confidence"] == 0.91


def test_handles_empty_decision_gracefully():
    """Defensive: empty dict must not raise."""
    out = build_reasoning_overlay({})
    assert isinstance(out, dict)
    assert "reason_codes" in out
    assert "NEUTRAL_ACTION" in out["reason_codes"]


def test_summary_mentions_commander_disagreement():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.6,
        "commander_shadow": {"action": "HOLD", "authority": "SHADOW_ONLY"},
    })
    assert "Commander" in out["summary"]
    assert "shadow only" in out["summary"]


def test_bear_case_aggregates_concerns():
    out = build_reasoning_overlay({
        "action": "BUY", "confidence": 0.55, "calibrated_confidence": 0.91,
        "risk_adjustments": ["small_sample_discount"],
        "commander_shadow": {"action": "HOLD", "authority": "SHADOW_ONLY"},
    })
    assert "Commander disagreed" in out["bear_case"]
    assert "limited high-confidence sample size" in out["bear_case"]
    assert "model confidence may be understated" in out["bear_case"]
