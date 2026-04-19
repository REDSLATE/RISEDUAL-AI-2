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
        return pd.DataFrame(), pd.Series(dtype=int), 0

    monkeypatch.setattr("services.ml_retrain_service._load_training_dataframe", fake_load)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value.insert_one = AsyncMock()

    result = await run_nightly_retrain(fake_db)

    assert result["status"] == "skipped"
    assert "insufficient_samples" in result["reason"]
    fake_db.__getitem__.return_value.insert_one.assert_awaited_once()
