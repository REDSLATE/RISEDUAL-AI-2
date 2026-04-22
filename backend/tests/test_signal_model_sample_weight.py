"""Integration tests for `SignalModel.fit(sample_weight=...)`.

Proves that passing per-row weights changes the trained model's
decision boundary in the expected direction. This is the feedback
loop from the ML retrain service's severity-weighting → XGBoost
gradient → calibrated probability output.

Kept as a pytest-only integration test (not unit): the test
trains a full XGBoost + CalibratedClassifierCV pipeline on ~80
synthetic samples, which takes ~1.5s. That's worth it because
the downstream value of severity weighting is "the model learns
the right thing", and unit-level mocks can't verify that.

Skipped automatically if xgboost/sklearn aren't installed — the
same graceful degradation SignalModel itself implements.
"""
from __future__ import annotations

import pytest

xgb = pytest.importorskip("xgboost")
sklearn = pytest.importorskip("sklearn")
pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from risedual_core.ml.signal_model import SignalModel, SignalModelConfig


def _synthetic_dataset(n: int = 80, seed: int = 42):
    """Two-feature synthetic set: `feat_a` perfectly predicts y,
    `feat_b` is noise. This means with uniform weights the model
    should learn feat_a → strong importance.

    When we upweight rows where feat_b matches y, the model should
    shift some importance toward feat_b. If sample_weight doesn't
    propagate, importance stays identical.
    """
    rng = np.random.default_rng(seed)
    feat_a = rng.normal(0, 1, n)
    feat_b = rng.normal(0, 1, n)
    # y is a noisy function of feat_a only.
    y = (feat_a + rng.normal(0, 0.1, n) > 0).astype(int)
    X = pd.DataFrame({"feat_a": feat_a, "feat_b": feat_b})
    return X, pd.Series(y, name="y")


@pytest.fixture
def feature_cols_patch(monkeypatch):
    """SignalModelConfig expects `feature_columns` to match the
    FEATURE_COLUMNS constant by default. Override to our 2 synth
    columns so we don't drag in 30+ real feature names."""
    cfg_fields = {"feature_columns": ["feat_a", "feat_b"]}
    return cfg_fields


def test_fit_accepts_sample_weight_without_crashing(feature_cols_patch):
    X, y = _synthetic_dataset()
    weights = pd.Series(np.ones(len(X)), index=X.index)
    cfg = SignalModelConfig(**feature_cols_patch)
    model = SignalModel(config=cfg)
    model.fit(X, y, sample_weight=weights)
    proba = model.predict_proba(X)
    # Sanity: probabilities in [0,1].
    assert (proba >= 0).all() and (proba <= 1).all()


def test_fit_sample_weight_none_matches_legacy_path(feature_cols_patch):
    """`fit(X, y)` (no sample_weight) must behave exactly like the
    old call signature — uniform training. Verified by checking
    the feature-importance ranking still prefers `feat_a`.
    """
    X, y = _synthetic_dataset()
    cfg = SignalModelConfig(**feature_cols_patch)
    model = SignalModel(config=cfg)
    model.fit(X, y)  # no sample_weight
    imps = model._feature_importances
    # feat_a is the real predictor; its importance should dominate.
    assert imps.get("feat_a", 0) > imps.get("feat_b", 0)


def test_sample_weight_shifts_feature_importance(feature_cols_patch):
    """Core correctness test: upweighting rows where `feat_b` aligns
    with y should shift some model importance toward feat_b. If
    sample_weight didn't propagate through CalibratedClassifierCV
    to the XGBoost base, both importance vectors would be identical.
    """
    X, y = _synthetic_dataset()

    # Upweight rows where feat_b points the same direction as y.
    # This is a contrived "feat_b is relevant for high-conviction
    # rows" scenario — exactly the asymmetry the severity weighting
    # creates in real training (high-magnitude outcomes count more).
    aligned = ((X["feat_b"] > 0) == (y == 1))
    weights_skewed = pd.Series(np.where(aligned, 3.0, 0.3), index=X.index)
    weights_uniform = pd.Series(np.ones(len(X)), index=X.index)

    cfg = SignalModelConfig(**feature_cols_patch)

    model_uniform = SignalModel(config=cfg)
    model_uniform.fit(X, y, sample_weight=weights_uniform)
    imp_uniform = model_uniform._feature_importances.get("feat_b", 0)

    model_skewed = SignalModel(config=cfg)
    model_skewed.fit(X, y, sample_weight=weights_skewed)
    imp_skewed = model_skewed._feature_importances.get("feat_b", 0)

    # With skewed weights, feat_b should matter MORE to the model.
    # If sample_weight didn't propagate, both would be equal.
    assert imp_skewed > imp_uniform, (
        f"expected feat_b importance to increase with aligned weights, "
        f"got uniform={imp_uniform:.4f}, skewed={imp_skewed:.4f} — "
        "sample_weight may not be propagating through CalibratedClassifierCV"
    )


def test_fit_clips_extreme_sample_weights(feature_cols_patch):
    """A pathological weight >10 should be clipped rather than let
    a single row dominate the gradient. This is the belt-and-braces
    guard in `SignalModel.fit` — we don't test the exact numeric
    cap, just that the fit doesn't explode on a 1000× weight.
    """
    X, y = _synthetic_dataset()
    # One row with weight 1000, all others at 1.
    weights = pd.Series(np.ones(len(X)), index=X.index)
    weights.iloc[0] = 1000.0
    cfg = SignalModelConfig(**feature_cols_patch)
    model = SignalModel(config=cfg)
    model.fit(X, y, sample_weight=weights)
    # Didn't crash; feature importances still reasonable.
    imps = model._feature_importances
    assert imps.get("feat_a", 0) > 0
