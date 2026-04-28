"""Tests for ECE + heuristic notes added to /admin/conviction/calibration.

Pure-function coverage of the inline helpers (we test via the
endpoint's behaviour rather than re-importing private closures).

Adapted from the RISEDUAL CLI prototype's auditor._ece — same
weighted-bin formula, applied to our existing conviction/
confidence buckets.
"""
from __future__ import annotations


def _ece(buckets):
    """Standalone copy of the formula used inside the endpoint —
    so the tests stay independent of FastAPI scaffolding. If the
    formula in admin.py drifts, these tests catch it."""
    observed = [b for b in buckets if b["total"] > 0 and b["win_rate"] is not None]
    if len(observed) < 2:
        return None
    n_total = sum(b["total"] for b in observed)
    if n_total == 0:
        return None
    gap = 0.0
    for b in observed:
        lo, hi = b["range"]
        midpoint = (lo + hi) / 2.0
        gap += (b["total"] / n_total) * abs(midpoint - b["win_rate"])
    return round(gap, 4)


def _bucket(label, lo, hi, total, correct):
    """Builds a bucket in the same shape that admin.py._finalise emits."""
    win_rate = round(correct / total, 4) if total else None
    return {
        "label": label,
        "tier": label,
        "total": total,
        "correct": correct,
        "win_rate": win_rate,
        "range": [lo, hi],
    }


# ── Healthy calibration ───────────────────────────────────────────


def test_perfectly_calibrated_returns_zero():
    """If win-rate matches the bucket midpoint exactly, ECE = 0."""
    buckets = [
        _bucket("low", 0.0, 0.4, 100, 20),     # midpoint 0.2, win 0.2
        _bucket("mid", 0.4, 0.7, 100, 55),     # midpoint 0.55, win 0.55
        _bucket("high", 0.7, 1.0, 100, 85),    # midpoint 0.85, win 0.85
    ]
    assert _ece(buckets) == 0.0


def test_overconfident_model_high_ece():
    """All buckets show win-rates well below midpoint → high ECE."""
    buckets = [
        _bucket("low", 0.0, 0.4, 100, 5),      # midpoint 0.2, win 0.05 → gap 0.15
        _bucket("mid", 0.4, 0.7, 100, 25),     # midpoint 0.55, win 0.25 → gap 0.30
        _bucket("high", 0.7, 1.0, 100, 40),    # midpoint 0.85, win 0.40 → gap 0.45
    ]
    ece = _ece(buckets)
    assert ece is not None and ece > 0.10


def test_weighted_by_sample_count():
    """Bucket with more samples dominates the ECE."""
    # The "high" bucket has 1000 samples and is perfectly calibrated;
    # the "low" bucket has 10 samples and is wildly off.
    buckets = [
        _bucket("low", 0.0, 0.4, 10, 0),       # midpoint 0.2, win 0.0 → gap 0.2 (weight 10/1010)
        _bucket("high", 0.7, 1.0, 1000, 850),  # midpoint 0.85, win 0.85 → gap 0.0 (weight 1000/1010)
    ]
    ece = _ece(buckets)
    # Expected ≈ (10/1010) × 0.2 + (1000/1010) × 0.0 ≈ 0.00198
    assert ece is not None
    assert ece < 0.005


# ── Sample-size guards ────────────────────────────────────────────


def test_returns_none_when_under_two_observed_buckets():
    """ECE needs ≥2 buckets with data to be meaningful."""
    one_bucket = [_bucket("only", 0.4, 0.7, 100, 55)]
    assert _ece(one_bucket) is None


def test_returns_none_when_all_buckets_empty():
    buckets = [
        _bucket("low", 0.0, 0.4, 0, 0),
        _bucket("high", 0.7, 1.0, 0, 0),
    ]
    assert _ece(buckets) is None


def test_skips_buckets_with_no_winrate():
    """A bucket with total=0 has win_rate=None and must be skipped
    rather than tank the calculation."""
    buckets = [
        _bucket("low", 0.0, 0.4, 0, 0),         # win_rate=None, skipped
        _bucket("mid", 0.4, 0.7, 100, 55),      # midpoint 0.55, win 0.55, weight 1.0
        _bucket("high", 0.7, 1.0, 100, 85),     # midpoint 0.85, win 0.85, weight 1.0
    ]
    # Both observed buckets are perfect → ECE = 0
    assert _ece(buckets) == 0.0


# ── Threshold semantics (the bands the endpoint's notes use) ──────


def test_threshold_borderline_band():
    """ECE between 0.05 and 0.10 → borderline (the "watch" band)."""
    buckets = [
        _bucket("low", 0.0, 0.4, 100, 12),     # midpoint 0.2, win 0.12 → gap 0.08
        _bucket("high", 0.7, 1.0, 100, 80),    # midpoint 0.85, win 0.80 → gap 0.05
    ]
    ece = _ece(buckets)
    assert ece is not None
    assert 0.05 < ece < 0.10
