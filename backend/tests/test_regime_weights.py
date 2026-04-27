"""Tests for the regime-conditional weights scaffolding.

The whole module is default-inert until ≥30 dissents per regime
bucket accumulate. Tests verify that invariant + the safety bounds.
"""
from __future__ import annotations

import pytest

from services.regime_weights import (
    MAX_REGIME_WEIGHT,
    MIN_REGIME_SAMPLES,
    MIN_REGIME_WEIGHT,
    NEUTRAL_WEIGHT,
    _clamp,
    _win_rate_to_weight,
    compute_regime_weights,
    lookup_weight,
)


# ── Win-rate to weight envelope ───────────────────────────────────────────────


def test_win_rate_to_weight_strong_upweight():
    assert _win_rate_to_weight(0.75) == 1.20
    assert _win_rate_to_weight(0.65) == 1.20


def test_win_rate_to_weight_small_upweight():
    assert _win_rate_to_weight(0.60) == 1.10
    assert _win_rate_to_weight(0.55) == 1.10


def test_win_rate_to_weight_neutral_band():
    assert _win_rate_to_weight(0.50) == NEUTRAL_WEIGHT
    assert _win_rate_to_weight(0.45) == NEUTRAL_WEIGHT


def test_win_rate_to_weight_small_downweight():
    assert _win_rate_to_weight(0.40) == 0.85
    assert _win_rate_to_weight(0.35) == 0.85


def test_win_rate_to_weight_strong_downweight():
    assert _win_rate_to_weight(0.20) == 0.70
    assert _win_rate_to_weight(0.30) == 0.70


def test_win_rate_to_weight_none_returns_neutral():
    assert _win_rate_to_weight(None) == NEUTRAL_WEIGHT


# ── Clamp ─────────────────────────────────────────────────────────────────────


def test_clamp_within_bounds_unchanged():
    assert _clamp(1.0) == 1.0
    assert _clamp(0.85) == 0.85


def test_clamp_floor_and_ceiling():
    assert _clamp(0.10) == MIN_REGIME_WEIGHT
    assert _clamp(2.0) == MAX_REGIME_WEIGHT


# ── Compute regime weights — maturity guardrail ───────────────────────────────


def test_under_threshold_returns_neutral():
    """Below MIN_REGIME_SAMPLES → weight stays 1.0 + actionable=False."""
    stats = {"buckets": [{
        "regime": "trending", "shadow_engine": "council",
        "asset_type": "crypto",
        "scored_dissent_count": MIN_REGIME_SAMPLES - 1,
        "win_rate": 0.95,  # would normally trigger 1.20
        "total_delta_usd": 100.0,
    }]}
    out = compute_regime_weights(stats)
    rec = out["weights"]["crypto"]["trending"]
    assert rec["weight"] == NEUTRAL_WEIGHT  # not 1.20 — under-threshold
    assert rec["actionable"] is False
    assert rec["needed_samples"] == 1


def test_at_threshold_applies_envelope():
    stats = {"buckets": [{
        "regime": "trending", "shadow_engine": "council",
        "asset_type": "crypto",
        "scored_dissent_count": MIN_REGIME_SAMPLES,
        "win_rate": 0.62,
        "total_delta_usd": 25.0,
    }]}
    out = compute_regime_weights(stats)
    rec = out["weights"]["crypto"]["trending"]
    assert rec["weight"] == 1.10
    assert rec["actionable"] is True
    assert rec["needed_samples"] == 0


def test_strong_negative_winrate_clamps_at_floor():
    """0.70 from envelope is above the 0.50 floor — but if envelope
    ever drops below floor, clamp protects."""
    stats = {"buckets": [{
        "regime": "parabolic", "shadow_engine": "council",
        "asset_type": "crypto",
        "scored_dissent_count": 50,
        "win_rate": 0.10,
        "total_delta_usd": -50.0,
    }]}
    out = compute_regime_weights(stats)
    weight = out["weights"]["crypto"]["parabolic"]["weight"]
    assert MIN_REGIME_WEIGHT <= weight <= MAX_REGIME_WEIGHT


def test_buckets_with_missing_regime_or_asset_skipped():
    stats = {"buckets": [
        {"regime": None, "asset_type": "crypto", "scored_dissent_count": 50,
         "win_rate": 0.7},
        {"regime": "trending", "asset_type": None, "scored_dissent_count": 50,
         "win_rate": 0.7},
        {"regime": "trending", "asset_type": "crypto", "scored_dissent_count": 50,
         "win_rate": 0.7},
    ]}
    out = compute_regime_weights(stats)
    # Only the last (well-formed) bucket should appear.
    assert "crypto" in out["weights"]
    assert "trending" in out["weights"]["crypto"]


def test_empty_stats_produces_empty_weights():
    out = compute_regime_weights({})
    assert out["weights"] == {}
    assert out["min_samples_required"] == MIN_REGIME_SAMPLES


# ── Lookup helper — double gate ───────────────────────────────────────────────


def test_lookup_returns_neutral_when_env_flag_off(monkeypatch):
    """The framework env flag is the master switch — even an
    actionable bucket should not influence sizing while it's off."""
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", False)
    payload = {"weights": {"crypto": {"trending": {
        "weight": 1.20, "actionable": True,
    }}}}
    assert lookup_weight(payload, asset_type="crypto", regime="trending") == NEUTRAL_WEIGHT


def test_lookup_returns_neutral_when_unmatched_bucket(monkeypatch):
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", True)
    payload = {"weights": {"crypto": {"trending": {
        "weight": 1.20, "actionable": True,
    }}}}
    assert lookup_weight(payload, asset_type="stock", regime="trending") == NEUTRAL_WEIGHT
    assert lookup_weight(payload, asset_type="crypto", regime="parabolic") == NEUTRAL_WEIGHT


def test_lookup_returns_neutral_when_under_threshold(monkeypatch):
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", True)
    payload = {"weights": {"crypto": {"trending": {
        "weight": 1.20, "actionable": False,
    }}}}
    # Even with the env flag on, an under-threshold bucket should stay neutral.
    assert lookup_weight(payload, asset_type="crypto", regime="trending") == NEUTRAL_WEIGHT


def test_lookup_returns_weight_when_both_gates_pass(monkeypatch):
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", True)
    payload = {"weights": {"crypto": {"trending": {
        "weight": 1.10, "actionable": True,
    }}}}
    assert lookup_weight(payload, asset_type="crypto", regime="trending") == 1.10


def test_lookup_clamps_runaway_payload_value(monkeypatch):
    """If a regression in compute_regime_weights ever leaks an
    out-of-bounds value, the lookup helper still clamps it."""
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", True)
    payload = {"weights": {"crypto": {"trending": {
        "weight": 5.0, "actionable": True,  # impossibly high
    }}}}
    out = lookup_weight(payload, asset_type="crypto", regime="trending")
    assert out == MAX_REGIME_WEIGHT


def test_lookup_handles_invalid_weight_gracefully(monkeypatch):
    monkeypatch.setattr("services.regime_weights.REGIME_WEIGHTS_ENABLED", True)
    payload = {"weights": {"crypto": {"trending": {
        "weight": "not_a_number", "actionable": True,
    }}}}
    assert lookup_weight(payload, asset_type="crypto", regime="trending") == NEUTRAL_WEIGHT


# ── Bound discipline ──────────────────────────────────────────────────────────


def test_bounds_pinned_in_code():
    """Hard bounds must stay code-constants. An operator misconfig
    cannot unlock 0× or 2× sizing."""
    from services import regime_weights as rw
    assert rw.MAX_REGIME_WEIGHT == 1.25
    assert rw.MIN_REGIME_WEIGHT == 0.50
    assert rw.NEUTRAL_WEIGHT == 1.0
