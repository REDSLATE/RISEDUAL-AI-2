"""XGBoost signal model interface for risedual_core.

This module provides a thin, dependency-isolating wrapper around an XGBoost
binary classifier so the rest of the codebase never imports ``xgboost`` or
``sklearn`` directly.  Swapping the underlying model implementation only
requires changes here.

The model predicts ``is_correct`` (binary: 1 = prediction will be correct),
then maps that probability to a :class:`~risedual_core.schemas.market.PredictionDirection`
with a Platt-calibrated confidence score.

Typical usage::

    model = SignalModel()
    model.fit(X_train, y_train)

    result = model.predict(snapshot)
    print(result.direction, result.confidence)

    model.save("signal_model_v1.joblib")
    model_copy = SignalModel.load("signal_model_v1.joblib")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from risedual_core.ml.features import (
    FEATURE_COLUMNS,
    encode_regime,
    impute_features,
    snapshot_to_vector,
    vector_to_dataframe,
)
from risedual_core.schemas.market import FeaturesSnapshot, PredictionDirection, SignalResult

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

logger = logging.getLogger(__name__)


# ── Dependency guard ──────────────────────────────────────────────────────────


def _require_ml_deps() -> tuple[Any, Any, Any]:
    """Import and return (xgboost, CalibratedClassifierCV, numpy).

    Raises a clear :exc:`ImportError` for each missing package so callers
    receive an actionable message rather than a bare ``ModuleNotFoundError``.

    Returns
    -------
    tuple
        ``(xgb, CalibratedClassifierCV, np)`` — the three ML dependency
        modules needed by :meth:`SignalModel.fit`.
    """
    try:
        import xgboost as xgb  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "xgboost is required for SignalModel.fit. "
            "Install it with: pip install xgboost"
        ) from exc

    try:
        from sklearn.calibration import CalibratedClassifierCV  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "scikit-learn is required for SignalModel.fit. "
            "Install it with: pip install scikit-learn"
        ) from exc

    try:
        import numpy as np  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "numpy is required for SignalModel.fit. "
            "Install it with: pip install numpy"
        ) from exc

    return xgb, CalibratedClassifierCV, np


# ── Calibration stats ─────────────────────────────────────────────────────────


class CalibrationStats(BaseModel):
    """Evaluation metrics produced by :meth:`SignalModel.evaluate`.

    Stored alongside the trained model in the joblib artefact and exposed
    via :attr:`SignalModel.calibration_stats` so the API and CLI can surface
    these numbers without re-computing them.
    """

    accuracy: float = Field(ge=0.0, le=1.0, description="Fraction of correct directional calls.")
    brier_score: float = Field(ge=0.0, le=1.0, description="Brier score (lower is better).")
    ece: float = Field(ge=0.0, le=1.0, description="Expected Calibration Error (lower is better).")
    n_predictions: int = Field(ge=0, description="Total labeled predictions used in evaluation.")
    model_version: str = Field(description="Model version string from SignalModelConfig.")
    evaluated_at: datetime = Field(description="UTC datetime when evaluation was performed.")


# ── Configuration ─────────────────────────────────────────────────────────────


class SignalModelConfig(BaseModel):
    """Hyper-parameters and metadata for :class:`SignalModel`.

    All fields have sensible defaults that work for the initial prototype.
    Override them when constructing the model for fine-tuning experiments.
    """

    n_estimators: int = Field(default=300, description="Number of boosting rounds.")
    max_depth: int = Field(default=4, description="Maximum tree depth.")
    learning_rate: float = Field(default=0.05, description="Boosting learning rate (eta).")
    subsample: float = Field(default=0.8, description="Fraction of samples per tree.")
    colsample_bytree: float = Field(
        default=0.8, description="Fraction of features per tree."
    )
    scale_pos_weight: float = Field(
        default=1.0,
        description="Weight of positive class; increase for imbalanced datasets.",
    )
    random_state: int = Field(default=42, description="Random seed for reproducibility.")
    feature_columns: list[str] = Field(
        default_factory=lambda: list(FEATURE_COLUMNS),
        description="Ordered list of feature columns the model expects.",
    )
    model_version: str = Field(
        default="0.1.0", description="Semantic version string for the saved artefact."
    )


# ── Model ─────────────────────────────────────────────────────────────────────


class SignalModel:
    """XGBoost binary classifier wrapped with Platt calibration.

    Predicts whether a prediction will be correct (``is_correct=1``).
    Confidence scores are Platt-calibrated via scikit-learn's
    ``CalibratedClassifierCV(method='sigmoid')`` so they reflect real
    empirical probabilities.

    Parameters
    ----------
    config:
        Model hyper-parameters.  Uses :class:`SignalModelConfig` defaults
        when not provided.
    """

    def __init__(self, config: SignalModelConfig | None = None) -> None:
        self._config = config or SignalModelConfig()
        self._model: object | None = None          # calibrated sklearn estimator
        self._raw_model: object | None = None      # underlying XGBClassifier
        self._feature_medians: dict[str, float] = {}
        self._feature_importances: dict[str, float] = {}
        self._calibration_stats: CalibrationStats | None = None

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(
        self,
        X: "pd.DataFrame",
        y: "pd.Series",
        sample_weight: "pd.Series | None" = None,
    ) -> None:
        """Train XGBoost and fit Platt calibration on the same data.

        For production use, pass separate training and calibration splits to
        avoid optimistic calibration estimates.  This method accepts the full
        dataset and uses 5-fold cross-validation internally to fit the
        calibration layer.

        Parameters
        ----------
        X:
            Feature DataFrame with columns matching ``config.feature_columns``.
            Missing values (``NaN``) are imputed with column medians.
        y:
            Binary target series (``int`` or ``bool``); 1 = correct prediction.
        sample_weight:
            Optional per-row training weight. Used to weight outcomes by
            severity — a -5% blown trade should count 10× more than a
            -0.5% stop-out. When ``None`` (legacy path), all rows carry
            equal weight. Values outside ``[0, 10]`` are treated as
            anomalies and clipped so no single row can swamp the
            gradient. Propagated through ``CalibratedClassifierCV`` to
            both the XGBoost base estimator and the sigmoid calibration
            fold.

        Raises
        ------
        ImportError
            If ``xgboost`` or ``scikit-learn`` are not installed.
        """
        xgb, CalibratedClassifierCV, np = _require_ml_deps()

        logger.info(
            "Fitting SignalModel with %d samples, %d features.",
            len(X),
            len(self._config.feature_columns),
        )

        # ── Select and order feature columns ──────────────────────────────────
        feature_cols = [c for c in self._config.feature_columns if c in X.columns]
        X_feat = X[feature_cols].copy()

        # ── Impute missing values and store medians for inference ─────────────
        X_imputed = impute_features(X_feat)
        self._feature_medians = {
            col: float(X_feat[col].median())
            for col in feature_cols
            if not X_feat[col].isna().all()
        }

        # ── Fit base XGBoost classifier ───────────────────────────────────────
        base_clf = xgb.XGBClassifier(
            n_estimators=self._config.n_estimators,
            max_depth=self._config.max_depth,
            learning_rate=self._config.learning_rate,
            subsample=self._config.subsample,
            colsample_bytree=self._config.colsample_bytree,
            scale_pos_weight=self._config.scale_pos_weight,
            random_state=self._config.random_state,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
        )
        self._raw_model = base_clf

        # ── Wrap with Platt scaling (5-fold CV) ───────────────────────────────
        calibrated = CalibratedClassifierCV(
            estimator=base_clf,
            method="sigmoid",
            cv=5,
        )
        # sklearn's `CalibratedClassifierCV.fit` forwards
        # `sample_weight` to both the CV-split XGBoost fit and the
        # calibration-regression fit, which is exactly what we want:
        # a high-magnitude outcome should dominate both decision-
        # tree splits AND the probability calibration curve. Clip to
        # [0, 10] as a belt-and-braces guard — anything larger than
        # 10× uniform weight is almost certainly a data-quality bug,
        # not a real 10× important trade.
        if sample_weight is not None:
            sw_array = sample_weight.clip(lower=0.0, upper=10.0).values
            calibrated.fit(X_imputed.values, y.values, sample_weight=sw_array)
        else:
            calibrated.fit(X_imputed.values, y.values)
        self._model = calibrated

        # ── Cache feature importances from the first fold's base estimator ────
        try:
            # CalibratedClassifierCV exposes .calibrated_classifiers_
            first_clf = calibrated.calibrated_classifiers_[0].estimator
            raw_importances: "np.ndarray" = first_clf.feature_importances_
            self._feature_importances = dict(
                zip(feature_cols, raw_importances.tolist(), strict=False)
            )
        except (AttributeError, IndexError):
            self._feature_importances = {}

        logger.info("SignalModel training complete.")

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict_proba(self, X: "pd.DataFrame") -> "np.ndarray":
        """Return calibrated probability of ``is_correct=1`` for each row.

        Parameters
        ----------
        X:
            Feature DataFrame.  Missing values are imputed using the medians
            computed during training.

        Returns
        -------
        numpy.ndarray
            1-D array of probabilities, shape ``(n_samples,)``, one value per row.

        Raises
        ------
        RuntimeError
            If the model has not been trained yet.
        ImportError
            If ``numpy`` is not installed.
        """
        if self._model is None:
            raise RuntimeError(
                "SignalModel has not been trained. Call .fit() or .load() first."
            )

        try:
            import numpy as np  # noqa: PLC0415,F401  # used below in "np.ndarray" type annotation
        except ImportError as exc:
            raise ImportError(
                "numpy is required for SignalModel.predict_proba. "
                "Install it with: pip install numpy"
            ) from exc

        feature_cols = [c for c in self._config.feature_columns if c in X.columns]
        X_feat = X[feature_cols].copy()
        X_imputed = impute_features(X_feat, medians=self._feature_medians)

        # predict_proba returns shape (n, 2); column 1 = P(is_correct=1)
        proba_matrix: "np.ndarray" = self._model.predict_proba(X_imputed.values)
        return proba_matrix[:, 1]

    def shap_top_features(self, X: "pd.DataFrame", top_n: int = 3) -> "list[list[tuple[str, float]]]":
        """Return per-row top-N SHAP-like feature contributions.

        Uses XGBoost's native ``pred_contribs=True`` path on each
        fold's base estimator inside ``CalibratedClassifierCV`` and
        averages across folds. No external ``shap`` dependency
        needed. The returned contributions are in log-odds space,
        sign-preserving: negative values pushed the prediction
        DOWN, positive pushed it UP. The caller typically ranks by
        absolute value.

        Returns a list of length ``len(X)`` — each element is a
        list of ``(feature_name, contribution)`` tuples sorted by
        ``abs(contribution)`` descending, truncated to ``top_n``.
        Returns an empty list per row when SHAP can't be computed
        (e.g. the model isn't a ``CalibratedClassifierCV(XGB)``
        assembly after load).
        """
        if self._model is None:
            raise RuntimeError("SignalModel has not been trained.")
        try:
            import numpy as np  # noqa: PLC0415
            import xgboost as xgb  # noqa: PLC0415
        except ImportError:
            return [[] for _ in range(len(X))]

        feature_cols = [c for c in self._config.feature_columns if c in X.columns]
        X_feat = X[feature_cols].copy()
        X_imputed = impute_features(X_feat, medians=self._feature_medians)

        try:
            calibrated_folds = getattr(
                self._model, "calibrated_classifiers_", None,
            )
            if not calibrated_folds:
                return [[] for _ in range(len(X))]
            dmat = xgb.DMatrix(X_imputed.values, feature_names=feature_cols)
            # Average SHAP across the 5 folds' base estimators. Each
            # predict(pred_contribs=True) returns an (n, F+1) matrix
            # where the last column is the bias term — we drop it.
            agg = None
            n_folds = 0
            for fold in calibrated_folds:
                base = getattr(fold, "estimator", None)
                if base is None:
                    continue
                booster = base.get_booster()
                contribs = booster.predict(dmat, pred_contribs=True)
                if agg is None:
                    agg = np.zeros_like(contribs)
                agg = agg + contribs
                n_folds += 1
            if agg is None or n_folds == 0:
                return [[] for _ in range(len(X))]
            mean_contribs = agg / n_folds
            # Strip bias column.
            feat_contribs = mean_contribs[:, :-1]

            rows: list[list[tuple[str, float]]] = []
            for i in range(feat_contribs.shape[0]):
                pairs = list(zip(feature_cols, feat_contribs[i].tolist(), strict=False))
                pairs.sort(key=lambda kv: abs(kv[1]), reverse=True)
                rows.append(pairs[:top_n])
            return rows
        except Exception:
            return [[] for _ in range(len(X))]


    def predict(self, snapshot: FeaturesSnapshot) -> SignalResult:
        """Score a single :class:`FeaturesSnapshot` and return a :class:`SignalResult`.

        Processing pipeline:

        1. Convert snapshot → flat feature dict via :func:`~risedual_core.ml.features.snapshot_to_vector`.
        2. Encode regime label via :func:`~risedual_core.ml.features.encode_regime`.
        3. Stack into a single-row DataFrame and impute NaN.
        4. Call :meth:`predict_proba` to get the calibrated probability.
        5. Map probability → :class:`~risedual_core.schemas.market.PredictionDirection`.
        6. Attach top-3 feature importances.

        Parameters
        ----------
        snapshot:
            The point-in-time feature snapshot for a single ticker.

        Returns
        -------
        SignalResult
            Fully populated signal result including direction and confidence.

        Raises
        ------
        RuntimeError
            If the model has not been trained yet.
        """
        # Step 1 – convert snapshot to flat feature dict
        vector = snapshot_to_vector(snapshot)

        # Step 2 – encode regime and add as extra feature (not in FEATURE_COLUMNS
        # by default, but we include it for potential use)
        vector["regime_encoded"] = float(encode_regime(snapshot.regime_label))

        # Step 3 – build DataFrame and run through the full predict_proba pipeline
        df = vector_to_dataframe([vector])
        calibrated_prob = float(self.predict_proba(df)[0])

        # Step 4 – determine direction from probability
        # calibrated_prob is P(is_correct=1); >0.5 means we expect the bull
        # direction to be correct (simplified binary mapping).
        direction = (
            PredictionDirection.UP if calibrated_prob > 0.5 else PredictionDirection.DOWN
        )

        # Step 5 – top-3 feature importances
        top_features: dict[str, float] = {}
        if self._feature_importances:
            sorted_imp = sorted(
                self._feature_importances.items(), key=lambda kv: kv[1], reverse=True
            )
            top_features = dict(sorted_imp[:3])

        return SignalResult(
            ticker=snapshot.ticker,
            direction=direction,
            confidence=calibrated_prob,
            raw_probability=calibrated_prob,  # same until we add a separate raw pass
            regime=snapshot.regime_label,
            feature_importance=top_features,
            model_version=self._config.model_version,
            timestamp=snapshot.timestamp,
            explanation=None,
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Serialize the model, calibrator, config, and feature medians.

        The artefact is a joblib-compressed file containing a plain dict with
        the following keys:

        - ``"model"`` — the fitted :class:`CalibratedClassifierCV` instance
        - ``"config"`` — the :class:`SignalModelConfig` dict
        - ``"feature_medians"`` — ``dict[str, float]``
        - ``"feature_importances"`` — ``dict[str, float]``

        Parameters
        ----------
        path:
            File path to write.  Parent directories must exist.

        Raises
        ------
        RuntimeError
            If the model has not been trained yet.
        ImportError
            If ``joblib`` is not installed.
        """
        # requires: scikit-learn (ships joblib), or standalone joblib
        if self._model is None:
            raise RuntimeError(
                "Cannot save an untrained SignalModel. Call .fit() first."
            )

        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for SignalModel.save. "
                "Install it with: pip install joblib  (or scikit-learn)"
            ) from exc

        payload = {
            "model": self._model,
            "config": self._config.model_dump(),
            "feature_medians": self._feature_medians,
            "feature_importances": self._feature_importances,
            "calibration_stats": (
                self._calibration_stats.model_dump() if self._calibration_stats else None
            ),
        }
        joblib.dump(payload, Path(path), compress=3)
        logger.info("SignalModel saved to %s.", path)

    @classmethod
    def load(cls, path: str | Path) -> SignalModel:
        """Load a previously saved :class:`SignalModel` from disk.

        Parameters
        ----------
        path:
            Path to a joblib file produced by :meth:`save`.

        Returns
        -------
        SignalModel
            A fully reconstructed, ready-to-use model instance.

        Raises
        ------
        FileNotFoundError
            If ``path`` does not exist.
        ImportError
            If ``joblib`` is not installed.
        """
        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for SignalModel.load. "
                "Install it with: pip install joblib  (or scikit-learn)"
            ) from exc

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"SignalModel artefact not found: {path}")

        payload = joblib.load(path)
        config = SignalModelConfig(**payload["config"])

        instance = cls(config=config)
        instance._model = payload["model"]
        instance._feature_medians = payload.get("feature_medians", {})
        instance._feature_importances = payload.get("feature_importances", {})
        raw_stats = payload.get("calibration_stats")
        instance._calibration_stats = CalibrationStats(**raw_stats) if raw_stats else None

        logger.info("SignalModel loaded from %s (version=%s).", path, config.model_version)
        return instance

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_trained(self) -> bool:
        """Return ``True`` if the model has been fitted and is ready for inference."""
        return self._model is not None

    @property
    def config(self) -> SignalModelConfig:
        """Return the model configuration."""
        return self._config

    @property
    def calibration_stats(self) -> CalibrationStats | None:
        """Return post-training calibration stats, or ``None`` if not yet computed.

        Populated automatically by :meth:`evaluate` after a training run.
        Pass the held-out evaluation set to :meth:`evaluate` to compute
        Brier score and ECE against ground-truth outcomes.
        """
        return self._calibration_stats

    def evaluate(
        self,
        X_eval: "pd.DataFrame",
        y_eval: "pd.Series",
        n_predictions: int = 0,
    ) -> CalibrationStats:
        """Compute and store calibration stats against a held-out evaluation set.

        Should be called after :meth:`fit` with a held-out split.
        Results are stored in :attr:`calibration_stats` and also persisted
        by :meth:`save` so they survive serialization.

        Parameters
        ----------
        X_eval:
            Feature DataFrame (same schema as training data).
        y_eval:
            Ground-truth binary labels (1 = correct prediction).
        n_predictions:
            Total number of labeled predictions in the dataset (for reporting).

        Returns
        -------
        CalibrationStats
            The computed statistics.
        """
        from risedual_core.ml.calibration import brier_score, expected_calibration_error  # noqa: PLC0415

        probas = self.predict_proba(X_eval)
        preds = (probas > 0.5).astype(int)
        accuracy = float((preds == y_eval.values).mean())
        brier = brier_score(y_eval.values.tolist(), probas.tolist())
        ece = expected_calibration_error(y_eval.values.tolist(), probas.tolist())

        stats = CalibrationStats(
            accuracy=accuracy,
            brier_score=brier,
            ece=ece,
            n_predictions=n_predictions or len(y_eval),
            model_version=self._config.model_version,
            evaluated_at=datetime.now(timezone.utc),
        )
        self._calibration_stats = stats
        logger.info(
            "CalibrationStats: accuracy=%.3f brier=%.4f ece=%.4f n=%d",
            accuracy, brier, ece, stats.n_predictions,
        )
        return stats
