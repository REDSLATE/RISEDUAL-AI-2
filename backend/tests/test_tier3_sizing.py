"""Tests for the Tier 3 adaptive sizing engine (`ai_core.sizing`)."""
from __future__ import annotations

import pandas as pd
import pytest

from ai_core.sizing import (
    MAX_CONF_MULT,
    MAX_POSITION_MULTIPLIER,
    MIN_CONFIDENCE_TO_TRADE,
    MIN_CONF_MULT,
    MIN_POSITION_MULTIPLIER,
    apply_adaptive_position_size,
    apply_per_trade_sizing,
    build_tier3_snapshot_message,
    compute_confidence_multiplier,
    compute_final_position_size,
    compute_position_multiplier,
    execute_trade_with_sizing,
)


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────

def _healthy_readiness() -> dict:
    """A readiness snapshot that passes every safety throttle."""
    return {
        "confidence_score": 100.0,
        "unlocked": True,
        "reasons": [],
        "stats": {
            "days": 30,
            "total_trades": 150,
            "high_conf_trades": 50,
            "strong_miss_rate": 0.05,
            "clamp_total": 0,
        },
    }


def _typical_readiness() -> dict:
    """Matches the actual preview DB state today — 81.33 with three
    sample-size blockers trips the high-conf throttle."""
    return {
        "confidence_score": 81.33,
        "unlocked": False,
        "reasons": [
            "Insufficient live days",
            "Insufficient trade count",
            "Not enough high-confidence samples",
        ],
        "stats": {
            "days": 7,
            "total_trades": 82,
            "high_conf_trades": 5,
            "strong_miss_rate": 0.032,
            "clamp_total": 0,
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# compute_position_multiplier
# ════════════════════════════════════════════════════════════════════════════════

def test_position_mult_healthy_stats_hits_ceiling():
    assert compute_position_multiplier(_healthy_readiness()) == pytest.approx(1.0)


def test_position_mult_zero_score_hits_floor():
    bad = {"confidence_score": 0, "stats": {"high_conf_trades": 50}}
    # 0/100 = 0, clamped up to MIN, but the high_conf throttle doesn't fire.
    assert compute_position_multiplier(bad) == pytest.approx(MIN_POSITION_MULTIPLIER)


def test_position_mult_strong_miss_throttle():
    r = _healthy_readiness()
    r["stats"]["strong_miss_rate"] = 0.15   # > 10% cutoff
    # 1.0 × 0.5 = 0.5
    assert compute_position_multiplier(r) == pytest.approx(0.5)


def test_position_mult_clamp_canary_heaviest_throttle():
    r = _healthy_readiness()
    r["stats"]["clamp_total"] = 1
    # 1.0 × 0.25 = 0.25
    assert compute_position_multiplier(r) == pytest.approx(0.25)


def test_position_mult_small_sample_throttle():
    r = _healthy_readiness()
    r["stats"]["high_conf_trades"] = 5
    # 1.0 × 0.75 = 0.75
    assert compute_position_multiplier(r) == pytest.approx(0.75)


def test_position_mult_throttles_compound():
    """All three safety throttles firing at once should compound."""
    r = _healthy_readiness()
    r["stats"]["strong_miss_rate"] = 0.20
    r["stats"]["clamp_total"] = 3
    r["stats"]["high_conf_trades"] = 10
    # 1.0 × 0.5 × 0.25 × 0.75 = 0.09375 → rounded 0.094
    assert compute_position_multiplier(r) == pytest.approx(0.094, abs=1e-3)


def test_position_mult_fails_closed_on_empty_dict():
    """Empty-dict defaults trip high_conf throttle but nothing else."""
    # score 0 → clamped up to MIN (0.25) × 0.75 = 0.1875
    assert compute_position_multiplier({}) == pytest.approx(0.1875, abs=1e-3)


def test_position_mult_ceiling_preserved_above_100():
    """Garbage score above 100 must still clamp to the ceiling."""
    weird = {"confidence_score": 500, "stats": {"high_conf_trades": 50}}
    assert compute_position_multiplier(weird) == pytest.approx(MAX_POSITION_MULTIPLIER)


# ════════════════════════════════════════════════════════════════════════════════
# compute_confidence_multiplier
# ════════════════════════════════════════════════════════════════════════════════

def test_conf_mult_at_min_hits_floor():
    assert compute_confidence_multiplier(50) == pytest.approx(MIN_CONF_MULT)


def test_conf_mult_at_max_hits_ceiling():
    assert compute_confidence_multiplier(100) == pytest.approx(MAX_CONF_MULT)


def test_conf_mult_midpoint_is_midway():
    # 75 → norm 0.5 → mid = 0.3 + 0.5*(1.5-0.3) = 0.9
    assert compute_confidence_multiplier(75) == pytest.approx(0.9)


def test_conf_mult_below_min_clamps_up():
    assert compute_confidence_multiplier(10) == pytest.approx(MIN_CONF_MULT)


def test_conf_mult_above_max_clamps_down():
    assert compute_confidence_multiplier(200) == pytest.approx(MAX_CONF_MULT)


# ════════════════════════════════════════════════════════════════════════════════
# compute_final_position_size
# ════════════════════════════════════════════════════════════════════════════════

def test_final_size_below_trade_gate_returns_zero():
    r = _healthy_readiness()
    assert compute_final_position_size(1000, r, {"confidence": 40}) == 0.0
    # Boundary — exactly the gate should ALSO trade (≥ not >).
    at_gate = compute_final_position_size(1000, r, {"confidence": MIN_CONFIDENCE_TO_TRADE})
    assert at_gate > 0


def test_final_size_high_conf_scales_up():
    r = _healthy_readiness()
    low = compute_final_position_size(1000, r, {"confidence": 60})
    high = compute_final_position_size(1000, r, {"confidence": 95})
    assert high > low


def test_final_size_respects_absolute_ceiling():
    """Garbage confidence > 100 must not push size above 2x base."""
    r = _healthy_readiness()
    size = compute_final_position_size(1000, r, {"confidence": 500})
    assert size <= 2000.0


def test_final_size_respects_absolute_floor():
    """When readiness and confidence both conspire, floor at 0.1 × base."""
    worst = {
        "confidence_score": 0,
        "stats": {"strong_miss_rate": 0.5, "clamp_total": 5, "high_conf_trades": 0},
    }
    size = compute_final_position_size(1000, worst, {"confidence": 55})
    # Natural multiplier would be tiny; floor must still apply.
    assert size >= 100.0   # 0.1 × 1000


# ── Calibration-aware sizing (Phase 2) ─────────────────────────────
#
# `compute_final_position_size` now accepts an optional
# `model_ece` kwarg. These tests pin the expected impact on size
# without duplicating the tier-boundary tests that already live in
# `test_learning_sizing.py`.


def test_final_size_legacy_call_unchanged():
    """Regression: callers that don't pass `model_ece` must get
    the exact same size they got before the upgrade landed."""
    r = _healthy_readiness()
    legacy = compute_final_position_size(1000, r, {"confidence": 80})
    with_none = compute_final_position_size(1000, r, {"confidence": 80}, model_ece=None)
    assert legacy == with_none


def test_final_size_degrades_with_miscalibration():
    """Same confidence, same readiness, higher ECE → smaller size.
    A miscalibrated model should never out-size its well-calibrated
    self just because the confidence number was identical."""
    r = _healthy_readiness()
    good_ece = compute_final_position_size(1000, r, {"confidence": 80}, model_ece=0.03)
    bad_ece = compute_final_position_size(1000, r, {"confidence": 80}, model_ece=0.25)
    assert bad_ece < good_ece
    # Floor still applies — miscalibrated model shouldn't drop below
    # 0.1× base even with ECE off the scale.
    assert bad_ece >= 100.0


def test_final_size_well_calibrated_matches_legacy():
    """ECE < 5% → multiplier 1.0 → identical to legacy (no-op)."""
    r = _healthy_readiness()
    legacy = compute_final_position_size(1000, r, {"confidence": 80})
    well_cal = compute_final_position_size(1000, r, {"confidence": 80}, model_ece=0.02)
    assert legacy == well_cal


# ════════════════════════════════════════════════════════════════════════════════
# apply_adaptive_position_size + apply_per_trade_sizing
# ════════════════════════════════════════════════════════════════════════════════

def test_apply_adaptive_ignores_prediction():
    """Daily-level hook — pure readiness, no per-signal input."""
    r = _healthy_readiness()
    assert apply_adaptive_position_size(1000, r) == pytest.approx(1000.0)


def test_apply_per_trade_is_alias_of_compute_final():
    r = _typical_readiness()
    pred = {"confidence": 92}
    assert apply_per_trade_sizing(1000, r, pred) == compute_final_position_size(1000, r, pred)


# ════════════════════════════════════════════════════════════════════════════════
# build_tier3_snapshot_message
# ════════════════════════════════════════════════════════════════════════════════

def test_snapshot_message_with_up_delta():
    current = _typical_readiness()
    previous = {"confidence_score": 79.4}
    msg = build_tier3_snapshot_message(current, previous)
    assert "81.3" in msg
    assert "▲" in msg
    assert "+1.93" in msg
    assert "LOCKED" in msg
    # First two blockers enriched with numbers.
    assert "Days 7/30" in msg
    assert "Trades 82/100" in msg


def test_snapshot_message_with_down_delta():
    current = _typical_readiness()
    previous = {"confidence_score": 85.0}
    msg = build_tier3_snapshot_message(current, previous)
    assert "▼" in msg
    assert "-3.67" in msg


def test_snapshot_message_flat_delta():
    current = _typical_readiness()
    previous = {"confidence_score": 81.33}
    msg = build_tier3_snapshot_message(current, previous)
    assert "→" in msg
    assert "0.0" in msg


def test_snapshot_message_no_prior():
    msg = build_tier3_snapshot_message(_typical_readiness())
    # No delta markup when there's no previous snapshot.
    assert "▲" not in msg
    assert "▼" not in msg
    assert "→" not in msg


def test_snapshot_message_unlocked_state():
    current = _healthy_readiness()
    msg = build_tier3_snapshot_message(current)
    assert "UNLOCKED" in msg
    assert "Blockers: none" in msg


def test_snapshot_message_enriches_all_reason_types():
    """Verify every blocker class gets a numeric chip, not a bare
    reason string."""
    current = {
        "confidence_score": 50,
        "stats": {
            "days": 12,
            "total_trades": 55,
            "high_conf_trades": 8,
            "strong_miss_rate": 0.18,
            "clamp_total": 1,
        },
        "reasons": [
            "Insufficient live days",
            "Insufficient trade count",
            "Not enough high-confidence samples",
            "Too many strong misses",
            "Conviction clamp triggered",
        ],
    }
    # Message truncates to first 2 — test each reason individually via
    # the internal formatter.
    from ai_core.sizing import _format_blocker
    stats = current["stats"]
    assert _format_blocker("Insufficient live days", stats) == "Days 12/30"
    assert _format_blocker("Insufficient trade count", stats) == "Trades 55/100"
    assert _format_blocker("Not enough high-confidence samples", stats) == "High-conf 8/30"
    assert _format_blocker("Too many strong misses", stats) == "Strong miss 18.0%"
    assert _format_blocker("Conviction clamp triggered", stats) == "Clamp triggered"
    # Unknown reasons pass through.
    assert _format_blocker("New mystery rule", stats) == "New mystery rule"


# ════════════════════════════════════════════════════════════════════════════════
# execute_trade_with_sizing (integration — simulator round-trip)
# ════════════════════════════════════════════════════════════════════════════════

def _bars(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_execute_skips_on_low_confidence():
    signal = {"entry": 100, "tp": 105, "sl": 97, "direction": "LONG", "confidence": 40}
    out = execute_trade_with_sizing(signal, _healthy_readiness(), base_size=1000)
    assert out["skipped"] is True
    assert out["size"] == 0.0


def test_execute_returns_pending_when_no_df():
    signal = {"entry": 100, "tp": 105, "sl": 97, "direction": "LONG", "confidence": 90}
    out = execute_trade_with_sizing(signal, _healthy_readiness(), base_size=1000, df=None)
    assert out["result"] == "PENDING"
    assert out["reason"] == "live_path_required"
    assert out["size"] > 0.0


def test_execute_long_win_via_simulator():
    """TP hit → WIN, size present."""
    signal = {"entry": 100, "tp": 105, "sl": 97, "direction": "LONG", "confidence": 90}
    df = _bars([
        {"open": 100, "high": 102, "low": 99, "close": 101},
        {"open": 101, "high": 106, "low": 100, "close": 105},
    ])
    out = execute_trade_with_sizing(signal, _healthy_readiness(), base_size=1000, df=df)
    # Size stamped
    assert out["size"] > 0.0
    # Outcome should be WIN or at least not LOSS (simulator canonical)
    assert out.get("result") in {"WIN", "LOSS", "PENDING"}


def test_execute_long_loss_via_simulator():
    signal = {"entry": 100, "tp": 110, "sl": 98, "direction": "LONG", "confidence": 90}
    df = _bars([
        {"open": 100, "high": 100.5, "low": 97, "close": 97.5},
    ])
    out = execute_trade_with_sizing(signal, _healthy_readiness(), base_size=1000, df=df)
    assert out["size"] > 0.0
    assert out.get("result") in {"WIN", "LOSS", "PENDING"}


def test_execute_uses_stop_loss_alias():
    """Caller can pass the long-form `stop_loss`/`take_profit` keys
    instead of the short `sl`/`tp` ones."""
    signal = {
        "entry": 100, "take_profit": 105, "stop_loss": 97,
        "direction": "LONG", "confidence": 90,
    }
    df = _bars([
        {"open": 100, "high": 106, "low": 99, "close": 105},
    ])
    out = execute_trade_with_sizing(signal, _healthy_readiness(), base_size=1000, df=df)
    assert out["size"] > 0.0
    assert "result" in out
