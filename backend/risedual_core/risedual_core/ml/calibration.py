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


# ── Private helpers ─────────────────────────────────────────────────────────


def _bin_mask(
    y_prob: "np.ndarray",
    lo: float,
    hi: float,
    is_last_bin: bool,
) -> "np.ndarray":
    """Return a boolean mask selecting samples whose predicted probability
    falls within ``[lo, hi)``, or ``[lo, hi]`` for the last bin.

    The last bin includes its right edge to avoid dropping samples with
    ``y_prob == 1.0``.

    Parameters
    ----------
    y_prob:
        1-D array of predicted probabilities.
    lo:
        Lower bin edge (inclusive).
    hi:
        Upper bin edge (exclusive, or inclusive when ``is_last_bin=True``).
    is_last_bin:
        If ``True`` the upper edge is included (closed interval on both sides).

    Returns
    -------
    numpy.ndarray
        Boolean mask of shape ``(n,)``.
    """
    if is_last_bin:
        return (y_prob >= lo) & (y_prob <= hi)
    return (y_prob >= lo) & (y_prob < hi)


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
        mask = _bin_mask(y_prob, lo, hi, is_last_bin=(i == n_bins - 1))

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
        mask = _bin_mask(y_prob, lo, hi, is_last_bin=(i == n_bins - 1))

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


# ── CalibrationGate & Kelly Sizing ───────────────────────────────────────────


import dataclasses
from enum import Enum
from typing import NamedTuple


class Tier(str, Enum):
    """Autonomous action tiers, ordered by escalating trust requirements."""

    LOCKED = "locked"
    TIER1_ALERTS = "tier1_alerts"
    TIER2_PAPER = "tier2_paper"
    TIER3_LIVE = "tier3_live"


@dataclasses.dataclass(frozen=True)
class TierStatus:
    """Result of a single tier gate check."""

    tier: Tier
    unlocked: bool
    reason: str  # human-readable unlock/lock explanation


class GateResult(NamedTuple):
    """Full result from :func:`check_all_gates`."""

    tier1: TierStatus
    tier2: TierStatus
    tier3: TierStatus

    @property
    def highest_unlocked(self) -> Tier:
        """Return the highest tier that is currently unlocked."""
        if self.tier3.unlocked:
            return Tier.TIER3_LIVE
        if self.tier2.unlocked:
            return Tier.TIER2_PAPER
        if self.tier1.unlocked:
            return Tier.TIER1_ALERTS
        return Tier.LOCKED


# Gate thresholds (these are the single source of truth — never duplicate inline)
_T1_MIN_ACCURACY: float = 0.55
_T1_MIN_PREDICTIONS: int = 100
_T1_MAX_ECE: float = 0.15

_T2_MIN_ACCURACY: float = 0.60
_T2_MIN_SHARPE: float = 1.0
_T2_MAX_DRAWDOWN: float = 0.15  # 15%

_T3_MIN_ACCURACY: float = 0.62
_T3_MIN_SHARPE: float = 1.2
_T3_MAX_DRAWDOWN: float = 0.12  # 12%
_T3_MIN_LIVE_DAYS: int = 30


def check_tier1(
    accuracy: float,
    n_predictions: int,
    ece: float,
) -> TierStatus:
    """Gate check for Tier 1 (push alerts).

    Parameters
    ----------
    accuracy:
        Fraction of correct directional predictions, e.g. ``0.57``.
    n_predictions:
        Total labeled predictions evaluated.
    ece:
        Expected Calibration Error (lower is better).

    Returns
    -------
    TierStatus
        ``unlocked=True`` iff all three conditions are met.
    """
    failures: list[str] = []
    if accuracy < _T1_MIN_ACCURACY:
        failures.append(
            f"accuracy {accuracy:.3f} < {_T1_MIN_ACCURACY}"
        )
    if n_predictions < _T1_MIN_PREDICTIONS:
        failures.append(
            f"n_predictions {n_predictions} < {_T1_MIN_PREDICTIONS}"
        )
    if ece > _T1_MAX_ECE:
        failures.append(f"ECE {ece:.3f} > {_T1_MAX_ECE}")

    if failures:
        return TierStatus(
            tier=Tier.TIER1_ALERTS,
            unlocked=False,
            reason="Locked — " + "; ".join(failures),
        )
    return TierStatus(
        tier=Tier.TIER1_ALERTS,
        unlocked=True,
        reason=(
            f"Unlocked — accuracy={accuracy:.3f}, "
            f"n={n_predictions}, ECE={ece:.3f}"
        ),
    )


def check_tier2(
    accuracy: float,
    sharpe: float,
    max_drawdown: float,
    tier1_unlocked: bool = True,
) -> TierStatus:
    """Gate check for Tier 2 (paper trading).

    Parameters
    ----------
    accuracy:
        Directional accuracy from backtest or live labels.
    sharpe:
        Annualised Sharpe ratio from walk-forward backtest.
    max_drawdown:
        Maximum drawdown as a fraction, e.g. ``0.08`` for 8%.
    tier1_unlocked:
        Tier 2 additionally requires Tier 1 to be unlocked.

    Returns
    -------
    TierStatus
    """
    failures: list[str] = []
    if not tier1_unlocked:
        failures.append("Tier 1 not yet unlocked")
    if accuracy < _T2_MIN_ACCURACY:
        failures.append(
            f"accuracy {accuracy:.3f} < {_T2_MIN_ACCURACY}"
        )
    if sharpe < _T2_MIN_SHARPE:
        failures.append(f"Sharpe {sharpe:.2f} < {_T2_MIN_SHARPE}")
    if max_drawdown > _T2_MAX_DRAWDOWN:
        failures.append(
            f"drawdown {max_drawdown:.1%} > {_T2_MAX_DRAWDOWN:.0%}"
        )

    if failures:
        return TierStatus(
            tier=Tier.TIER2_PAPER,
            unlocked=False,
            reason="Locked — " + "; ".join(failures),
        )
    return TierStatus(
        tier=Tier.TIER2_PAPER,
        unlocked=True,
        reason=(
            f"Unlocked — accuracy={accuracy:.3f}, "
            f"Sharpe={sharpe:.2f}, DD={max_drawdown:.1%}"
        ),
    )


def check_tier3(
    accuracy: float,
    sharpe: float,
    max_drawdown: float,
    live_days: int,
    user_opted_in: bool,
    tier2_unlocked: bool = True,
) -> TierStatus:
    """Gate check for Tier 3 (live execution).

    Parameters
    ----------
    accuracy:
        Directional accuracy from live paper-trading period.
    sharpe:
        Annualised Sharpe from live paper-trading period.
    max_drawdown:
        Maximum drawdown as a fraction.
    live_days:
        Number of calendar days the paper-trading tier has been live.
    user_opted_in:
        User must explicitly opt in before live execution is ever attempted.
    tier2_unlocked:
        Tier 3 additionally requires Tier 2 to be unlocked.

    Returns
    -------
    TierStatus
    """
    failures: list[str] = []
    if not tier2_unlocked:
        failures.append("Tier 2 not yet unlocked")
    if not user_opted_in:
        failures.append("user opt-in required")
    if accuracy < _T3_MIN_ACCURACY:
        failures.append(
            f"accuracy {accuracy:.3f} < {_T3_MIN_ACCURACY}"
        )
    if sharpe < _T3_MIN_SHARPE:
        failures.append(f"Sharpe {sharpe:.2f} < {_T3_MIN_SHARPE}")
    if max_drawdown > _T3_MAX_DRAWDOWN:
        failures.append(
            f"drawdown {max_drawdown:.1%} > {_T3_MAX_DRAWDOWN:.0%}"
        )
    if live_days < _T3_MIN_LIVE_DAYS:
        failures.append(
            f"live_days {live_days} < {_T3_MIN_LIVE_DAYS}"
        )

    if failures:
        return TierStatus(
            tier=Tier.TIER3_LIVE,
            unlocked=False,
            reason="Locked — " + "; ".join(failures),
        )
    return TierStatus(
        tier=Tier.TIER3_LIVE,
        unlocked=True,
        reason=(
            f"Unlocked — accuracy={accuracy:.3f}, "
            f"Sharpe={sharpe:.2f}, DD={max_drawdown:.1%}, "
            f"live_days={live_days}"
        ),
    )


def check_all_gates(
    accuracy: float,
    n_predictions: int,
    ece: float,
    sharpe: float = 0.0,
    max_drawdown: float = 1.0,
    live_days: int = 0,
    user_opted_in: bool = False,
) -> GateResult:
    """Run all three tier gate checks and return a :class:`GateResult`.

    This is the primary entry point for the orchestrator.  All parameters
    default to conservative values so callers only need to provide what is
    available at their current stage.

    Parameters
    ----------
    accuracy:
        Directional prediction accuracy (fraction correct).
    n_predictions:
        Number of labeled predictions used in calibration.
    ece:
        Expected Calibration Error.
    sharpe:
        Annualised Sharpe ratio (0.0 until backtest available).
    max_drawdown:
        Maximum observed drawdown (1.0 = no backtest data yet).
    live_days:
        Days of continuous live paper-trading (0 until Tier 2 active).
    user_opted_in:
        Explicit Tier 3 opt-in flag.

    Returns
    -------
    GateResult
    """
    t1 = check_tier1(accuracy=accuracy, n_predictions=n_predictions, ece=ece)
    t2 = check_tier2(
        accuracy=accuracy,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        tier1_unlocked=t1.unlocked,
    )
    t3 = check_tier3(
        accuracy=accuracy,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        live_days=live_days,
        user_opted_in=user_opted_in,
        tier2_unlocked=t2.unlocked,
    )
    return GateResult(tier1=t1, tier2=t2, tier3=t3)


# ── Kelly Position Sizing ────────────────────────────────────────────────────

_HALF_KELLY: float = 0.5       # standard safety fraction
_MAX_POSITION_FRACTION: float = 0.25   # hard cap: never exceed 25% of portfolio


def kelly_fraction(
    win_probability: float,
    win_return: float = 1.0,
    loss_return: float = 1.0,
) -> float:
    """Compute the full Kelly criterion position fraction.

    Kelly formula: ``f = (p * b - q) / b``

    where ``p`` is win probability, ``q = 1 - p``, and ``b`` is the
    win-to-loss ratio.

    Parameters
    ----------
    win_probability:
        Probability of a winning trade, e.g. ``0.57``.
    win_return:
        Expected gain per unit risked on a win (default 1.0 → 1:1 payoff).
    loss_return:
        Expected loss per unit risked on a loss (default 1.0 → symmetric).

    Returns
    -------
    float
        Full Kelly fraction in ``[0, 1]``.  Negative values (edge < 0) are
        clamped to 0.
    """
    if win_probability <= 0.0 or win_probability >= 1.0:
        return 0.0
    if loss_return <= 0.0 or win_return <= 0.0:
        return 0.0

    b = win_return / loss_return
    q = 1.0 - win_probability
    f = (win_probability * b - q) / b
    return max(0.0, f)


def half_kelly_position(
    win_probability: float,
    portfolio_value: float,
    win_return: float = 1.0,
    loss_return: float = 1.0,
    max_fraction: float = _MAX_POSITION_FRACTION,
) -> float:
    """Compute a half-Kelly position size in dollars.

    Applies a 50% safety haircut on the full Kelly fraction and caps at
    ``max_fraction`` of ``portfolio_value`` to prevent ruin on model error.

    Parameters
    ----------
    win_probability:
        Model confidence / predicted win probability.
    portfolio_value:
        Total portfolio value in dollars.
    win_return:
        Expected fractional gain per dollar on a win.
    loss_return:
        Expected fractional loss per dollar on a loss.
    max_fraction:
        Hard cap as a fraction of portfolio, default 0.25 (25%).

    Returns
    -------
    float
        Dollar position size, always in ``[0, max_fraction * portfolio_value]``.
    """
    full_f = kelly_fraction(
        win_probability=win_probability,
        win_return=win_return,
        loss_return=loss_return,
    )
    half_f = full_f * _HALF_KELLY
    capped_f = min(half_f, max_fraction)
    return round(capped_f * portfolio_value, 2)
