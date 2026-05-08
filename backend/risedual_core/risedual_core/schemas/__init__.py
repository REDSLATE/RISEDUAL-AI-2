"""risedual_core.schemas public API.

Re-exports the canonical domain models so callers can import directly from
``risedual_core.schemas`` without knowing the sub-module layout.

Example::

    from risedual_core.schemas import FeaturesSnapshot, SignalResult, PredictionDirection
"""

from __future__ import annotations

from risedual_core.schemas.market import (
    FeaturesSnapshot,
    OutcomeLabel,
    PredictionDirection,
    SignalResult,
)

__all__ = [
    "FeaturesSnapshot",
    "OutcomeLabel",
    "PredictionDirection",
    "SignalResult",
]
