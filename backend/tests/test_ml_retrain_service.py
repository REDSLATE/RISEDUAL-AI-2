"""Regression tests for `services.ml_retrain_service`."""
from pathlib import Path

import pytest

from services.ml_retrain_service import (
    MAX_SAMPLES,
    MIN_SAMPLES_FOR_TRAINING,
    MODELS_DIR,
    _next_version_number,
    get_latest_model_info,
)


def test_next_version_is_monotonically_increasing(tmp_path, monkeypatch):
    """New version number must be max(existing)+1, scan the models dir."""
    fake_models = tmp_path / "models"
    fake_models.mkdir()
    (fake_models / "signal_model_v7.joblib").write_bytes(b"x")
    (fake_models / "signal_model_v11.joblib").write_bytes(b"x")
    (fake_models / "unrelated.joblib").write_bytes(b"x")

    monkeypatch.setattr("services.ml_retrain_service.MODELS_DIR", fake_models)
    assert _next_version_number() == 12


def test_next_version_with_empty_dir(tmp_path, monkeypatch):
    """First-ever run must start at 1."""
    empty = tmp_path / "models_empty"
    empty.mkdir()
    monkeypatch.setattr("services.ml_retrain_service.MODELS_DIR", empty)
    assert _next_version_number() == 1


def test_get_latest_model_info_returns_none_when_no_artefacts(tmp_path, monkeypatch):
    empty = tmp_path / "models_empty"
    empty.mkdir()
    monkeypatch.setattr("services.ml_retrain_service.MODELS_DIR", empty)
    assert get_latest_model_info() is None


def test_constants_are_sane():
    """Sanity on tuning constants — don't silently regress to pathological values."""
    assert MIN_SAMPLES_FOR_TRAINING >= 100
    assert MAX_SAMPLES >= MIN_SAMPLES_FOR_TRAINING
    assert isinstance(MODELS_DIR, Path)


@pytest.mark.asyncio
async def test_nightly_retrain_skips_on_insufficient_samples(tmp_path, monkeypatch):
    """If the DB has too few labeled rows, we must skip cleanly rather than
    blow up halfway through training."""
    from unittest.mock import AsyncMock, MagicMock
    from services.ml_retrain_service import run_nightly_retrain

    # Fake DB that returns an empty dataframe via the private loader.
    async def fake_load(db, max_samples):
        import pandas as pd
        empty_w = pd.Series(dtype=float)
        return (pd.DataFrame(), pd.Series(dtype=int), empty_w, empty_w, 0,
                {"r_eligible_frac": 0.0, "r_skipped_frac": 0.0},
                pd.DataFrame())

    monkeypatch.setattr("services.ml_retrain_service._load_training_dataframe", fake_load)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value.insert_one = AsyncMock()

    result = await run_nightly_retrain(fake_db)

    assert result["status"] == "skipped"
    assert "insufficient_samples" in result["reason"]
    fake_db.__getitem__.return_value.insert_one.assert_awaited_once()

    # The early-skip path must still stamp the end-to-end wall-time on the
    # log row so ops can trend retrain cost over time (including skipped
    # runs — a sudden jump means the data-load itself regressed).
    assert "total_wall_seconds" in result
    assert isinstance(result["total_wall_seconds"], (int, float))
    assert result["total_wall_seconds"] >= 0.0


# ── Severity-weighting tests ─────────────────────────────────────
#
# These pin the `_severity_weights` formula against the documented
# piecewise spec. Kept separate from the orchestration tests so a
# weight-formula tweak doesn't cascade unrelated test renames.


def test_severity_weights_maps_noise_to_low_weight():
    """Tiny moves → 0.5 (noise band). A positive 0.2% move stays
    at 0.5; a negative -0.5% move gets loss-amplified to 0.625."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.002, -0.005, 0.009]})  # all <1%
    w = _severity_weights(df)
    assert w.iloc[0] == 0.5   # +0.2% → noise, no amplifier
    assert w.iloc[1] == 0.625  # -0.5% → noise × 1.25 loss amplifier
    assert w.iloc[2] == 0.5   # +0.9% → noise, no amplifier


def test_severity_weights_ramps_through_weak_band():
    """Linear 1.0 → 2.0 ramp across [1%, 3%]. Winners unchanged;
    losers get 1.25× amplified on top."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.01, 0.02, 0.029, -0.015]})
    w = _severity_weights(df)
    # Row 0 (+1% exactly) → 1.0, no amplifier
    assert abs(w.iloc[0] - 1.0) < 0.01
    # Row 1 (+2%) → ~1.5 ramp midpoint, no amplifier
    assert abs(w.iloc[1] - 1.5) < 0.01
    # Row 2 (+2.9%) → ~1.95, no amplifier
    assert abs(w.iloc[2] - 1.95) < 0.01
    # Row 3 (-1.5%) → ramp 1.25 × loss amplifier 1.25 = 1.5625
    assert abs(w.iloc[3] - 1.5625) < 0.01


def test_severity_weights_caps_strong_moves():
    """A 5% win hits the 2.0 cap. A -5% loss hits 2.0 × 1.25 = 2.5.
    This is the core user-facing upgrade: losing big hurts the
    model MORE than winning big helps it."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.05, -0.05, 0.20, -0.30]})
    w = _severity_weights(df)
    assert w.iloc[0] == 2.0   # +5% → cap, no amplifier
    assert w.iloc[1] == 2.5   # -5% → cap × 1.25 loss amplifier
    assert w.iloc[2] == 2.0   # +20% → cap, no amplifier
    assert w.iloc[3] == 2.5   # -30% → cap × 1.25 loss amplifier


def test_severity_weights_fallback_on_missing_column():
    """No `return_1d` column → uniform weight 1.0 (legacy path)."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"outcome": ["up", "down", "flat"]})
    w = _severity_weights(df)
    assert len(w) == 3
    assert all(w == 1.0)


def test_severity_weights_handles_nan_returns():
    """NaN `return_1d` falls back to neutral 1.0. NaN is treated
    as non-negative (no loss amplifier) to avoid double-penalising
    the unknown-direction path."""
    import pandas as pd
    import numpy as np
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [np.nan, 0.05, np.nan]})
    w = _severity_weights(df)
    assert w.iloc[0] == 1.0   # NaN → neutral, no amplifier
    assert w.iloc[1] == 2.0   # +5% → cap, no amplifier
    assert w.iloc[2] == 1.0   # NaN → neutral, no amplifier


def test_severity_weighting_asymmetry_matches_conviction_scale():
    """The core claim: a -5% blown trade carries more training
    signal than a -0.5% stop-out. Now the ratio is 2.5 / 0.625 = 4×
    (same as before — both sides get the 1.25× loss amplifier and it
    cancels in the ratio) — proving the pipeline upgrade is
    orthogonal to the severity asymmetry.
    """
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [-0.005, -0.05]})
    w = _severity_weights(df)
    assert w.iloc[0] == 0.625  # -0.5% × 1.25 amplifier
    assert w.iloc[1] == 2.5    # -5% × 1.25 amplifier
    # Ratio preserved through the amplification.
    assert w.iloc[1] / w.iloc[0] == 4.0


def test_sign_aware_loss_amplification_ratio():
    """Same-magnitude win vs loss: the loss carries exactly 1.25×
    more training weight. This pins the sign-aware asymmetry that
    teaches the model 'avoiding losses > capturing gains'.
    """
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.05, -0.05]})  # identical |r|
    w = _severity_weights(df)
    assert w.iloc[1] / w.iloc[0] == 1.25


def test_loss_amplifier_matches_scalar_module():
    """The DataFrame-level _LOSS_AMPLIFIER must agree with the
    scalar `compute_signed_weight` in `ai_core.learning_upgrade`.
    If someone tunes one side and forgets the other, training-time
    weights and inference-time single-row scoring would silently
    disagree.
    """
    from services.ml_retrain_service import _LOSS_AMPLIFIER as df_amp
    from ai_core.learning_upgrade import _LOSS_AMPLIFIER as scalar_amp
    assert df_amp == scalar_amp
