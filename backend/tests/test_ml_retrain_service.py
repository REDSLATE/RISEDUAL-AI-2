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
        return pd.DataFrame(), pd.Series(dtype=int), pd.Series(dtype=float), 0

    monkeypatch.setattr("services.ml_retrain_service._load_training_dataframe", fake_load)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value.insert_one = AsyncMock()

    result = await run_nightly_retrain(fake_db)

    assert result["status"] == "skipped"
    assert "insufficient_samples" in result["reason"]
    fake_db.__getitem__.return_value.insert_one.assert_awaited_once()


# ── Severity-weighting tests ─────────────────────────────────────
#
# These pin the `_severity_weights` formula against the documented
# piecewise spec. Kept separate from the orchestration tests so a
# weight-formula tweak doesn't cascade unrelated test renames.


def test_severity_weights_maps_noise_to_low_weight():
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.002, -0.005, 0.009]})  # all <1%
    w = _severity_weights(df)
    assert all(w == 0.5), f"expected 0.5 for <1% moves, got {w.tolist()}"


def test_severity_weights_ramps_through_weak_band():
    """Linear 1.0 → 2.0 ramp across [1%, 3%]. A 2% mover should
    sit near 1.5; a 2.9% mover should be close to 2.0 but not at it."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.01, 0.02, 0.029, -0.015]})
    w = _severity_weights(df)
    # Row 0 (exactly 1%) → 1.0
    assert abs(w.iloc[0] - 1.0) < 0.01
    # Row 1 (2%) → ~1.5 (midpoint of ramp)
    assert abs(w.iloc[1] - 1.5) < 0.01
    # Row 2 (2.9%) → ~1.95
    assert abs(w.iloc[2] - 1.95) < 0.01
    # Row 3 (abs 1.5%) → ~1.25
    assert abs(w.iloc[3] - 1.25) < 0.01


def test_severity_weights_caps_strong_moves():
    """A 5% or 20% move both hit the cap. The user's -5% blown trade
    example should weight exactly 2.0."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [0.05, -0.05, 0.20, -0.30]})
    w = _severity_weights(df)
    assert all(w == 2.0), f"expected 2.0 (cap) for ≥3% moves, got {w.tolist()}"


def test_severity_weights_fallback_on_missing_column():
    """No `return_1d` column → uniform weight 1.0 (legacy path)."""
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"outcome": ["up", "down", "flat"]})
    w = _severity_weights(df)
    assert len(w) == 3
    assert all(w == 1.0)


def test_severity_weights_handles_nan_returns():
    """NaN `return_1d` falls back to neutral 1.0 (uniform weight).
    We intentionally DON'T penalise missing magnitude as noise —
    a NaN means "we don't know", not "the move was tiny". Uniform
    weight is the safe default: matches the legacy path's behavior
    for rows that have no return_1d at all."""
    import pandas as pd
    import numpy as np
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [np.nan, 0.05, np.nan]})
    w = _severity_weights(df)
    assert w.iloc[0] == 1.0  # NaN → neutral uniform
    assert w.iloc[1] == 2.0  # real 5% move → cap
    assert w.iloc[2] == 1.0


def test_severity_weighting_asymmetry_matches_conviction_scale():
    """The core user-facing claim: a -5% blown trade carries more
    training signal than a -0.5% stop-out. Pins the 4:1 ratio so
    future tweaks to the ramp can't silently flatten the asymmetry.
    """
    import pandas as pd
    from services.ml_retrain_service import _severity_weights

    df = pd.DataFrame({"return_1d": [-0.005, -0.05]})
    w = _severity_weights(df)
    # -0.5% stop-out → 0.5, -5% blown trade → 2.0.
    # Ratio is 4× (not 10× as one might naively expect — the cap
    # at ≥3% prevents a single outlier from dominating). This is
    # intentional: sklearn `sample_weight` is multiplicative on the
    # gradient, so 4× is plenty of skew without destabilising the
    # calibration curve.
    assert w.iloc[1] / w.iloc[0] == 4.0
