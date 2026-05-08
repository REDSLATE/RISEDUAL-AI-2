"""Tests for the Tier 1 Fast Veto Shadow Layer.

Covers the four contract pillars that any future operator change
must preserve:

1. **Authority is veto-only.** ``FAST_VETO_CAN_APPROVE`` is ``False``,
   and the result envelope cannot encode a BUY/SELL/HOLD instruction.
2. **Shadow mode is observation only.** ``would_veto`` may be True
   while ``enforce_veto`` stays False unless the operator flips
   ``FAST_VETO_ENFORCE_ENABLED``.
3. **Rule cascade is deterministic.** Each rule fires for its own
   feature condition with no overlap leaks.
4. **The hot path stays fast.** Single evaluation finishes well
   under the 1ms budget on a developer laptop.
"""
from __future__ import annotations

import time

import pytest

from services import fast_veto_layer
from services.fast_veto_layer import (
    FAST_VETO_CAN_APPROVE,
    FastVetoResult,
    build_fast_veto_features,
    evaluate_fast_veto,
)


# ── 1. Authority invariants ──────────────────────────────────────


def test_can_approve_flag_is_hardcoded_false():
    """The single most important contract: this layer cannot ever
    instruct the executor to take a position."""
    assert FAST_VETO_CAN_APPROVE is False


def test_result_envelope_has_no_action_field():
    """``FastVetoResult`` must not contain ``action``, ``side``,
    ``direction`` etc. — fields the executor would route into a
    trade. Veto-only by construction."""
    r = evaluate_fast_veto(signal={"confidence": 0.6}, market_state={})
    forbidden = {"action", "side", "direction", "qty", "size"}
    assert not (set(r.__dict__.keys()) & forbidden), (
        "FastVetoResult must not carry any executor-actionable fields"
    )


# ── 2. Shadow vs enforce separation ──────────────────────────────


def test_shadow_mode_never_enforces(monkeypatch):
    """Even with ``would_veto=True``, ``enforce_veto`` must stay
    False unless ``FAST_VETO_ENFORCE_ENABLED`` was True at module
    load."""
    monkeypatch.setattr(fast_veto_layer, "FAST_VETO_ENFORCE_ENABLED", False)
    r = evaluate_fast_veto(
        signal={"confidence": 0.5},
        market_state={"drawdown_pct": 25.0},  # will trip drawdown rule
    )
    assert r.would_veto is True
    assert r.enforce_veto is False


def test_enforce_mode_can_set_enforce_veto(monkeypatch):
    monkeypatch.setattr(fast_veto_layer, "FAST_VETO_ENFORCE_ENABLED", True)
    r = evaluate_fast_veto(
        signal={"confidence": 0.5},
        market_state={"drawdown_pct": 25.0},
    )
    assert r.would_veto is True
    assert r.enforce_veto is True


def test_enforce_mode_does_not_force_veto_on_clean_signal(monkeypatch):
    monkeypatch.setattr(fast_veto_layer, "FAST_VETO_ENFORCE_ENABLED", True)
    r = evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={
            "spread_bps": 10.0,
            "volatility_score": 0.3,
            "drawdown_pct": 1.0,
            "liquidity_score": 0.95,
        },
    )
    assert r.would_veto is False
    assert r.enforce_veto is False


# ── 3. Rule cascade — each rule fires for its own condition ──────


def test_rule_drawdown_breach():
    r = evaluate_fast_veto(
        signal={"confidence": 0.9},
        market_state={"drawdown_pct": 12.0},
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_DRAWDOWN_BREACH"


def test_rule_wide_spread():
    r = evaluate_fast_veto(
        signal={"confidence": 0.9},
        market_state={"spread_bps": 100.0, "drawdown_pct": 0.0},
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_WIDE_SPREAD"


def test_rule_vol_spike_low_confidence():
    r = evaluate_fast_veto(
        signal={"confidence": 0.50},
        market_state={"volatility_score": 0.95, "drawdown_pct": 0.0},
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_VOL_SPIKE_LOW_CONFIDENCE"


def test_rule_vol_spike_HIGH_confidence_does_not_veto():
    """High volatility alone must not veto — only when the model is
    also unsure (confidence < 0.70). Locks in the AND clause."""
    r = evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"volatility_score": 0.95, "drawdown_pct": 0.0},
    )
    assert r.would_veto is False
    assert r.reason == "FAST_VETO_PASS_SHADOW"


def test_rule_low_liquidity():
    r = evaluate_fast_veto(
        signal={"confidence": 0.9},
        market_state={"liquidity_score": 0.10, "drawdown_pct": 0.0},
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_LOW_LIQUIDITY"


def test_clean_signal_passes():
    r = evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={
            "spread_bps": 5.0,
            "volatility_score": 0.30,
            "drawdown_pct": 0.5,
            "liquidity_score": 0.95,
        },
    )
    assert r.would_veto is False
    assert r.reason == "FAST_VETO_PASS_SHADOW"


def test_drawdown_takes_priority_over_spread():
    """First matching rule wins — drawdown should fire even if
    spread also exceeds threshold."""
    r = evaluate_fast_veto(
        signal={"confidence": 0.5},
        market_state={"drawdown_pct": 15.0, "spread_bps": 100.0},
    )
    assert r.reason == "FAST_VETO_DRAWDOWN_BREACH"


# ── 4. Optional model layer ──────────────────────────────────────


def test_model_consensus_triggers_veto():
    """Two or more models scoring >=0.80 should produce a veto when
    the rule cascade hasn't already fired."""
    class _FakeModel:
        def __init__(self, score):
            self._score = score

        def predict_proba(self, X):
            return [[1 - self._score, self._score]]

    r = evaluate_fast_veto(
        signal={"confidence": 0.80},
        market_state={"drawdown_pct": 0.0, "spread_bps": 5.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        models={"a": _FakeModel(0.85), "b": _FakeModel(0.90), "c": _FakeModel(0.10)},
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_MODEL_CONSENSUS"
    assert r.model_scores["a"] == pytest.approx(0.85)
    assert r.model_scores["b"] == pytest.approx(0.90)


def test_single_high_score_does_not_trigger_veto():
    """Consensus requires >= 2 models. Single model agreement is
    not enough."""
    class _FakeModel:
        def predict_proba(self, X):
            return [[0.05, 0.95]]

    r = evaluate_fast_veto(
        signal={"confidence": 0.80},
        market_state={"drawdown_pct": 0.0, "spread_bps": 5.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        models={"a": _FakeModel()},
    )
    assert r.would_veto is False


def test_model_failure_does_not_propagate():
    """A misbehaving model must not crash evaluation; the layer
    falls back to rules. Hot-path safety property."""
    class _BrokenModel:
        def predict_proba(self, X):
            raise RuntimeError("model corrupted")

    r = evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"drawdown_pct": 0.0, "spread_bps": 5.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        models={"broken": _BrokenModel()},
    )
    # Result must still come back with a structured envelope
    assert isinstance(r, FastVetoResult)
    assert r.model_scores["broken"] == -1.0


# ── 5. Hot-path latency budget ────────────────────────────────────


def test_evaluation_latency_under_budget():
    """Single eval (no models) should clear well under 1ms (1000µs)
    on a dev box. Provides a regression alert if someone adds
    allocations to the hot path."""
    # Warm the buffer
    for _ in range(50):
        evaluate_fast_veto({"confidence": 0.8}, {"drawdown_pct": 0.0})

    started = time.perf_counter_ns()
    iters = 1000
    for _ in range(iters):
        evaluate_fast_veto({"confidence": 0.8}, {"drawdown_pct": 0.0})
    avg_us = ((time.perf_counter_ns() - started) / 1_000.0) / iters

    # Generous budget: even on a slow CI node we should be well
    # under 1ms. Failure here means someone added an allocation
    # or import to the hot path.
    assert avg_us < 1000.0, f"avg latency {avg_us:.1f}µs exceeds 1ms budget"


# ── 6. Feature builder ────────────────────────────────────────────


def test_features_handle_missing_keys():
    """Producer can pass partial dicts — defaults must keep the
    envelope sane."""
    f = build_fast_veto_features(signal={}, market_state={})
    assert f["confidence"] == 0.0
    assert f["spread_bps"] == 0.0
    assert f["volatility_score"] == 0.0
    assert f["drawdown_pct"] == 0.0
    assert f["liquidity_score"] == 1.0  # default to "good" so missing data doesn't false-veto


def test_features_handle_none_values():
    f = build_fast_veto_features(
        signal={"confidence": None},
        market_state={"spread_bps": None, "drawdown_pct": None},
    )
    assert f["confidence"] == 0.0
    assert f["spread_bps"] == 0.0


def test_features_handle_garbage_values():
    f = build_fast_veto_features(
        signal={"confidence": "not-a-number"},
        market_state={"spread_bps": object()},
    )
    assert f["confidence"] == 0.0
    assert f["spread_bps"] == 0.0
