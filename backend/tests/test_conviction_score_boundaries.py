"""Regression tests for the boundary clamp on `score_prediction_outcome`.

Locks in the `MAX_PENALTY = -2.5`, `MIN_REWARD = +2.5` contract so any
future change to `GRADE_WEIGHTS` or the confidence-scaling rules can't
silently produce a runaway learning signal.
"""
from __future__ import annotations

import pytest

from services import conviction_service as cs
from services.conviction_service import (
    MAX_PENALTY,
    MIN_REWARD,
    score_prediction_outcome,
)


def test_boundary_constants_are_symmetric_soft_caps():
    """Bounds must sit just outside the natural [-2, +2] range so
    current grades pass through unchanged."""
    assert MIN_REWARD == pytest.approx(2.5)
    assert MAX_PENALTY == pytest.approx(-2.5)
    # Symmetric — otherwise calibration mean gets biased.
    assert MIN_REWARD == -MAX_PENALTY


@pytest.mark.parametrize(
    "grade,conf,want",
    [
        ("STRONG_HIT",  100, +2.0),
        ("STRONG_HIT",   50, +1.0),
        ("WEAK_HIT",    100, +1.0),
        ("NEUTRAL",     100,  0.0),
        ("WEAK_MISS",   100, -1.0),
        ("STRONG_MISS", 100, -2.0),
        ("STRONG_MISS",  50, -1.0),
        ("UNKNOWN_TAG", 100,  0.0),
    ],
)
def test_natural_range_passes_through_unchanged(grade, conf, want):
    assert score_prediction_outcome(grade, conf) == pytest.approx(want)


@pytest.mark.parametrize("conf,want", [(-50, 0.0), (0, 0.0), (250, +2.0)])
def test_confidence_is_clamped_to_zero_one(conf, want):
    assert score_prediction_outcome("STRONG_HIT", conf) == pytest.approx(want)


def test_runaway_weight_is_clamped_to_min_reward(monkeypatch):
    """Simulate a future weight-table bug: a +5.0 weight must not
    produce a score above MIN_REWARD."""
    monkeypatch.setitem(cs.GRADE_WEIGHTS, "RUNAWAY_WIN", +5.0)
    assert score_prediction_outcome("RUNAWAY_WIN", 100) == pytest.approx(MIN_REWARD)


def test_runaway_weight_is_clamped_to_max_penalty(monkeypatch):
    monkeypatch.setitem(cs.GRADE_WEIGHTS, "RUNAWAY_LOSS", -5.0)
    assert score_prediction_outcome("RUNAWAY_LOSS", 100) == pytest.approx(MAX_PENALTY)


def test_output_is_always_within_bounds():
    """Fuzz across the full grade table + a wide confidence sweep."""
    for grade in cs.GRADE_WEIGHTS:
        for conf in (-100, -1, 0, 1, 33, 50, 66, 99, 100, 101, 500):
            got = score_prediction_outcome(grade, conf)
            assert MAX_PENALTY <= got <= MIN_REWARD, (grade, conf, got)
