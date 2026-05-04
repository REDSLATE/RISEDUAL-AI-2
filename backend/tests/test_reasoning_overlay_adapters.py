"""
Tests for the trade-doc adapters in
``services.decision_reasoning_overlay``.

Pin:
1. Adapters never mutate their input.
2. Equity adapter surfaces COMMANDER_DISAGREES when brake_log carries
   a different commander_action than the trade direction.
3. Equity adapter surfaces SMALL_SAMPLE_DISCOUNT when a
   ``small_sample_*`` risk_adjustment is present in the brake log.
4. Equity adapter surfaces INTEGRITY_MITIGATION_ACTIVE when the
   ``symbol_failure_penalty`` block is present.
5. Crypto adapter surfaces commander auth ACTIVE when the trade
   carries ``adversarial_action="full_trigger"`` /
   ``"full_override"``.
6. Crypto adapter surfaces COMMANDER_AGREES when the signal's
   ``commander_action`` matches the trade direction.
"""
from __future__ import annotations

import copy

from services.decision_reasoning_overlay import (
    build_crypto_paper_trade_decision_view,
    build_equity_paper_trade_decision_view,
    build_reasoning_overlay,
)


# ── Equity adapter ────────────────────────────────────────────────


def test_equity_adapter_does_not_mutate_input():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "regime": "TRENDING_BULL",
        "commander_phase2_brake": {
            "decision": "PASS",
            "commander_decision": "SHORT",
            "commander_confidence": 0.7,
            "promotion_phase": "shadow",
        },
    }
    snapshot = copy.deepcopy(trade)
    _ = build_equity_paper_trade_decision_view(
        trade, patterns=["bull_flag"], dynamic_conf_threshold=0.55,
    )
    assert trade == snapshot


def test_equity_adapter_normalises_up_down_to_long_short():
    trade_up = {"ticker": "NVDA", "direction": "up", "confidence": 0.65}
    trade_down = {"ticker": "NVDA", "direction": "down", "confidence": 0.65}
    out_up = build_reasoning_overlay(
        build_equity_paper_trade_decision_view(trade_up),
    )
    out_down = build_reasoning_overlay(
        build_equity_paper_trade_decision_view(trade_down),
    )
    assert "REGIME_SUPPORTS_LONG" in out_up["reason_codes"]
    assert "NEUTRAL_ACTION" not in out_up["reason_codes"]
    assert "REGIME_SUPPORTS_SHORT" in out_down["reason_codes"]
    assert "NEUTRAL_ACTION" not in out_down["reason_codes"]


def test_equity_adapter_surfaces_commander_disagrees():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "regime": "TRENDING_BULL",
        "commander_phase2_brake": {
            "decision": "PASS",
            "commander_decision": "SHORT",  # opposite of "up" → LONG
            "commander_confidence": 0.7,
            "promotion_phase": "shadow",
        },
    }
    view = build_equity_paper_trade_decision_view(
        trade, patterns=["bull_flag"], dynamic_conf_threshold=0.55,
    )
    out = build_reasoning_overlay(view)
    assert "COMMANDER_DISAGREES" in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" in out["reason_codes"]
    assert "PASSED_ALL_GATES" in out["reason_codes"]


def test_equity_adapter_phase2_brake_veto_is_failed_gate():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "regime": "TRENDING_BULL",
        "commander_phase2_brake": {
            "decision": "VETO",
            "commander_decision": "SHORT",
            "commander_confidence": 0.85,
            "promotion_phase": "full",
        },
    }
    view = build_equity_paper_trade_decision_view(trade)
    assert "phase2_brake_veto" in view["failed_gates"]
    out = build_reasoning_overlay(view)
    assert "FAILED_ONE_OR_MORE_GATES" in out["reason_codes"]
    assert "PASSED_ALL_GATES" not in out["reason_codes"]


def test_equity_adapter_full_phase_means_commander_active():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "commander_phase2_brake": {
            "decision": "PASS",
            "commander_decision": "LONG",
            "commander_confidence": 0.7,
            "promotion_phase": "full",
        },
    }
    view = build_equity_paper_trade_decision_view(trade)
    assert view["commander_shadow"]["authority"] == "ACTIVE"
    out = build_reasoning_overlay(view)
    assert "COMMANDER_AGREES" in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" not in out["reason_codes"]


def test_equity_adapter_failure_penalty_surfaces_integrity():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "symbol_failure_penalty": {"applied": True, "factor": 0.5},
    }
    view = build_equity_paper_trade_decision_view(trade)
    assert "integrity_mitigation_symbol_failures" in view["risk_adjustments"]
    out = build_reasoning_overlay(view)
    assert "INTEGRITY_MITIGATION_ACTIVE" in out["reason_codes"]


def test_equity_adapter_sovereign_contribution_above_threshold():
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "sovereign_contribution": {"delta_confidence": 0.05},
    }
    view = build_equity_paper_trade_decision_view(trade)
    risk_codes = " ".join(view["risk_adjustments"])
    assert "sovereign_contribution_+0.050" in risk_codes


def test_equity_adapter_sovereign_contribution_below_threshold_skipped():
    """Tiny deltas below 0.001 shouldn't emit a noise risk_adjustment."""
    trade = {
        "ticker": "NVDA",
        "direction": "up",
        "confidence": 0.65,
        "sovereign_contribution": {"delta_confidence": 0.0005},
    }
    view = build_equity_paper_trade_decision_view(trade)
    risk_codes = " ".join(view["risk_adjustments"])
    assert "sovereign_contribution" not in risk_codes


# ── Crypto adapter ────────────────────────────────────────────────


def test_crypto_adapter_does_not_mutate_input():
    trade = {
        "symbol": "BTC", "direction": "LONG", "confidence": 0.65,
        "regime": "TRENDING_BULL",
        "strategist_conf": 0.7, "auditor_conf": 0.6,
        "adversarial_action": "full_trigger",
    }
    signal = {"commander_action": "LONG", "commander_confidence": 0.8}
    snap_t = copy.deepcopy(trade)
    snap_s = copy.deepcopy(signal)
    _ = build_crypto_paper_trade_decision_view(trade, signal=signal)
    assert trade == snap_t
    assert signal == snap_s


def test_crypto_adapter_full_trigger_means_commander_active():
    trade = {
        "symbol": "BTC", "direction": "LONG", "confidence": 0.65,
        "strategist_conf": 0.7, "auditor_conf": 0.6,
        "adversarial_action": "full_trigger",
    }
    signal = {"commander_action": "LONG", "commander_confidence": 0.8}
    view = build_crypto_paper_trade_decision_view(trade, signal=signal)
    assert view["commander_shadow"]["authority"] == "ACTIVE"
    out = build_reasoning_overlay(view)
    assert "COMMANDER_AGREES" in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" not in out["reason_codes"]


def test_crypto_adapter_no_adversarial_means_shadow_only():
    trade = {
        "symbol": "BTC", "direction": "LONG", "confidence": 0.65,
        "strategist_conf": 0.7, "auditor_conf": 0.6,
    }
    signal = {"commander_action": "SHORT", "commander_confidence": 0.7}
    view = build_crypto_paper_trade_decision_view(trade, signal=signal)
    assert view["commander_shadow"]["authority"] == "SHADOW_ONLY"
    out = build_reasoning_overlay(view)
    assert "COMMANDER_DISAGREES" in out["reason_codes"]
    assert "COMMANDER_NO_AUTHORITY" in out["reason_codes"]


def test_crypto_adapter_passes_strategist_and_auditor_gates():
    trade = {
        "symbol": "BTC", "direction": "LONG", "confidence": 0.65,
        "strategist_conf": 0.7, "auditor_conf": 0.6,
    }
    view = build_crypto_paper_trade_decision_view(trade)
    assert "strategist_confidence" in view["passed_gates"]
    assert "auditor_verdict" in view["passed_gates"]


def test_crypto_adapter_full_override_carries_commander_full_override_active():
    trade = {
        "symbol": "BTC", "direction": "LONG", "confidence": 0.65,
        "strategist_conf": 0.7, "auditor_conf": 0.6,
        "adversarial_action": "full_override",
    }
    view = build_crypto_paper_trade_decision_view(trade)
    assert "commander_full_override_active" in view["risk_adjustments"]
