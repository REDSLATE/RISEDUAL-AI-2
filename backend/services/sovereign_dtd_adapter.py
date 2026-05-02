"""
Sovereign AI — DTD (Design-Time / Research) Adapter.

Permitted operations in DTD mode:
* Shadow-log Sovereign AI decisions as a **challenger** against existing
  production outputs.
* Participate in training / evaluation (``nightly_retrain`` placeholder,
  future XGBoost / LightGBM pipelines).
* Produce promotion evidence for the gate.

**Blocked operations** in DTD mode:
* Direct trade execution (the challenger never touches order flow).

Every DTD-only function asserts ``require_dtd()`` — if the core is started
with ``RISEDUAL_CORE_MODE=PRD`` (the default), calls into this module raise
``RuntimeError`` so training code can never run against a live production
database.
"""
from __future__ import annotations

from typing import Any, Optional

from services.sovereign_ai_core import (
    SovereignDecision,
    SovereignFeatures,
    nightly_sovereign_retrain_placeholder,
    run_sovereign_ai_decision,
)
from services.sovereign_mode_guard import require_dtd


async def run_dtd_challenger_decision(
    db: Any,
    symbol: str,
    *,
    asset_type: str = "equity",
    features: Optional[SovereignFeatures] = None,
    advisory_votes: Optional[dict[str, Any]] = None,
) -> SovereignDecision:
    """DTD — run Sovereign AI as a challenger and log a shadow decision."""
    require_dtd()
    return await run_sovereign_ai_decision(
        db=db,
        symbol=symbol,
        asset_type=asset_type,  # type: ignore[arg-type]
        shadow=True,
        features=features,
        advisory_votes=advisory_votes,
        persist=True,
    )


async def run_dtd_nightly_retrain(db: Any) -> dict[str, Any]:
    """DTD — build training rows, train/evaluate candidate models,
    produce promotion candidates. Guarded behind ``require_dtd()``.
    """
    require_dtd()
    return await nightly_sovereign_retrain_placeholder(db)
