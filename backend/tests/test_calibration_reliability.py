"""Tests for the decile reliability-diagram calibration service."""
from __future__ import annotations

import pytest

from services.calibration_reliability import (
    bucket_for,
    compute_reliability,
    normalise_confidence,
)


# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────

def _row(conf, correct):
    return {"confidence": conf, "verified_24h": {"correct": correct}}


# ────────────────────────────────────────────────────────────────────────────────
# normalise_confidence
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,want",
    [
        (50, 50.0),
        (50.0, 50.0),
        ("72", 72.0),
        (0.75, 75.0),      # 0-1 fractional scale scaled up
        (1.0, 100.0),      # edge: treated as fractional
        (150, 100.0),      # clamped to 100
        (-10, 0.0),        # clamped to 0
    ],
)
def test_normalise_confidence_accepts_both_scales(raw, want):
    assert normalise_confidence(raw) == pytest.approx(want)


@pytest.mark.parametrize("raw", [None, "abc", {"x": 1}])
def test_normalise_confidence_returns_none_for_garbage(raw):
    assert normalise_confidence(raw) is None


# ────────────────────────────────────────────────────────────────────────────────
# bucket_for  —  round(confidence / 10) * 10
# ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "conf,want",
    [
        (0, 0),
        (4.9, 0),
        (5, 0),    # banker's rounding → 0 (round half to even)
        (5.1, 10),
        (14, 10),
        (15, 20),  # banker's → 20
        (55, 60),
        (64, 60),
        (65, 60),  # banker's → 60
        (95, 100),
        (100, 100),
    ],
)
def test_bucket_for_decile_rounding(conf, want):
    assert bucket_for(conf) == want


# ────────────────────────────────────────────────────────────────────────────────
# compute_reliability
# ────────────────────────────────────────────────────────────────────────────────

def test_empty_input_returns_neutral_snapshot():
    out = compute_reliability([])
    assert out["total_verified"] == 0
    assert out["overall_accuracy"] is None
    assert out["ece"] is None
    assert out["well_calibrated"] is False
    # Eleven buckets, all zeroed
    assert len(out["buckets"]) == 11
    assert all(b["total"] == 0 for b in out["buckets"])


def test_perfectly_calibrated_has_zero_ece():
    """70% confidence bucket with 70% hit-rate ⇒ gap = 0 ⇒ ECE = 0."""
    rows = [_row(70, True)] * 7 + [_row(70, False)] * 3
    out = compute_reliability(rows)
    assert out["total_verified"] == 10
    assert out["overall_accuracy"] == pytest.approx(0.7)
    assert out["ece"] == pytest.approx(0.0)
    assert out["well_calibrated"] is True
    # The 70-bucket has 10 rows, 7 correct
    b70 = next(b for b in out["buckets"] if b["bucket"] == 70)
    assert b70["total"] == 10
    assert b70["correct"] == 7
    assert b70["accuracy"] == pytest.approx(0.7)
    assert b70["gap"] == pytest.approx(0.0)


def test_miscalibrated_bucket_produces_nonzero_ece():
    """90% confidence but only 30% hit-rate — a classic overconfident model."""
    rows = [_row(90, True)] * 3 + [_row(90, False)] * 7
    out = compute_reliability(rows)
    # Gap = 0.3 - 0.9 = -0.6 ⇒ |gap| = 0.6, all weight in one bucket
    assert out["ece"] == pytest.approx(0.6)
    assert out["well_calibrated"] is False
    b90 = next(b for b in out["buckets"] if b["bucket"] == 90)
    assert b90["accuracy"] == pytest.approx(0.3)
    assert b90["gap"] == pytest.approx(-0.6)


def test_neutral_outcomes_excluded():
    rows = [
        _row(50, True),
        _row(50, False),
        _row(50, None),    # NEUTRAL — excluded
        {"confidence": 50},  # no grade — excluded
    ]
    out = compute_reliability(rows)
    assert out["total_verified"] == 2
    assert out["overall_accuracy"] == pytest.approx(0.5)


def test_unparseable_confidence_excluded():
    rows = [
        _row(50, True),
        _row("bad", True),
        _row(None, True),
    ]
    out = compute_reliability(rows)
    assert out["total_verified"] == 1


def test_fractional_confidence_lands_in_correct_bucket():
    """A 0.72 fraction should normalise to 72 and round-bucket to 70."""
    rows = [_row(0.72, True)] * 5 + [_row(0.72, False)] * 5
    out = compute_reliability(rows)
    # All rows in bucket 70
    b70 = next(b for b in out["buckets"] if b["bucket"] == 70)
    assert b70["total"] == 10
    assert b70["correct"] == 5


def test_weighted_ece_aggregates_across_buckets():
    """Two buckets, one calibrated, one miscalibrated — ECE is
    total-weighted absolute gap, NOT a simple mean."""
    rows = (
        [_row(70, True)] * 7 + [_row(70, False)] * 3       # 10 rows, gap 0
        + [_row(90, True)] * 1 + [_row(90, False)] * 9     # 10 rows, gap -0.8
    )
    out = compute_reliability(rows)
    # ECE = (10/20)*0 + (10/20)*0.8 = 0.4
    assert out["ece"] == pytest.approx(0.4)
    assert out["well_calibrated"] is False


def test_buckets_sorted_ascending_with_all_eleven_slots():
    rows = [_row(50, True)]
    out = compute_reliability(rows)
    labels = [b["bucket"] for b in out["buckets"]]
    assert labels == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
