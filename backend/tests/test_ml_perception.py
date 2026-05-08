"""Perception engine tests — Phase 1.

Validates:
  * 6 sub-models can be instantiated and produce ModelOutput
  * extract_*_features produce normalised [0, 1] vectors
  * AutonomousDecisionEngine.evaluate_all returns all 6 keys
  * 5 symbolic rules fire on representative inputs
  * PerceptionML boot receipt is registered
  * PerceptionML.decide produces a layer="perception" MLVerdict
"""
from __future__ import annotations

import numpy as np

from services.ml import boot_receipts
from services.ml.contracts import FeatureFrame, Verdict
from services.ml.perception import (
    AutonomousDecisionEngine,
    DrawdownDistanceModel,
    EventShockModel,
    LiquidityModel,
    PacingModel,
    PerceptionML,
    RegimeStateModel,
    SystemHealthModel,
)
from services.ml.perception.models import (
    extract_drawdown_features,
    extract_event_shock_features,
    extract_liquidity_features,
    extract_pacing_features,
    extract_regime_features,
    extract_system_health_features,
)


# ── Sub-models ───────────────────────────────────────────────────


def test_event_shock_model_returns_score_in_range():
    out = EventShockModel().predict(np.array([0.5] * 4))
    assert 0.0 <= out.score <= 1.0
    assert 0.0 <= out.confidence <= 1.0
    assert out.label in ("EVENT_LOW", "EVENT_HIGH")


def test_regime_state_model_three_classes():
    out = RegimeStateModel().predict(np.array([0.5] * 5))
    assert out.label in ("RISK_OFF", "CHOP", "RISK_ON", "UNKNOWN")


def test_drawdown_distance_model():
    out = DrawdownDistanceModel().predict(np.array([0.5] * 3))
    assert out.label in ("DD_DEEP", "DD_NEAR_PEAK", "DD_NORMAL")


def test_liquidity_model():
    out = LiquidityModel().predict(np.array([0.5] * 4))
    assert out.label in ("LIQ_OK", "LIQ_THIN")


def test_system_health_model():
    out = SystemHealthModel().predict(np.array([0.5] * 4))
    assert out.label in ("HEALTHY", "DEGRADED")


def test_pacing_model():
    out = PacingModel().predict(np.array([0.5] * 4))
    assert out.label in ("ON_PACE", "BEHIND_PACE")


# ── Feature extractors ───────────────────────────────────────────


def test_extractors_emit_unit_range_vectors():
    big = {
        "news_count": 100,                   # capped to 1
        "news_sentiment": 5,                 # capped to 1
        "vix": 100,                          # capped to 1
        "spread_bps": 5000,                  # → invert → 0
        "data_lag_ms": 100000,
        "pipeline_latency_ms": 100000,
        "intraday_progress": 100,
    }
    for fn in (extract_event_shock_features, extract_regime_features,
               extract_drawdown_features, extract_liquidity_features,
               extract_system_health_features, extract_pacing_features):
        v = fn(big)
        assert (v >= 0).all() and (v <= 1).all()


# ── Engine ──────────────────────────────────────────────────────


def test_engine_evaluate_all_returns_six_keys():
    e = AutonomousDecisionEngine()
    s = e.evaluate_all({})  # all defaults
    assert set(s.keys()) == {
        "event_shock", "regime", "drawdown", "liquidity",
        "system_health", "pacing",
    }


def test_engine_symbolic_r1_system_degraded():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    scores["system_health"] = {"score": 0.1, "label": "DEGRADED", "confidence": 0.9}
    out = e.apply_symbolic_rules(scores, intent_hint="BUY")
    assert out["decision"] == Verdict.NO_TRADE.value
    assert out["reason"] == "SYSTEM_DEGRADED"
    assert out["rule"] == "R1"


def test_engine_symbolic_r2_event_shock():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    scores["system_health"] = {"score": 1.0, "label": "HEALTHY", "confidence": 1.0}
    scores["event_shock"] = {"score": 0.9, "label": "EVENT_HIGH", "confidence": 0.8}
    out = e.apply_symbolic_rules(scores)
    assert out["reason"] == "EVENT_SHOCK_ACTIVE"
    assert out["rule"] == "R2"


def test_engine_symbolic_r3_liquidity_thin():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    scores["system_health"] = {"score": 1.0, "label": "HEALTHY", "confidence": 1.0}
    scores["event_shock"] = {"score": 0.0, "label": "EVENT_LOW", "confidence": 1.0}
    scores["liquidity"] = {"score": 0.0, "label": "LIQ_THIN", "confidence": 1.0}
    out = e.apply_symbolic_rules(scores)
    assert out["reason"] == "LIQUIDITY_THIN"


def test_engine_symbolic_r4_drawdown_deep():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    scores["system_health"] = {"score": 1.0, "label": "HEALTHY", "confidence": 1.0}
    scores["event_shock"] = {"score": 0.0, "label": "EVENT_LOW", "confidence": 1.0}
    scores["liquidity"] = {"score": 1.0, "label": "LIQ_OK", "confidence": 1.0}
    scores["drawdown"] = {"score": 0.95, "label": "DD_DEEP", "confidence": 0.9}
    out = e.apply_symbolic_rules(scores)
    assert out["reason"] == "DRAWDOWN_DEEP"


def test_engine_symbolic_r5_regime_risk_off_blocks_buy():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    scores["system_health"] = {"score": 1.0, "label": "HEALTHY", "confidence": 1.0}
    scores["event_shock"] = {"score": 0.0, "label": "EVENT_LOW", "confidence": 1.0}
    scores["liquidity"] = {"score": 1.0, "label": "LIQ_OK", "confidence": 1.0}
    scores["drawdown"] = {"score": 0.5, "label": "DD_NORMAL", "confidence": 0.5}
    scores["regime"] = {"score": 0.9, "label": "RISK_OFF", "confidence": 0.9}
    out = e.apply_symbolic_rules(scores, intent_hint="BUY")
    assert out["reason"] == "REGIME_RISK_OFF"


def test_engine_symbolic_pass_forwards_intent():
    e = AutonomousDecisionEngine()
    scores = e.evaluate_all({})
    # Force healthy scores
    scores["system_health"] = {"score": 1.0, "label": "HEALTHY", "confidence": 1.0}
    scores["event_shock"] = {"score": 0.0, "label": "EVENT_LOW", "confidence": 1.0}
    scores["liquidity"] = {"score": 1.0, "label": "LIQ_OK", "confidence": 1.0}
    scores["drawdown"] = {"score": 0.5, "label": "DD_NORMAL", "confidence": 0.5}
    scores["regime"] = {"score": 0.9, "label": "RISK_ON", "confidence": 0.9}
    out = e.apply_symbolic_rules(scores, intent_hint="BUY")
    assert out["decision"] == Verdict.BUY.value
    assert out["reason"] == "PERCEPTION_PASS"


# ── PerceptionML layer ───────────────────────────────────────────


def test_perception_layer_boot_registers_receipt():
    boot_receipts.clear()
    PerceptionML().boot()
    r = boot_receipts.get("perception")
    assert r is not None
    assert r.ready is True


def test_perception_decide_returns_layer_perception():
    layer = PerceptionML()
    frame = FeatureFrame(symbol="AAPL", lane="equity", timestamp="2026-02-01T00:00:00Z")
    v = layer.decide(frame)
    assert v.layer == "perception"
    assert v.decision in (Verdict.BUY.value, Verdict.SELL.value, Verdict.NO_TRADE.value)
    assert "scores" in (frame.perception or {})
