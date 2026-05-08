"""Tests for R-weighting wiring in `services.ml_retrain_service`.

Exercises:
  * `_r_eligible_mask_and_weights` (pure DataFrame logic)
  * `_severity_weights` blending — R-weight wins for schema_v4 rows
    with full execution data; magnitude weight stays for legacy rows

No MongoDB — we construct DataFrames directly and assert on the
returned Series values. Locks the drift-logging invariant that an
empty features_snapshots row set never crashes and that the R path
caps at the same 2.5 max as the magnitude path so downstream clips
stay untriggered.
"""
from __future__ import annotations

import pandas as pd
import pytest

from services.ml_retrain_service import (
    _WEIGHT_CAP,
    _r_eligible_mask_and_weights,
    _severity_weights,
)


# ── _r_eligible_mask_and_weights ─────────────────────────────────


def test_missing_columns_returns_all_false_mask():
    """Legacy DataFrame (no execution columns) → mask all False,
    weights all NaN. Magnitude fallback will fill them in."""
    df = pd.DataFrame({"return_1d": [0.02, -0.03]})
    mask, weights = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 0
    assert weights.isna().all()


def test_schema_version_below_four_not_eligible():
    """schema_version < 4 → ineligible even if the 4 execution
    fields are populated. Guards against accidentally pulling in
    partial-data rows from an older schema."""
    df = pd.DataFrame([
        {"schema_version": 2, "entry_price": 100.0, "exit_price": 110.0,
         "stop_loss": 95.0, "direction": "LONG"},
        {"schema_version": 3, "entry_price": 100.0, "exit_price": 110.0,
         "stop_loss": 95.0, "direction": "LONG"},
    ])
    mask, _ = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 0


def test_invalid_direction_not_eligible():
    """'BUY' / 'sideways' / case mismatches aren't LONG or SHORT —
    ineligible. The snapshot_enricher should have normalised these
    already, but the retrain path must be resilient to a dirty row."""
    df = pd.DataFrame([
        {"schema_version": 4, "entry_price": 100.0, "exit_price": 110.0,
         "stop_loss": 95.0, "direction": "BUY"},
        {"schema_version": 4, "entry_price": 100.0, "exit_price": 110.0,
         "stop_loss": 95.0, "direction": "long"},  # lowercase
    ])
    mask, _ = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 0


def test_eligible_long_winner_gets_r_weight():
    """schema=4, LONG +2R winner → weight ~2.0 (cap, no loss penalty)."""
    df = pd.DataFrame([{
        "schema_version": 4, "entry_price": 100.0, "exit_price": 110.0,
        "stop_loss": 95.0, "direction": "LONG",
    }])
    mask, weights = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 1
    assert weights.iloc[0] == pytest.approx(2.0)


def test_eligible_short_loser_gets_amplified_weight():
    """schema=4, SHORT -1R loser → weight 1.0 × 1.25 = 1.25."""
    df = pd.DataFrame([{
        "schema_version": 4, "entry_price": 100.0, "exit_price": 105.0,
        "stop_loss": 95.0, "direction": "SHORT",
    }])
    mask, weights = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 1
    assert weights.iloc[0] == pytest.approx(1.25)


def test_noise_floor_row_gets_zero_weight():
    """|R| = 0.1 (below 0.25 noise floor) → weight 0.0 (effectively
    dropped from XGBoost gradient)."""
    df = pd.DataFrame([{
        "schema_version": 4, "entry_price": 100.0, "exit_price": 100.5,
        "stop_loss": 95.0, "direction": "LONG",  # +0.1R
    }])
    mask, weights = _r_eligible_mask_and_weights(df)
    assert mask.sum() == 1
    assert weights.iloc[0] == 0.0


def test_r_weight_never_exceeds_magnitude_path_cap():
    """R cap must equal the magnitude-path cap (2.0 × 1.25 = 2.5)
    so SignalModel.fit's 10× anti-explosion clip stays untriggered
    regardless of which pipeline fed the row."""
    # 10R catastrophic loser with amplifier.
    df = pd.DataFrame([{
        "schema_version": 4, "entry_price": 100.0, "exit_price": 0.0,
        "stop_loss": 90.0, "direction": "LONG",
    }])
    _, weights = _r_eligible_mask_and_weights(df)
    assert weights.iloc[0] <= _WEIGHT_CAP * 1.25 + 1e-9
    assert weights.iloc[0] == pytest.approx(2.5)


# ── _severity_weights blending ───────────────────────────────────


def test_legacy_row_uses_magnitude_path():
    """No execution columns → magnitude severity weight. A +2%
    mover sits mid-ramp at ~1.5."""
    df = pd.DataFrame([{"return_1d": 0.02}])
    w = _severity_weights(df)
    assert w.iloc[0] == pytest.approx(1.5)


def test_schema_v4_row_overrides_magnitude_with_r():
    """Row has BOTH return_1d and the execution block → R-weight
    wins, magnitude is discarded. A +2R LONG winner with only a
    +0.2% return_1d still gets weight 2.0 (R tier), NOT 0.5
    (magnitude noise tier)."""
    df = pd.DataFrame([{
        "return_1d": 0.002,  # magnitude-noise tier
        "schema_version": 4, "entry_price": 100.0, "exit_price": 110.0,
        "stop_loss": 95.0, "direction": "LONG",  # +2R via stop distance
    }])
    w = _severity_weights(df)
    assert w.iloc[0] == pytest.approx(2.0)


def test_mixed_batch_legacy_and_r_weighted():
    """Batch with one legacy row + one R-eligible row — each gets
    its own weighting path. Locks the blend invariant that
    magnitude-only rows aren't disturbed by the R pass."""
    df = pd.DataFrame([
        # Legacy: magnitude only. +5% strong mover, winner → 2.0 cap.
        {"return_1d": 0.05,
         "schema_version": 2, "entry_price": None, "exit_price": None,
         "stop_loss": None, "direction": None},
        # R-eligible: -2R loser → 2.5.
        {"return_1d": -0.04,
         "schema_version": 4, "entry_price": 100.0, "exit_price": 90.0,
         "stop_loss": 105.0, "direction": "LONG"},
    ])
    w = _severity_weights(df)
    assert w.iloc[0] == pytest.approx(2.0)  # magnitude cap, no amp (winner)
    assert w.iloc[1] == pytest.approx(2.5)  # R cap × loss amp


def test_blend_preserves_length_and_index():
    """Drop-from-gradient via zero weight keeps the Series in
    lockstep with X/y — no index shifts that would desync training."""
    df = pd.DataFrame([
        {"return_1d": 0.05,
         "schema_version": 2, "entry_price": None, "exit_price": None,
         "stop_loss": None, "direction": None},
        # R-eligible noise-floor row (|R|=0.1) → 0 weight.
        {"return_1d": 0.001,
         "schema_version": 4, "entry_price": 100.0, "exit_price": 100.5,
         "stop_loss": 95.0, "direction": "LONG"},
        {"return_1d": -0.02,
         "schema_version": 2, "entry_price": None, "exit_price": None,
         "stop_loss": None, "direction": None},
    ])
    w = _severity_weights(df)
    assert len(w) == 3
    assert list(w.index) == [0, 1, 2]
    assert w.iloc[1] == 0.0  # R-skipped
    assert w.iloc[0] > 0     # magnitude path unaffected


def test_empty_df_returns_empty_series():
    """Cold-start / no data → don't crash, return empty Series."""
    df = pd.DataFrame()
    w = _severity_weights(df)
    assert len(w) == 0
