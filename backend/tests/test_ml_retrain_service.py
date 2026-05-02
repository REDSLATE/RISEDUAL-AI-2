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



# ── Cost-trend helper ────────────────────────────────────────────
#
# Backs /api/admin/ml-retrain-cost-trend. Tests cover:
#  * aggregates + derived samples_per_fit_second
#  * legacy rows (pre-telemetry) excluded cleanly
#  * thread_binding_health verdict for all three risk bands


def _fake_db_with_rows(rows: list[dict]):
    """Build a mock Motor-style DB whose collection returns `rows` via the
    chained find/sort/limit/to_list pipeline the helper uses."""
    from unittest.mock import AsyncMock, MagicMock

    async def _to_list(length):
        # Mirror server-side "newest first" sort that the helper will
        # then reverse for chart plotting.
        return sorted(
            rows, key=lambda r: r.get("started_at", ""), reverse=True,
        )[:length]

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(side_effect=_to_list)

    collection = MagicMock()
    collection.find.return_value = cursor

    db = MagicMock()
    db.__getitem__.return_value = collection
    return db


def _row(started, version, samples, fit_wall, fit_cpu, status="success"):
    threads_equiv = round(fit_cpu / max(fit_wall, 1e-3), 2) if fit_wall else None
    return {
        "started_at": started,
        "status": status,
        "model_version": version,
        "samples": samples,
        "total_wall_seconds": fit_wall + 5.0 if fit_wall else 5.0,
        "fit_wall_seconds": fit_wall,
        "fit_cpu_seconds": fit_cpu,
        "fit_cpu_threads_equiv": threads_equiv,
    }


@pytest.mark.asyncio
async def test_cost_trend_aggregates_and_throughput():
    """Happy path: 4 rows, all with timing, should produce populated
    aggregates and a derived samples_per_fit_second on each row."""
    from services.ml_retrain_service import get_retrain_cost_trend

    rows = [
        _row("2026-05-01T02:30:00+00:00", "0.1.10", 10_000, 100.0, 300.0),
        _row("2026-05-02T02:30:00+00:00", "0.1.11", 12_000, 120.0, 360.0),
        _row("2026-05-03T02:30:00+00:00", "0.1.12", 11_000, 110.0, 330.0),
        _row("2026-05-04T02:30:00+00:00", "0.1.13",  9_000,  90.0, 270.0),
    ]
    db = _fake_db_with_rows(rows)

    out = await get_retrain_cost_trend(db, limit=30)

    assert out["count"] == 4
    # Chronological order — oldest first
    assert out["runs"][0]["model_version"] == "0.1.10"
    assert out["runs"][-1]["model_version"] == "0.1.13"

    # Derived throughput on each row: samples / fit_wall_seconds
    assert out["runs"][0]["samples_per_fit_second"] == 100.0      # 10_000 / 100
    assert out["runs"][1]["samples_per_fit_second"] == 100.0      # 12_000 / 120

    agg = out["aggregates"]
    assert agg["runs_with_timing"] == 4
    assert agg["runs_without_timing"] == 0
    assert agg["mean_fit_wall_seconds"] == 105.0                  # (100+120+110+90)/4
    assert agg["mean_threads_equiv"] == 3.0                       # cpu/wall = 3 on every row
    assert agg["mean_samples_per_fit_second"] == 100.0

    # Last 3 rows have threads_equiv=3.0 → ok band
    assert out["thread_binding_health"]["status"] == "ok"


@pytest.mark.asyncio
async def test_cost_trend_excludes_legacy_pretelemetry_rows():
    """Rows inserted before the timing improvement (no fit_wall_seconds)
    should be counted but not plotted — otherwise a newly-upgraded
    instance would show a chart with half the points at 0."""
    from services.ml_retrain_service import get_retrain_cost_trend

    rows = [
        # Legacy — pre-telemetry row, missing fit_wall_seconds
        {
            "started_at": "2026-04-01T02:30:00+00:00",
            "status": "success",
            "model_version": "0.1.1",
            "samples": 8_000,
        },
        # Modern — has timing
        _row("2026-05-04T02:30:00+00:00", "0.1.13", 9_000, 90.0, 270.0),
    ]
    db = _fake_db_with_rows(rows)

    out = await get_retrain_cost_trend(db, limit=30)

    assert out["count"] == 1
    assert out["runs"][0]["model_version"] == "0.1.13"
    assert out["aggregates"]["runs_with_timing"] == 1
    assert out["aggregates"]["runs_without_timing"] == 1


@pytest.mark.asyncio
async def test_cost_trend_health_degraded_when_cap_appears_unbound():
    """If the last 3 runs show threads_equiv near 1.0, the verdict must be
    `degraded` — this is the regression canary for n_jobs / env overrides
    being accidentally stripped from a future redeploy."""
    from services.ml_retrain_service import get_retrain_cost_trend

    # All three recent runs: cpu ≈ wall → threads_equiv ≈ 1.0
    rows = [
        _row("2026-05-01T02:30:00+00:00", "0.1.10", 10_000, 100.0, 100.0),
        _row("2026-05-02T02:30:00+00:00", "0.1.11", 12_000, 120.0, 120.0),
        _row("2026-05-03T02:30:00+00:00", "0.1.12", 11_000, 110.0, 110.0),
    ]
    db = _fake_db_with_rows(rows)

    out = await get_retrain_cost_trend(db, limit=30)
    assert out["thread_binding_health"]["status"] == "degraded"
    assert out["thread_binding_health"]["window_mean"] == 1.0


@pytest.mark.asyncio
async def test_cost_trend_health_unknown_with_few_runs():
    """Less than 3 timed runs — verdict must be `unknown`, not a false
    positive from a short window."""
    from services.ml_retrain_service import get_retrain_cost_trend

    rows = [
        _row("2026-05-01T02:30:00+00:00", "0.1.10", 10_000, 100.0, 300.0),
        _row("2026-05-02T02:30:00+00:00", "0.1.11", 12_000, 120.0, 360.0),
    ]
    db = _fake_db_with_rows(rows)

    out = await get_retrain_cost_trend(db, limit=30)
    assert out["thread_binding_health"]["status"] == "unknown"
    assert out["thread_binding_health"]["window_mean"] is None


@pytest.mark.asyncio
async def test_cost_trend_empty_history():
    """No successful runs at all — must return empty structure, not crash."""
    from services.ml_retrain_service import get_retrain_cost_trend

    db = _fake_db_with_rows([])
    out = await get_retrain_cost_trend(db, limit=30)

    assert out["count"] == 0
    assert out["runs"] == []
    assert out["aggregates"] == {"runs_with_timing": 0, "runs_without_timing": 0}
    assert out["thread_binding_health"]["status"] == "unknown"
