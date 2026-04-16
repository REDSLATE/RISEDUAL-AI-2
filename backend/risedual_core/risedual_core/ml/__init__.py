"""risedual_core.ml public API.

Re-exports the primary ML classes, configs, calibration utilities, and the
canonical feature/signal schemas so callers can import directly from
``risedual_core.ml`` without knowing the sub-module layout.

Example::

    from risedual_core.ml import SignalModel, RegimeModel, calibration_curve_data
    from risedual_core.ml import FeaturesSnapshot, SignalResult
"""

from __future__ import annotations

from risedual_core.ml.calibration import (
    brier_score,
    calibration_curve_data,
    expected_calibration_error,
)
from risedual_core.ml.regime_model import RegimeConfig, RegimeModel
from risedual_core.ml.signal_model import SignalModel, SignalModelConfig
from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

__all__ = [
    # Models
    "SignalModel",
    "SignalModelConfig",
    "RegimeModel",
    "RegimeConfig",
    # Schemas (re-exported for convenience)
    "FeaturesSnapshot",
    "SignalResult",
    # Calibration utilities
    "calibration_curve_data",
    "expected_calibration_error",
    "brier_score",
]
