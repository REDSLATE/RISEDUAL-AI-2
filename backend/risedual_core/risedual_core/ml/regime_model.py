"""Market regime classifier for risedual_core.

Identifies the current market regime (bull / bear / sideways) from a daily
price series using either a Hidden Markov Model (default) or KMeans clustering.

Both implementations operate on the same three derived features:

- ``rolling_vol`` — 20-day realised volatility (std-dev of log returns)
- ``trend``       — normalised distance from the 60-day SMA
- ``momentum``    — 20-day rolling return

After fitting, regimes are mapped to bull / bear / sideways by sorting
cluster centroids on their mean ``momentum``:

- highest momentum → ``"bull"``
- lowest momentum  → ``"bear"``
- middle           → ``"sideways"``

Typical usage::

    model = RegimeModel()
    model.fit(price_series)       # pd.Series with DatetimeIndex

    current = model.predict(price_series)   # "bull" | "bear" | "sideways"
    full    = model.predict_series(price_series)  # pd.Series of labels

    model.save("regime_v1.joblib")
    model2 = RegimeModel.load("regime_v1.joblib")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

REGIME_LABELS: dict[int, str] = {0: "sideways", 1: "bull", 2: "bear"}
"""Mapping from sorted-momentum rank to human-readable regime name.

Rank 0 = middle momentum → sideways
Rank 1 = highest momentum → bull
Rank 2 = lowest momentum  → bear
"""

_VOL_WINDOW = 20
_TREND_WINDOW = 60
_MOM_WINDOW = 20

# ── Configuration ─────────────────────────────────────────────────────────────


class RegimeConfig(BaseModel):
    """Configuration for :class:`RegimeModel`."""

    n_regimes: int = Field(
        default=3,
        description="Number of regimes to identify (bull, bear, sideways).",
    )
    method: Literal["hmm", "kmeans"] = Field(
        default="hmm",
        description="Clustering method: 'hmm' (default) or 'kmeans' fallback.",
    )
    lookback_days: int = Field(
        default=60,
        description="Minimum historical days required before fitting.",
    )
    random_state: int = Field(
        default=42, description="Random seed for reproducibility (KMeans only)."
    )
    model_version: str = Field(
        default="0.1.0", description="Semantic version for artefact tracking."
    )


# ── Internal feature extraction ───────────────────────────────────────────────


def _extract_regime_features(price_series: "pd.Series") -> "pd.DataFrame":
    """Derive rolling features from a daily price series.

    Parameters
    ----------
    price_series:
        Daily closing prices with a :class:`pandas.DatetimeIndex`.

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ``rolling_vol``, ``trend``, ``momentum``,
        dropping any rows with ``NaN`` (warm-up period).
    """
    try:
        import numpy as np  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "pandas and numpy are required for regime feature extraction. "
            "Install them with: pip install pandas numpy"
        ) from exc

    log_returns = np.log(price_series / price_series.shift(1))

    rolling_vol = log_returns.rolling(_VOL_WINDOW).std()
    sma_60 = price_series.rolling(_TREND_WINDOW).mean()
    trend = (price_series / sma_60) - 1.0
    momentum = price_series.pct_change(_MOM_WINDOW)

    features = pd.DataFrame(
        {"rolling_vol": rolling_vol, "trend": trend, "momentum": momentum}
    ).dropna()

    return features


def _rank_clusters_by_momentum(
    labels: "pd.Series | list",
    features: "pd.DataFrame",
) -> dict[int, str]:
    """Map raw cluster indices to regime names ordered by mean momentum.

    Parameters
    ----------
    labels:
        Array-like of integer cluster assignments aligned to ``features``.
    features:
        Feature DataFrame containing a ``momentum`` column.

    Returns
    -------
    dict[int, str]
        Maps original cluster index → ``"bull"``, ``"bear"``, or ``"sideways"``.
    """
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("pandas is required for cluster ranking.") from exc

    label_series = pd.Series(labels, index=features.index)
    mean_momentum = (
        features["momentum"]
        .groupby(label_series)
        .mean()
        .sort_values(ascending=False)
    )

    # sorted order: highest momentum → bull (1), then sideways (0), then bear (2)
    rank_to_regime = {0: "sideways", 1: "bull", 2: "bear"}
    # mean_momentum is sorted descending: index 0 = highest, last = lowest
    cluster_to_rank = {
        cluster_idx: rank
        for rank, cluster_idx in enumerate(mean_momentum.index)
    }
    return {
        cluster_idx: rank_to_regime.get(rank, "sideways")
        for cluster_idx, rank in cluster_to_rank.items()
    }


# ── Model ─────────────────────────────────────────────────────────────────────


class RegimeModel:
    """Unsupervised market regime classifier.

    HMM (default) on rolling vol + trend + breadth features, or KMeans
    fallback.  Labels regimes as bull / bear / sideways based on the centroid
    with the highest mean return in each cluster.

    Parameters
    ----------
    config:
        Model configuration.  Uses :class:`RegimeConfig` defaults when omitted.
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self._config = config or RegimeConfig()
        self._model: object | None = None
        self._cluster_map: dict[int, str] = {}   # cluster index → regime label
        self._feature_means: dict[str, float] = {}
        self._feature_stds: dict[str, float] = {}

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(self, price_series: "pd.Series") -> None:
        """Fit the regime model on a daily price series.

        Derives rolling volatility, trend, and momentum features, then fits
        an HMM (via ``hmmlearn``) or KMeans (via ``scikit-learn``) on those
        3 features.  Assigns regime labels by matching centroids to
        bull / bear / sideways based on mean momentum in each cluster.

        Parameters
        ----------
        price_series:
            Daily closing prices indexed by :class:`pandas.DatetimeIndex`.
            Must have at least ``config.lookback_days`` rows.

        Raises
        ------
        ValueError
            If the series is too short.
        ImportError
            If the required ML library is not installed.
        """
        if len(price_series) < self._config.lookback_days:
            raise ValueError(
                f"price_series must have at least {self._config.lookback_days} rows; "
                f"got {len(price_series)}."
            )

        features = _extract_regime_features(price_series)

        if features.empty:
            raise ValueError(
                "Not enough data after computing rolling features. "
                "Extend the price series or reduce lookback_days."
            )

        # Standardise features for better clustering
        means = features.mean()
        stds = features.std().replace(0, 1.0)
        X = ((features - means) / stds).values

        self._feature_means = means.to_dict()
        self._feature_stds = stds.to_dict()

        if self._config.method == "hmm":
            labels = self._fit_hmm(X)
        else:
            labels = self._fit_kmeans(X)

        import pandas as pd  # noqa: PLC0415

        label_series = pd.Series(labels, index=features.index)
        self._cluster_map = _rank_clusters_by_momentum(label_series, features)

        logger.info(
            "RegimeModel fitted (%s, %d regimes). Cluster map: %s",
            self._config.method,
            self._config.n_regimes,
            self._cluster_map,
        )

    def _fit_hmm(self, X: "object") -> list[int]:
        """Fit a Gaussian HMM and return state sequence.

        Parameters
        ----------
        X:
            Standardised feature matrix, shape ``(n_samples, 3)``.

        Returns
        -------
        list[int]
            Viterbi-decoded state labels.

        Raises
        ------
        ImportError
            If ``hmmlearn`` is not installed.
        """
        # requires: hmmlearn
        try:
            from hmmlearn.hmm import GaussianHMM  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "hmmlearn is required for RegimeModel with method='hmm'. "
                "Install it with: pip install hmmlearn  "
                "or switch to method='kmeans' to use scikit-learn instead."
            ) from exc

        import numpy as np  # noqa: PLC0415

        hmm = GaussianHMM(
            n_components=self._config.n_regimes,
            covariance_type="full",
            n_iter=200,
            random_state=self._config.random_state,
        )
        hmm.fit(X)
        self._model = hmm

        labels: list[int] = hmm.predict(X).tolist()
        return labels

    def _fit_kmeans(self, X: "object") -> list[int]:
        """Fit KMeans and return cluster assignment sequence.

        Parameters
        ----------
        X:
            Standardised feature matrix, shape ``(n_samples, 3)``.

        Returns
        -------
        list[int]
            Cluster labels.

        Raises
        ------
        ImportError
            If ``scikit-learn`` is not installed.
        """
        # requires: scikit-learn
        try:
            from sklearn.cluster import KMeans  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required for RegimeModel with method='kmeans'. "
                "Install it with: pip install scikit-learn"
            ) from exc

        kmeans = KMeans(
            n_clusters=self._config.n_regimes,
            random_state=self._config.random_state,
            n_init="auto",
        )
        kmeans.fit(X)
        self._model = kmeans

        labels: list[int] = kmeans.labels_.tolist()
        return labels

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, price_series: "pd.Series") -> str:
        """Return the current regime label for the most recent data point.

        Parameters
        ----------
        price_series:
            Daily price series, same format as passed to :meth:`fit`.

        Returns
        -------
        str
            One of ``"bull"``, ``"bear"``, or ``"sideways"``.

        Raises
        ------
        RuntimeError
            If the model has not been trained.
        """
        label_series = self.predict_series(price_series)
        if label_series.empty:
            return "sideways"
        return str(label_series.iloc[-1])

    def predict_series(self, price_series: "pd.Series") -> "pd.Series":
        """Return a :class:`pandas.Series` of regime labels aligned to the price index.

        Parameters
        ----------
        price_series:
            Daily price series, same format as passed to :meth:`fit`.

        Returns
        -------
        pandas.Series
            String regime labels indexed to the same dates as the feature
            rows (shorter than ``price_series`` due to warm-up period).

        Raises
        ------
        RuntimeError
            If the model has not been trained.
        ImportError
            If ``pandas`` or the underlying ML library is not installed.
        """
        if self._model is None:
            raise RuntimeError(
                "RegimeModel has not been trained. Call .fit() or .load() first."
            )

        try:
            import numpy as np  # noqa: PLC0415
            import pandas as pd  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "pandas and numpy are required for RegimeModel.predict_series."
            ) from exc

        features = _extract_regime_features(price_series)
        if features.empty:
            return pd.Series(dtype=str)

        means = pd.Series(self._feature_means)
        stds = pd.Series(self._feature_stds).replace(0, 1.0)
        X = ((features - means) / stds).values

        if self._config.method == "hmm":
            raw_labels: list[int] = self._model.predict(X).tolist()
        else:
            raw_labels = self._model.predict(X).tolist()

        regime_labels = [self._cluster_map.get(lbl, "sideways") for lbl in raw_labels]
        return pd.Series(regime_labels, index=features.index, dtype=str)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Serialize the regime model to a joblib file.

        Parameters
        ----------
        path:
            Destination file path.

        Raises
        ------
        RuntimeError
            If the model has not been trained.
        ImportError
            If ``joblib`` is not installed.
        """
        if self._model is None:
            raise RuntimeError(
                "Cannot save an untrained RegimeModel. Call .fit() first."
            )

        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for RegimeModel.save. "
                "Install it with: pip install joblib  (or scikit-learn)"
            ) from exc

        payload = {
            "model": self._model,
            "config": self._config.model_dump(),
            "cluster_map": self._cluster_map,
            "feature_means": self._feature_means,
            "feature_stds": self._feature_stds,
        }
        joblib.dump(payload, Path(path), compress=3)
        logger.info("RegimeModel saved to %s.", path)

    @classmethod
    def load(cls, path: str | Path) -> RegimeModel:
        """Load a previously saved :class:`RegimeModel` from disk.

        Parameters
        ----------
        path:
            Path to a joblib file produced by :meth:`save`.

        Returns
        -------
        RegimeModel
            Fully reconstructed model ready for inference.

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
                "joblib is required for RegimeModel.load. "
                "Install it with: pip install joblib  (or scikit-learn)"
            ) from exc

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"RegimeModel artefact not found: {path}")

        payload = joblib.load(path)
        config = RegimeConfig(**payload["config"])

        instance = cls(config=config)
        instance._model = payload["model"]
        instance._cluster_map = payload.get("cluster_map", {})
        instance._feature_means = payload.get("feature_means", {})
        instance._feature_stds = payload.get("feature_stds", {})

        logger.info("RegimeModel loaded from %s (version=%s).", path, config.model_version)
        return instance

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_trained(self) -> bool:
        """Return ``True`` if the model has been fitted and is ready for inference."""
        return self._model is not None

    @property
    def config(self) -> RegimeConfig:
        """Return the model configuration."""
        return self._config
