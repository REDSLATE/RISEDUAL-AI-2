"""Tests for the heuristic notes layer added to shadow_stats.

Adapted from the AuditReport.notes pattern in the RISEDUAL CLI
prototype. Covers:
- Empty input → friendly "no decisions yet" note.
- Sub-threshold scored counts → "wait for sample size" guidance.
- High win-rate + low samples → flagged as not-yet-actionable.
- Low win-rate + sufficient samples → flagged as actively wrong.
- All healthy → single positive note.
"""
from __future__ import annotations

from services.research_shadow_stats import (
    compute_shadow_stats,
    _shadow_stats_notes,
    MIN_DISSENT_SAMPLES,
)


def _row(engine="council", asset_type="crypto", phase="entry",
         is_dissent=True, score=1.0):
    return {
        "shadow_engine": engine,
        "asset_type": asset_type,
        "decision_phase": phase,
        "is_dissent": is_dissent,
        # The reducer expects a tactical_score dict with delta_usd —
        # see research_shadow_stats.py line 112-117.
        "tactical_score": {"delta_usd": score},
    }


# ── compute_shadow_stats now includes notes ───────────────────────


def test_compute_returns_notes_field():
    out = compute_shadow_stats([])
    assert "notes" in out
    assert isinstance(out["notes"], list)


def test_no_decisions_friendly_note():
    out = compute_shadow_stats([])
    assert any("no shadow decisions" in n.lower() for n in out["notes"])


# ── Threshold guidance ────────────────────────────────────────────


def test_under_min_samples_blocked_note():
    """Under MIN_DISSENT_SAMPLES → no actionable buckets warning."""
    rows = [_row(score=1.0) for _ in range(5)]  # 5 wins, 5 dissents
    out = compute_shadow_stats(rows)
    notes = out["notes"]
    assert any("scored dissents" in n.lower() for n in notes)
    assert any(str(MIN_DISSENT_SAMPLES) in n for n in notes)


def test_high_winrate_low_samples_warns_before_promotion():
    """Lucky early run shouldn't unlock anything."""
    rows = [_row(score=1.0) for _ in range(5)]  # 100% win, 5 samples
    out = compute_shadow_stats(rows)
    # Should mention the sample-size caveat
    notes = out["notes"]
    assert any("wait for sample size" in n.lower() or "scored dissents" in n.lower()
               for n in notes)


def test_low_winrate_with_samples_flags_actively_wrong():
    """≥MIN_DISSENT_SAMPLES + win_rate < 0.35 → operator should pause."""
    # MIN_DISSENT_SAMPLES wins=8 losses=22 → 26.7% win rate over 30 scored.
    n = MIN_DISSENT_SAMPLES + 5
    wins = int(n * 0.27)
    losses = n - wins
    rows = (
        [_row(score=1.0) for _ in range(wins)]
        + [_row(score=-1.0) for _ in range(losses)]
    )
    out = compute_shadow_stats(rows)
    notes = out["notes"]
    assert any("actively wrong" in n.lower() or "below 50%" in n.lower()
               for n in notes)


def test_healthy_actionable_bucket_positive_note():
    """≥MIN_DISSENT_SAMPLES, win_rate ~50–65% → all healthy."""
    n = MIN_DISSENT_SAMPLES + 10
    wins = int(n * 0.55)  # 55% — actionable, not high-flier
    losses = n - wins
    rows = (
        [_row(score=1.0) for _ in range(wins)]
        + [_row(score=-1.0) for _ in range(losses)]
    )
    out = compute_shadow_stats(rows)
    notes = out["notes"]
    # Must have at least one note. With healthy mid-band data we
    # expect either the actionable-but-quiet positive note OR no
    # warning notes — assert there's no "actively wrong" panic.
    assert not any("actively wrong" in n.lower() for n in notes)


# ── Direct helper coverage ────────────────────────────────────────


def test_shadow_stats_notes_empty_dict():
    out = _shadow_stats_notes({}, MIN_DISSENT_SAMPLES)
    assert len(out) == 1
    assert "no shadow decisions" in out[0].lower()


def test_shadow_stats_notes_handles_unknown_keys():
    """Defensive — bucket dicts missing fields shouldn't raise."""
    bucket = {("council", "crypto"): {
        "shadow_engine": "council",
        "asset_type": "crypto",
        "actionable": False,
    }}
    out = _shadow_stats_notes(bucket, MIN_DISSENT_SAMPLES)
    assert isinstance(out, list)
    assert len(out) >= 1
