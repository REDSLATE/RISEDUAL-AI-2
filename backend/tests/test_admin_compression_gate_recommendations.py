"""Tests for ``routes.admin_compression_gate`` — recommended-action
helper + ``compression_gate_runs`` history persistence.

The route handler itself is exercised end-to-end by existing
integration tests; here we focus on the new logic added in this
patch:

1. ``_recommended_action`` returns sensible strings for each verdict.
2. The action helper does NOT touch the underlying script — it's a
   pure function over ``GateVerdict`` shape.
3. Mongo collection name is exposed as a stable constant.
"""

from __future__ import annotations

from routes.admin_compression_gate import (
    COMPRESSION_GATE_RUNS,
    _recommended_action,
)


# ─── recommended-action helper ──────────────────────────────────


def test_pass_recommends_promotion_with_gap_delta_when_available():
    msg = _recommended_action(
        verdict_label="PASS",
        breaches=[],
        baseline_summary={"weighted_avg_calibration_gap": 0.030},
        candidate_summary={"weighted_avg_calibration_gap": 0.020},
    )
    assert "Promote candidate" in msg
    assert "0.0300" in msg
    assert "0.0200" in msg
    assert "improved" in msg


def test_pass_handles_held_flat_or_worsened_within_tolerance():
    msg = _recommended_action(
        verdict_label="PASS",
        breaches=[],
        baseline_summary={"weighted_avg_calibration_gap": 0.020},
        candidate_summary={"weighted_avg_calibration_gap": 0.025},
    )
    assert "held flat" in msg


def test_pass_falls_back_when_gap_missing():
    msg = _recommended_action(
        verdict_label="PASS",
        breaches=[],
        baseline_summary={},
        candidate_summary={},
    )
    assert msg == "Promote candidate after manual sanity check."


def test_inconclusive_surfaces_first_reason():
    msg = _recommended_action(
        verdict_label="INCONCLUSIVE",
        breaches=["candidate has only 12 resolved predictions (<30)"],
        baseline_summary={},
        candidate_summary={},
    )
    assert "Hold the verdict" in msg
    assert "12 resolved" in msg


def test_inconclusive_with_no_reasons_falls_back():
    msg = _recommended_action(
        verdict_label="INCONCLUSIVE",
        breaches=[],
        baseline_summary={},
        candidate_summary={},
    )
    assert "insufficient samples" in msg


def test_fail_surfaces_worst_breach():
    breaches = [
        "calibration_gap[VOL_SPIKE] worsened +0.012 -> +0.060 (delta=+0.048)",
        "weighted_avg_calibration_gap worsened 0.0200 -> 0.0500 (delta=+0.0300)",
    ]
    msg = _recommended_action(
        verdict_label="FAIL",
        breaches=breaches,
        baseline_summary={},
        candidate_summary={},
    )
    assert "Hold candidate" in msg
    assert "do NOT promote" in msg
    assert "VOL_SPIKE" in msg


def test_fail_with_no_breach_text_does_not_crash():
    """Defensive — empty breach list with FAIL shouldn't return an
    empty recommendation. Guards against a future verdict-shape
    change accidentally muting the panel."""
    msg = _recommended_action(
        verdict_label="FAIL",
        breaches=[],
        baseline_summary={},
        candidate_summary={},
    )
    assert "Hold candidate" in msg


# ─── persistence wiring ─────────────────────────────────────────


def test_history_collection_name_is_stable():
    """The collection name is part of the public contract — admin
    tooling, manual queries, and any future migration script will
    reference this exact string."""
    assert COMPRESSION_GATE_RUNS == "compression_gate_runs"
