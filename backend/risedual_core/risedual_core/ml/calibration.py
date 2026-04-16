"""Calibration utilities for probability forecast evaluation.

All functions operate on plain NumPy arrays and have no dependency on
scikit-learn, making them safe to use in environments where only numpy
is available.

These utilities are used to evaluate how well the signal model's
confidence scores reflect true empirical frequencies.

References
----------
- Niculescu-Mizil & Caruana (2005). "Predicting Good Probabilities With
  Supervised Learning." ICML.
- Guo et al. (2017). "On Calibration of Modern Neural Networks." ICML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def _require_numpy() -> "type[np]":
    """Return the numpy module, raising a clear error if not installed."""
    try:
        import numpy as np  # noqa: PLC0415
        return np
    except ImportError as exc:
        raise ImportError(
            "numpy is required for calibration utilities. "
            "Install it with: pip install numpy"
        ) from exc


# ── Public API ────────────────────────────────────────────────────────────────


def calibration_curve_data(
    y_true: "np.ndarray",
    y_prob: "np.ndarray",
    n_bins: int = 10,
) -> list[dict]:
    """Compute calibration curve statistics for probability forecasts.

    Divides the probability range [0, 1] into ``n_bins`` equal-width buckets
    and, for each bucket, reports the mean predicted probability and the
    empirical accuracy (fraction of positives).

    Parameters
    ----------
    y_true:
        Binary ground-truth labels (0 or 1) as a 1-D array of shape ``(n,)``.
    y_prob:
        Predicted probabilities for the positive class, shape ``(n,)``.
    n_bins:
        Number of equally-spaced bins to use. Defaults to 10 (decile bins).

    Returns
    -------
    list[dict]
        One dict per non-empty bin with keys:

        - ``"confidence_bucket"`` : str — human-readable range, e.g. ``"0.7-0.8"``
        - ``"mean_predicted"`` : float — mean predicted probability in this bin
        - ``"actual_accuracy"`` : float — fraction of positive outcomes in this bin
        - ``"count"`` : int — number of samples in this bin

    Raises
    ------
    ImportError
        If ``numpy`` is not installed.
    ValueError
        If ``y_true`` and ``y_prob`` have different lengths, or ``n_bins < 2``.
    """
    np = _require_numpy()

    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)

    if y_true.shape != y_prob.shape:
        raise ValueError(
            f"y_true and y_prob must have the same shape; "
            f"got {y_true.shape} and {y_prob.shape}."
        )
    if n_bins < 2:
        raise ValueError(f"n_bins must be at least 2; got {n_bins}.")

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    results: list[dict] = []

    for i in range(n_bins):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]

        # Include the right edge only in the last bin to avoid double-counting 1.0
        if i < n_bins - 1:
            mask = (y_prob >= lo) & (y_prob < hi)
        else:
            mask = (y_prob >= lo) & (y_prob <= hi)

        count = int(mask.sum())
        if count == 0:
            continue  # skip empty bins

        mean_pred = float(y_prob[mask].mean())
        actual_acc = float(y_true[mask].mean())

        # Format the bucket label with 1 decimal place
        lo_str = f"{lo:.1f}"
        hi_str = f"{hi:.1f}"
        bucket_label = f"{lo_str}-{hi_str}"

        results.append(
            {
                "confidence_bucket": bucket_label,
                "mean_predicted": round(mean_pred, 6),
                "actual_accuracy": round(actual_acc, 6),
                "count": count,
            }
        )

    return results


def expected_calibration_error(
    y_true: "np.ndarray",
    y_prob: "np.ndarray",
    n_bins: int = 10,
) -> float:
    """Compute the Expected Calibration Error (ECE).

    ECE is the weighted average of the absolute difference between mean
    predicted probability and empirical accuracy across all bins:

    .. math::

        \\text{ECE} = \\sum_{b=1}^{B} \\frac{|\\mathcal{B}_b|}{n}
                      \\left| \\bar{p}_b - \\bar{y}_b \\right|

    A perfect calibrator has ECE = 0.

    Parameters
    ----------
    y_true:
        Binary ground-truth labels (0 or 1), shape ``(n,)``.
    y_prob:
        Predicted probabilities for the positive class, shape ``(n,)``.
    n_bins:
        Number of equally-spaced bins. Defaults to 10.

    Returns
    -------
    float
        ECE in [0, 1]. Lower is better.

    Raises
    ------
    ImportError
        If ``numpy`` is not installed.
    ValueError
        If ``y_true`` and ``y_prob`` have different lengths, or ``n_bins < 2``.
    """
    np = _require_numpy()

    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)

    if y_true.shape != y_prob.shape:
        raise ValueError(
            f"y_true and y_prob must have the same shape; "
            f"got {y_true.shape} and {y_prob.shape}."
        )
    if n_bins < 2:
        raise ValueError(f"n_bins must be at least 2; got {n_bins}.")

    n = len(y_true)
    if n == 0:
        return 0.0

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0

    for i in range(n_bins):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]

        if i < n_bins - 1:
            mask = (y_prob >= lo) & (y_prob < hi)
        else:
            mask = (y_prob >= lo) & (y_prob <= hi)

        count = int(mask.sum())
        if count == 0:
            continue

        mean_pred = float(y_prob[mask].mean())
        actual_acc = float(y_true[mask].mean())
        ece += (count / n) * abs(mean_pred - actual_acc)

    return round(ece, 8)


def brier_score(
    y_true: "np.ndarray",
    y_prob: "np.ndarray",
) -> float:
    """Compute the Brier score (mean squared probability error).

    The Brier score measures the mean squared difference between forecast
    probabilities and actual binary outcomes:

    .. math::

        \\text{BS} = \\frac{1}{n} \\sum_{i=1}^{n} (p_i - y_i)^2

    A perfect forecaster scores 0; a forecaster that always predicts 0.5
    scores 0.25. Lower is better.

    Parameters
    ----------
    y_true:
        Binary ground-truth labels (0 or 1), shape ``(n,)``.
    y_prob:
        Predicted probabilities for the positive class, shape ``(n,)``.

    Returns
    -------
    float
        Brier score in [0, 1]. Lower is better.

    Raises
    ------
    ImportError
        If ``numpy`` is not installed.
    ValueError
        If ``y_true`` and ``y_prob`` have different lengths.
    """
    np = _require_numpy()

    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)

    if y_true.shape != y_prob.shape:
        raise ValueError(
            f"y_true and y_prob must have the same shape; "
            f"got {y_true.shape} and {y_prob.shape}."
        )

    n = len(y_true)
    if n == 0:
        return 0.0

    return float(round(float(((y_prob - y_true) ** 2).mean()), 8))
