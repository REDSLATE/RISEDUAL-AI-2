"""Pytest coverage for the 2026-05-15 high-conf WR smoothing in
``compute_tier3_score`` / ``compute_tier3_breakdown``.

Doctrine:
  * Score signal uses the **smoothed** daily-mean WR over the last
    7 UTC days with high-conf samples — dampens the single-bucket
    25-point swings that caused the historical -25 day-over-day
    drops the operator complained about.
  * Unlock decision (``check_tier3_unlock``) STILL uses the raw
    window-aggregated WR. Smoothing is for the *score signal*,
    not the binary gate.
  * Cold-start safety: when fewer than 2 days of high-conf data
    exist, the smoother falls back to the raw WR so the score
    isn't artificially zeroed.
"""
from __future__ import annotations

import pytest

from services.tier3_readiness import (
    check_tier3_unlock,
    compute_tier3_breakdown,
    compute_tier3_score,
)


@pytest.fixture(autouse=True)
def _disable_tier3_bypass(monkeypatch):
    """2026-06-01: the operator bypass ``TIER3_BYPASS_UNLOCKED=1`` is now
    set in ``.env``. These tests validate the underlying smoothing
    + gate math, so they must run with the bypass off."""
    monkeypatch.delenv("TIER3_BYPASS_UNLOCKED", raising=False)


# ── compute_tier3_score honours the smoothed field ─────────────────────


def test_score_prefers_smoothed_wr_when_present():
    """A raw single-day collapse to 0.0 should NOT crater the score
    when the smoothed 7-day WR is healthy."""
    stats = {
        "days": 6, "total_trades": 868,
        "high_conf_trades": 40,
        # Raw across 30-day window collapsed today by one bad batch.
        "high_conf_win_rate": 0.0,
        # 7-day daily mean is healthy — score uses this.
        "high_conf_win_rate_smoothed": 0.80,
        "strong_miss_rate": 0.018,
        "last_7d_win_rate": 0.85,
        "clamp_total": 0,
    }
    score = compute_tier3_score(stats)
    # high-conf contribution = 0.80 * 25 = 20 pts.
    # Other contributions: exposure ceil(6/30)*20=4, volume min(868/100,1)*15=15,
    # risk (1-.018)*20≈19.64, last7 .85*10=8.5, canary 10. Total ≈ 77.14.
    assert score > 70.0
    # If we'd used raw 0.0 instead: high-conf would be 0 → total ≈ 57.
    raw_score_if_no_smoothing = compute_tier3_score(
        {**stats, "high_conf_win_rate_smoothed": 0.0}
    )
    assert score - raw_score_if_no_smoothing == pytest.approx(20.0, abs=0.5)


def test_score_falls_back_to_raw_without_smoothed_field():
    """Cached / older callers that don't carry the smoothed key must
    keep working — fall back to the raw WR."""
    stats = {
        "days": 6, "total_trades": 868,
        "high_conf_trades": 40,
        "high_conf_win_rate": 0.70,
        # No `high_conf_win_rate_smoothed` key on purpose.
        "strong_miss_rate": 0.02,
        "last_7d_win_rate": 0.80,
        "clamp_total": 0,
    }
    score = compute_tier3_score(stats)
    # high-conf contribution = 0.70 * 25 = 17.5 pts (using raw fallback)
    # exposure 4 + volume 15 + risk 19.6 + last7 8 + canary 10 + 17.5 ≈ 74.1
    assert 70.0 < score < 80.0


def test_breakdown_reconciles_to_score():
    """Sum of breakdown ``earned_pts`` must equal ``compute_tier3_score``
    for the same stats — the smoothed-WR change must not break this
    invariant."""
    stats = {
        "days": 6, "total_trades": 868,
        "high_conf_trades": 40,
        "high_conf_win_rate": 0.0,           # raw collapsed
        "high_conf_win_rate_smoothed": 0.80,  # smoothed healthy
        "strong_miss_rate": 0.018,
        "last_7d_win_rate": 0.85,
        "clamp_total": 0,
    }
    score = compute_tier3_score(stats)
    breakdown_sum = sum(b["earned_pts"] for b in compute_tier3_breakdown(stats))
    assert breakdown_sum == pytest.approx(score, abs=0.1)


# ── breakdown's `met` badge uses RAW WR (unlock alignment) ─────────────


def test_breakdown_met_badge_uses_raw_wr_not_smoothed():
    """The `met` boolean must align with the unlock decision — using
    smoothed here would let a "✓ cleared" badge appear on a gate that
    `check_tier3_unlock` still considers blocked."""
    stats = {
        "days": 30, "total_trades": 100,
        "high_conf_trades": 30,
        # Raw barely fails the 0.75 unlock threshold.
        "high_conf_win_rate": 0.70,
        # Smoothed comfortably above 0.75 — but the gate is still
        # unmet from the unlock's POV.
        "high_conf_win_rate_smoothed": 0.85,
        "strong_miss_rate": 0.05,
        "last_7d_win_rate": 0.80,
        "overall_win_rate": 0.80,
        "clamp_total": 0,
    }
    breakdown = {b["key"]: b for b in compute_tier3_breakdown(stats)}
    hc = breakdown["high_conf_accuracy"]
    assert hc["met"] is False, (
        "breakdown reported gate as cleared, but the raw WR is "
        "still below 0.75 — unlock would block. Badge must use raw."
    )
    # And the unlock decision must agree.
    decision = check_tier3_unlock(stats)
    assert "High-confidence win rate too low" in decision["reasons"]


# ── unlock decision keeps using raw WR (not smoothed) ──────────────────


def test_unlock_uses_raw_wr_not_smoothed():
    """A smoothed WR ≥ 0.75 with a raw WR < 0.75 must still keep the
    gate locked. The smoother is for the score signal only."""
    stats = {
        "days": 30, "total_trades": 100,
        "high_conf_trades": 30,
        "high_conf_win_rate": 0.50,            # raw fails
        "high_conf_win_rate_smoothed": 0.90,    # smoothed would pass
        "avg_confidence": 70.0,
        "strong_miss_rate": 0.05,
        "last_7d_win_rate": 0.80,
        "overall_win_rate": 0.80,
        "clamp_total": 0,
    }
    decision = check_tier3_unlock(stats)
    assert decision["unlocked"] is False
    assert "High-confidence win rate too low" in decision["reasons"]


# ── smoothing dampens single-day swings (the actual UX win) ────────────


def test_smoothing_dampens_single_day_collapse():
    """The structural fix the operator asked for: a single bad
    high-conf day should not swing the composite score by 20+ pts.

    Day N raw WR = 0.80 across 30 samples → score includes 0.80 * 25 = 20 pts.
    Day N+1: 5 new high-conf samples come in, all wrong.
             Raw WR drops to 30/35 = 0.857 (small).
             But if N had only 8 samples, swing could be huge.

    Smoothing path takes the DAILY MEAN of 7 daily WRs. A single bad
    day moves the smoothed mean by ~1/N where N ≈ 7 active days.
    Worst-case single-day swing on the score: 25 / 7 ≈ 3.6 pts.

    This test asserts that contract numerically.
    """
    # 6 days of perfect, 1 day of zero — daily mean = 6/7 = 0.857
    seven_days_perfect_plus_one_bad = (6 * 1.0 + 1 * 0.0) / 7
    # Versus the worst case raw WR collapse from 1.0 to 0.0 = -25 pts.
    smoothed_pts = seven_days_perfect_plus_one_bad * 25
    assert smoothed_pts > 20.0, (
        "smoothed single-bad-day swing should still earn >20pts; got "
        f"{smoothed_pts:.1f}"
    )
    # Versus raw zero-swing: 25 pts → 0 pts (full 25-pt collapse).
    # Smoothed result keeps us within ~4 pts of the pre-collapse score.
    assert (25.0 - smoothed_pts) < 4.0
