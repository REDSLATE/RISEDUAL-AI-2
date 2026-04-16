"""Feature engineering utilities for the risedual_core ML layer.

Provides the canonical ``FEATURE_COLUMNS`` list, conversion helpers for turning
:class:`~risedual_core.schemas.market.FeaturesSnapshot` objects into flat
numeric vectors, and imputation logic for inference-time missing values.

All heavy dependencies (pandas) are imported lazily so the module can be
imported in environments where pandas is not installed, as long as the
functions that require it are never called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from risedual_core.schemas.market import FeaturesSnapshot

if TYPE_CHECKING:
    import pandas as pd

# ── Column definitions ────────────────────────────────────────────────────────

PATTERN_COLUMNS: list[str] = [
    # "pattern_double_bottom",  # EXCLUDED: 42.2% WR, net PnL drag in backtest
    "pattern_bullish_engulfing",
    "pattern_bearish_engulfing",
    "pattern_bull_flag",
    "pattern_rsi_divergence",
    "pattern_macd_crossover",
    "pattern_volume_surge",
    "pattern_head_and_shoulders",
]
"""Phase 2 pattern boolean columns appended to :data:`FEATURE_COLUMNS`.

All default to ``None`` on :class:`~risedual_core.schemas.market.FeaturesSnapshot`
and are imputed to ``False`` (0) before XGBoost sees them.
"""

FEATURE_COLUMNS: list[str] = [
    "rsi_14",
    "macd",
    "macd_signal",
    "sma_20",
    "sma_50",
    "volume_ratio",
    "sentiment_score",
    "insider_activity",
    "sector_momentum",
    *PATTERN_COLUMNS,
]
"""Ordered list of feature columns consumed by the ML models.

Numeric indicators come first (Phase 1), pattern booleans follow (Phase 2).
The order is significant — any array constructed from a snapshot must follow
this ordering to remain compatible with saved model artefacts.  Snapshots
logged before Phase 2 deployment will have ``None`` for pattern columns,
which :func:`impute_features` fills with ``False`` (0.0) automatically.
"""

REGIME_COLUMN: str = "regime_label"
"""Name of the column that carries the regime label (string) in DataFrames."""

TARGET_COLUMN: str = "is_correct"
"""Name of the binary target column used during signal-model training."""


# ── Conversion helpers ────────────────────────────────────────────────────────


def snapshot_to_vector(snapshot: FeaturesSnapshot) -> dict[str, float]:
    """Convert a :class:`FeaturesSnapshot` to a flat numeric feature dict.

    Only the columns in :data:`FEATURE_COLUMNS` are included. Fields that
    are ``None`` on the snapshot are mapped to ``float("nan")`` so that
    downstream imputation can detect and fill them.

    Parameters
    ----------
    snapshot:
        The point-in-time feature snapshot to convert.

    Returns
    -------
    dict[str, float]
        Mapping of feature name → numeric value (possibly ``nan``).
    """
    nan = float("nan")
    return {col: (getattr(snapshot, col, None) or nan) for col in FEATURE_COLUMNS}


def vector_to_dataframe(vectors: list[dict[str, float]]) -> "pd.DataFrame":
    """Stack a list of feature dicts into a :class:`pandas.DataFrame`.

    Column order follows :data:`FEATURE_COLUMNS`. Extra keys present in the
    dicts are preserved but will appear after the canonical columns.

    Parameters
    ----------
    vectors:
        List of flat feature dicts, each produced by :func:`snapshot_to_vector`
        or equivalent.

    Returns
    -------
    pandas.DataFrame
        Rows correspond to individual snapshots; columns are feature names.

    Raises
    ------
    ImportError
        If ``pandas`` is not installed.
    """
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "pandas is required for vector_to_dataframe. "
            "Install it with: pip install pandas"
        ) from exc

    if not vectors:
        return pd.DataFrame(columns=FEATURE_COLUMNS)

    df = pd.DataFrame(vectors)
    # Reorder so canonical columns come first (extra columns follow)
    existing_canonical = [c for c in FEATURE_COLUMNS if c in df.columns]
    extra_cols = [c for c in df.columns if c not in FEATURE_COLUMNS]
    return df[existing_canonical + extra_cols]


def encode_regime(regime_label: str | None) -> int:
    """Map a string regime label to a signed integer.

    The mapping is intentionally ordinal so that models can treat this as a
    continuous feature alongside the other numerics.

    Parameters
    ----------
    regime_label:
        One of ``"bull"``, ``"bear"``, ``"sideways"``, or ``None``.

    Returns
    -------
    int
        ``1`` for bull, ``-1`` for bear, ``0`` for sideways or unknown.
    """
    _MAP: dict[str | None, int] = {
        "bull": 1,
        "bear": -1,
        "sideways": 0,
        None: 0,
    }
    return _MAP.get(regime_label, 0)


def impute_features(
    df: "pd.DataFrame",
    medians: dict[str, float] | None = None,
) -> "pd.DataFrame":
    """Fill ``NaN`` values in feature columns with column medians.

    During *training*, call without ``medians`` and the function will compute
    medians from ``df`` itself.  During *inference*, pass the medians computed
    on the training set so that the imputation is consistent.

    Parameters
    ----------
    df:
        DataFrame whose columns include (a subset of) :data:`FEATURE_COLUMNS`.
        Non-feature columns are left untouched.
    medians:
        Optional pre-computed median values keyed by column name.  When
        ``None``, medians are derived from ``df`` (fit + transform on the
        same data — appropriate for training).

    Returns
    -------
    pandas.DataFrame
        A copy of ``df`` with ``NaN`` values replaced by the appropriate
        column median.

    Raises
    ------
    ImportError
        If ``pandas`` is not installed.
    """
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "pandas is required for impute_features. "
            "Install it with: pip install pandas"
        ) from exc

    df = df.copy()

    # ── Numeric feature columns — fill with median (or 0.0 if all null) ─────────
    numeric_cols_present = [
        c for c in FEATURE_COLUMNS
        if c in df.columns and c not in PATTERN_COLUMNS
    ]

    if medians is None:
        # Fit medians from the current DataFrame (training-time usage)
        medians = {
            col: float(df[col].median())
            for col in numeric_cols_present
            if not df[col].isna().all()
        }

    for col in numeric_cols_present:
        fill_value = medians.get(col, 0.0)
        df[col] = df[col].fillna(fill_value)

    # ── Pattern boolean columns — fill None / NaN with False (0.0) ────────────
    # Pattern columns are boolean features; median imputation is meaningless.
    # Pre-Phase-2 snapshots have None here; treat as "pattern not detected".
    pattern_cols_present = [c for c in PATTERN_COLUMNS if c in df.columns]
    for col in pattern_cols_present:
        df[col] = df[col].fillna(False).astype(float)

    return df
