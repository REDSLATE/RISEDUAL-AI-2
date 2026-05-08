"""Regression tests for Tier 3 composite unlock gate + readiness score.

The `build_tier3_stats` DB path is exercised indirectly through the
pure-function unit surface — that's where the rule-of-6 correctness
lives. DB wiring is tiny (aggregation + count) and covered via the
live smoke test in the deployment notes.
"""
from __future__ import annotations

import pytest

from services.tier3_readiness import (
    CALIBRATION_GAP_LIMIT,
    HIGH_CONF_THRESHOLD,
    MAX_STRONG_MISS_RATE,
    MIN_DAYS,
    MIN_HIGH_CONF_SAMPLES,
    MIN_HIGH_CONF_WIN_RATE,
    MIN_TOTAL_TRADES,
    STABILITY_DROP_LIMIT,
    check_tier3_unlock,
    compute_tier3_score,
)


def _perfect_stats() -> dict:
    """A stats dict that passes every single check."""
    return {
        "days": 30,
        "total_trades": 150,
        "high_conf_trades": 50,
        "high_conf_win_rate": 0.80,
        "avg_confidence": 70.0,          # 70% stated vs 80% actual → gap 0.10 ≤ 0.15
        "strong_miss_rate": 0.05,
        "overall_win_rate": 0.75,
        "last_7d_win_rate": 0.72,        # within STABILITY_DROP_LIMIT
        "clamp_total": 0,
    }


# ────────────────────────────────────────────────────────────────────────────────
# Tunables sanity — lock in the public contract
# ────────────────────────────────────────────────────────────────────────────────

def test_tunables_are_sane():
    assert MIN_DAYS == 30
    assert MIN_TOTAL_TRADES == 100
    assert MIN_HIGH_CONF_SAMPLES == 30
    assert MIN_HIGH_CONF_WIN_RATE == 0.75
    assert CALIBRATION_GAP_LIMIT == 0.15
    assert MAX_STRONG_MISS_RATE == 0.10
    assert STABILITY_DROP_LIMIT == 0.15
    assert HIGH_CONF_THRESHOLD == 70.0


# ────────────────────────────────────────────────────────────────────────────────
# check_tier3_unlock — one failing check at a time
# ────────────────────────────────────────────────────────────────────────────────

def test_perfect_stats_unlocks():
    out = check_tier3_unlock(_perfect_stats())
    assert out["unlocked"] is True
    assert out["reasons"] == []
    assert 90 <= out["confidence_score"] <= 100


def test_fails_closed_on_empty_stats():
    """Empty dict = every default is 0 → fail-closed."""
    out = check_tier3_unlock({})
    assert out["unlocked"] is False
    # Must flag at least exposure + trade count + high-conf samples +
    # (strong_miss default is 1.0 so risk-control trips too) + recency.
    assert "Insufficient live days" in out["reasons"]
    assert "Insufficient trade count" in out["reasons"]
    assert "Not enough high-confidence samples" in out["reasons"]
    assert "Too many strong misses" in out["reasons"]


def test_insufficient_days_flagged():
    s = _perfect_stats()
    s["days"] = 10
    out = check_tier3_unlock(s)
    assert out["unlocked"] is False
    assert "Insufficient live days" in out["reasons"]


def test_insufficient_trade_count_flagged():
    s = _perfect_stats()
    s["total_trades"] = 42
    out = check_tier3_unlock(s)
    assert "Insufficient trade count" in out["reasons"]


def test_insufficient_high_conf_samples_flagged():
    s = _perfect_stats()
    s["high_conf_trades"] = 10
    out = check_tier3_unlock(s)
    assert "Not enough high-confidence samples" in out["reasons"]


def test_low_high_conf_win_rate_flagged():
    s = _perfect_stats()
    s["high_conf_win_rate"] = 0.60
    out = check_tier3_unlock(s)
    assert "High-confidence win rate too low" in out["reasons"]


def test_sample_count_check_short_circuits_win_rate_check():
    """When we don't have enough high-conf samples, the win-rate
    reason should NOT also fire — avoids double-flagging the same
    upstream fact."""
    s = _perfect_stats()
    s["high_conf_trades"] = 5
    s["high_conf_win_rate"] = 0.10  # would fail if evaluated
    out = check_tier3_unlock(s)
    assert "Not enough high-confidence samples" in out["reasons"]
    assert "High-confidence win rate too low" not in out["reasons"]


def test_calibration_gap_flagged():
    s = _perfect_stats()
    s["high_conf_win_rate"] = 0.95
    s["avg_confidence"] = 70.0         # gap = 0.25 > 0.15
    out = check_tier3_unlock(s)
    assert "Confidence calibration off" in out["reasons"]


def test_calibration_check_gated_by_sample_size():
    """Calibration is meaningless with <30 samples — shouldn't fire."""
    s = _perfect_stats()
    s["high_conf_trades"] = 5
    s["high_conf_win_rate"] = 0.95
    s["avg_confidence"] = 40.0         # large gap, but too few samples
    out = check_tier3_unlock(s)
    assert "Confidence calibration off" not in out["reasons"]


def test_strong_miss_rate_flagged():
    s = _perfect_stats()
    s["strong_miss_rate"] = 0.25
    out = check_tier3_unlock(s)
    assert "Too many strong misses" in out["reasons"]


def test_stability_drop_flagged():
    s = _perfect_stats()
    s["overall_win_rate"] = 0.80
    s["last_7d_win_rate"] = 0.50       # 30 pp drop
    out = check_tier3_unlock(s)
    assert "Recent performance unstable" in out["reasons"]


def test_stability_within_tolerance_passes():
    s = _perfect_stats()
    s["overall_win_rate"] = 0.80
    s["last_7d_win_rate"] = 0.70       # 10 pp drop ≤ 15 pp limit
    out = check_tier3_unlock(s)
    assert "Recent performance unstable" not in out["reasons"]


def test_clamp_canary_flagged():
    s = _perfect_stats()
    s["clamp_total"] = 3
    out = check_tier3_unlock(s)
    assert out["unlocked"] is False
    assert "Conviction clamp triggered" in out["reasons"]


# ────────────────────────────────────────────────────────────────────────────────
# compute_tier3_score
# ────────────────────────────────────────────────────────────────────────────────

def test_score_perfect_stats_near_max():
    s = _perfect_stats()
    assert 90 <= compute_tier3_score(s) <= 100


def test_score_zero_stats_near_zero():
    """All defaults at 0 except `strong_miss_rate` which defaults to 1.0
    in the function body — leaving only the 10-point canary term."""
    score = compute_tier3_score({})
    assert score == pytest.approx(10.0)


def test_score_respects_component_weights_sum_to_100():
    """Independent proof that weights sum to 100 via a perfect-score
    ceiling check. Protects against future weight tweaks silently
    drifting the UI scale."""
    ideal = {
        "days": MIN_DAYS,
        "total_trades": MIN_TOTAL_TRADES,
        "high_conf_win_rate": 1.0,
        "strong_miss_rate": 0.0,
        "last_7d_win_rate": 1.0,
        "clamp_total": 0,
    }
    assert compute_tier3_score(ideal) == pytest.approx(100.0)


def test_score_degrades_monotonically_with_days():
    low = compute_tier3_score({**_perfect_stats(), "days": 0})
    mid = compute_tier3_score({**_perfect_stats(), "days": 15})
    hi = compute_tier3_score({**_perfect_stats(), "days": 30})
    assert low < mid < hi


def test_score_canary_knocks_ten_points():
    hot = compute_tier3_score({**_perfect_stats(), "clamp_total": 0})
    cold = compute_tier3_score({**_perfect_stats(), "clamp_total": 1})
    assert hot - cold == pytest.approx(10.0)


def test_score_clamps_win_rates_to_valid_range():
    """Garbage win-rates > 1.0 must not push the total past 100."""
    bad = {
        **_perfect_stats(),
        "high_conf_win_rate": 2.0,      # garbage
        "last_7d_win_rate": 2.0,        # garbage
        "strong_miss_rate": -1.0,       # garbage
    }
    assert compute_tier3_score(bad) <= 100.0
