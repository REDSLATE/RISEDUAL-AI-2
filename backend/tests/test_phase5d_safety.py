"""Phase 5d safety patch tests:

  a) Stale-Model Protection (joblib mtime > 72h → observe-only)
  b) Feature-Health Weighting (clamps Strategist confidence; <0.3 = HOLD)
  c) Executor Heartbeat (records every pipeline run; surfaces frozen lanes)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier

from services.ml import boot_receipts, executor_heartbeat
from services.ml.contracts import FeatureFrame, Verdict
from services.ml.feature_health import compute_feature_health, should_force_hold
from services.ml.model_age import (
    evaluate_artifact,
    get_artifact_age_hours,
    stale_reason,
)
from services.ml.pipeline import RisedualMLPipeline
from services.ml.strategist import StrategistML


# ── Helpers ──────────────────────────────────────────────────────


def _frame(lane: str = "equity", **market) -> FeatureFrame:
    return FeatureFrame(
        symbol="AAPL",
        lane=lane,
        timestamp=datetime.now(timezone.utc).isoformat(),
        market=market or {},
    )


def _write_dummy_joblib(tmp_path: Path, *, age_hours: float = 0.0) -> Path:
    """Persist a tiny RandomForest so joblib.load works, then back-date
    the mtime to force a known artifact age."""
    clf = RandomForestClassifier(n_estimators=2, random_state=0)
    X = np.random.RandomState(0).rand(20, 10)
    y = np.array([0, 1, 2] * 6 + [0, 1])
    clf.fit(X, y)
    p = tmp_path / "model.joblib"
    joblib.dump(clf, p)
    if age_hours > 0:
        new_mtime = time.time() - age_hours * 3600.0
        os.utime(p, (new_mtime, new_mtime))
    return p


# ── (a) Stale-Model Protection ───────────────────────────────────


def test_model_age_returns_none_for_missing_path(tmp_path):
    assert get_artifact_age_hours(None) is None
    assert get_artifact_age_hours(str(tmp_path / "nope.joblib")) is None


def test_model_age_evaluate_fresh(tmp_path):
    p = _write_dummy_joblib(tmp_path, age_hours=1.0)
    stale, age, max_age = evaluate_artifact(str(p))
    assert stale is False
    assert age is not None and 0.5 < age < 2.0
    assert max_age == 72.0


def test_model_age_evaluate_stale(tmp_path):
    p = _write_dummy_joblib(tmp_path, age_hours=80.0)
    stale, age, max_age = evaluate_artifact(str(p))
    assert stale is True
    assert age is not None and age > 72.0


def test_stale_reason_format():
    msg = stale_reason(85.0, 72.0)
    assert msg.startswith("MODEL_STALE_OBSERVE_ONLY")
    assert "85.00h" in msg
    assert "72h" in msg


def test_strategist_boot_flips_to_observe_only_when_artifact_stale(
    tmp_path, monkeypatch,
):
    boot_receipts.clear()
    p = _write_dummy_joblib(tmp_path, age_hours=80.0)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(p))
    layer = StrategistML()
    receipt = layer.boot()
    assert receipt.stale is True
    assert receipt.ready is False
    assert receipt.reason and receipt.reason.startswith("MODEL_STALE_OBSERVE_ONLY")
    # decide() must return NO_TRADE because boot flipped ready=False.
    f = _frame()
    f.perception = {"symbolic": {"decision": "BUY", "reason": "PASS"},
                    "scores": {k: {"score": 0.5, "confidence": 0.9}
                               for k in ("event_shock", "regime", "drawdown",
                                         "liquidity", "system_health", "pacing")}}
    v = layer.decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert "MODEL_STALE_OBSERVE_ONLY" in (v.reason or "")


def test_strategist_boot_ready_when_artifact_fresh(tmp_path, monkeypatch):
    boot_receipts.clear()
    p = _write_dummy_joblib(tmp_path, age_hours=1.0)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(p))
    layer = StrategistML()
    receipt = layer.boot()
    assert receipt.stale is False
    assert receipt.ready is True
    assert receipt.model_age_hours is not None and receipt.model_age_hours < 2.0


# ── (b) Feature-Health Weighting ─────────────────────────────────


def test_feature_health_empty_frame_low_score():
    score, diag = compute_feature_health(_frame())
    # No perception, no market, no lag — score should be at or below 0.3
    # (perception=0, completeness=0, freshness=0.5 → 0.10).
    assert 0.0 <= score <= 0.3
    assert diag["force_hold"] is True
    assert should_force_hold(score) is True


def test_feature_health_full_frame_high_score():
    f = _frame(
        broker_uptime=1.0, data_lag_ms=50.0,
        error_rate=0.0, spread_bps=5.0, vix=18.0,
    )
    f.perception = {"scores": {
        k: {"score": 0.7, "confidence": 0.9} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    score, diag = compute_feature_health(f)
    assert score > 0.85
    assert diag["force_hold"] is False
    assert diag["data_freshness"] == 1.0
    assert diag["market_completeness"] == 1.0


def test_strategist_force_hold_when_feature_health_low():
    boot_receipts.clear()
    f = _frame()  # no market data, no perception confidence
    f.perception = {"symbolic": {"decision": "BUY", "reason": "PASS"},
                    "scores": {k: {"score": 0.5, "confidence": 0.0}
                               for k in ("event_shock", "regime", "drawdown",
                                         "liquidity", "system_health", "pacing")}}
    v = StrategistML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "STRATEGIST_FEATURE_HEALTH_LOW"
    assert v.diagnostics["feature_health_score"] < 0.3


def test_strategist_confidence_clamped_by_feature_health():
    boot_receipts.clear()
    f = _frame(
        broker_uptime=1.0, data_lag_ms=50.0,
        error_rate=0.0, spread_bps=5.0, vix=18.0,
    )
    f.perception = {"symbolic": {"decision": "BUY", "reason": "PASS"},
                    "scores": {
                        "event_shock":   {"score": 0.1, "confidence": 0.6},
                        "regime":        {"score": 0.8, "confidence": 0.6},
                        "drawdown":      {"score": 0.3, "confidence": 0.6},
                        "liquidity":     {"score": 0.8, "confidence": 0.6},
                        "system_health": {"score": 0.8, "confidence": 0.6},
                        "pacing":        {"score": 0.7, "confidence": 0.6},
                    }}
    f.shelly_recall = {"episodes_found": 0}
    v = StrategistML().decide(f)
    assert v.diagnostics["feature_health_score"] is not None
    raw = v.diagnostics["raw_confidence"]
    eff = v.diagnostics["effective_confidence"]
    health = v.diagnostics["feature_health_score"]
    # effective_confidence == raw * health (within float tolerance).
    assert abs(eff - raw * health) < 1e-9
    assert v.diagnostics["clamped"] is True
    if v.decision == Verdict.BUY.value:
        assert v.reason in ("STRATEGIST_CONFIRM_CLAMPED", "STRATEGIST_CONFIRM")


# ── (c) Executor Heartbeat ───────────────────────────────────────


def test_heartbeat_records_runs_and_signals():
    executor_heartbeat.reset()
    executor_heartbeat.record_pipeline_run(
        lane="equity", decision="BUY", blocked_at=None,
        reason="EXECUTOR_APPROVE", feature_health=0.8,
    )
    executor_heartbeat.record_pipeline_run(
        lane="equity", decision="NO_TRADE", blocked_at="auditor",
        reason="AUDITOR_DOWNGRADE", feature_health=0.6,
    )
    executor_heartbeat.record_pipeline_run(
        lane="crypto", decision="NO_TRADE", blocked_at="strategist",
        reason="STRATEGIST_FEATURE_HEALTH_LOW", feature_health=0.1,
    )
    snap = executor_heartbeat.get_snapshot()
    eq = snap["lanes"]["equity"]
    cr = snap["lanes"]["crypto"]
    assert eq["signals_1h"] == 1
    assert eq["holds_1h"] == 1
    assert eq["last_decision"] == "NO_TRADE"
    assert eq["last_signal_at"] is not None
    assert cr["signals_1h"] == 0
    assert cr["holds_1h"] == 1
    assert cr["feature_health_avg"] is not None and cr["feature_health_avg"] < 0.2


def test_heartbeat_frozen_when_no_recent_run(monkeypatch):
    executor_heartbeat.reset()
    snap = executor_heartbeat.get_snapshot()
    # Brand-new tracker = no run recorded yet → frozen=True.
    assert snap["lanes"]["equity"]["frozen"] is True
    assert snap["lanes"]["crypto"]["frozen"] is True
    assert snap["any_frozen"] is True


def test_heartbeat_frozen_when_long_dry_with_low_health():
    executor_heartbeat.reset()
    for _ in range(5):
        executor_heartbeat.record_pipeline_run(
            lane="equity", decision="NO_TRADE", blocked_at="strategist",
            reason="STRATEGIST_FEATURE_HEALTH_LOW", feature_health=0.05,
        )
    snap = executor_heartbeat.get_snapshot()
    assert snap["lanes"]["equity"]["frozen"] is True
    assert snap["lanes"]["equity"]["signals_1h"] == 0


def test_pipeline_emits_heartbeat_on_decide():
    executor_heartbeat.reset()
    p = RisedualMLPipeline()
    p.decide(_frame(broker_uptime=1.0, data_lag_ms=50, error_rate=0.0))
    snap = executor_heartbeat.get_snapshot()
    assert snap["lanes"]["equity"]["last_pipeline_run_at"] is not None
    # Both lanes still tracked.
    assert "crypto" in snap["lanes"]
