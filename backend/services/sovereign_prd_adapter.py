"""
Sovereign AI — PRD (Production) Adapter.

Permitted operations in PRD mode:
* Run Sovereign AI **shadow-only** beside the production decision (no
  execution, no authority).
* Read the latest Sovereign decision + promotion state.
* Apply a **bounded confidence contribution** to the production
  decision when Sovereign is promoted. Bounds:
    - Sovereign may adjust the production ``confidence`` only.
    - Sovereign may NEVER flip the production ``action``.
    - Sovereign may NEVER convert a production ``HOLD`` into a trade.

**Blocked operations** in PRD mode:
* Training / retraining (``require_prd()`` + the mode guard on the DTD
  adapter cross-guarantee).
* Direct order execution — the ``apply_promoted_sovereign_contribution``
  return value is a confidence delta, never an action override.

The critical rule in calling code:

    # ALLOWED
    production["confidence"] = adjusted_confidence

    # FORBIDDEN — would violate the safety invariant
    # production["action"] = sovereign_decision.action
"""
from __future__ import annotations

from typing import Any, Optional

from services.sovereign_ai_core import (
    SovereignDecision,
    SovereignFeatures,
    run_sovereign_ai_decision,
)
from services.sovereign_mode_guard import require_prd
from services.sovereign_promotion_gate import (
    apply_sovereign_contribution,
    compute_sovereign_promotion_status,
)


async def run_prd_sovereign_shadow(
    db: Any,
    symbol: str,
    *,
    asset_type: str = "equity",
    features: Optional[SovereignFeatures] = None,
    advisory_votes: Optional[dict[str, Any]] = None,
) -> SovereignDecision:
    """PRD — run Sovereign AI shadow-only beside production.

    No execution, no retraining, no direct control. Always persists to
    ``sovereign_decisions`` for the promotion gate math.
    """
    # Intentionally NOT guarded behind require_prd — shadow logging is safe
    # in both modes. The DTD adapter calls the same underlying core. We
    # gate only *contribution* and *training*, not observation.
    return await run_sovereign_ai_decision(
        db=db,
        symbol=symbol,
        asset_type=asset_type,  # type: ignore[arg-type]
        shadow=True,
        features=features,
        advisory_votes=advisory_votes,
        persist=True,
    )


async def apply_promoted_sovereign_contribution(
    db: Any,
    *,
    symbol: str,
    asset_type: str,
    production_action: str,
    production_confidence: float,
) -> tuple[float, dict[str, Any]]:
    """PRD — read the latest Sovereign decision + per-core promotion state,
    then apply a bounded confidence contribution.

    Returns ``(adjusted_confidence, sovereign_meta)``. ``sovereign_meta``
    carries ``applied`` (bool), ``reason``, ``delta``, and the sovereign
    action/confidence for downstream audit.

    Fail-safe: any read error returns the production confidence unchanged
    with ``reason="no_contribution"`` — the production path is never
    blocked by a Sovereign outage.
    """
    require_prd()

    promotion_state: dict[str, Any] | None = None
    sovereign_decision: dict[str, Any] | None = None

    try:
        status = await compute_sovereign_promotion_status(db, asset_type)  # type: ignore[arg-type]
        promotion_state = {"promoted": bool(status.get("promoted", False)), **status}
    except Exception:  # noqa: BLE001
        promotion_state = {"promoted": False}

    try:
        cur = db["sovereign_decisions"].find(
            {"symbol": symbol.upper(), "asset_type": asset_type},
            {"_id": 0},
        ).sort("created_at", -1).limit(1)
        rows = await cur.to_list(length=1)
        sovereign_decision = rows[0] if rows else None
    except Exception:  # noqa: BLE001
        sovereign_decision = None

    return apply_sovereign_contribution(
        production_confidence=production_confidence,
        production_action=production_action,
        sovereign_decision=sovereign_decision,
        promotion_state=promotion_state,
    )


def assert_prd_never_trains() -> bool:
    """Defensive guard — importable and call-able by PRD startup code to
    prove that training-capable code paths can't reach from PRD entry
    points. Wraps ``require_prd()`` so the call itself fails if mode drift
    happens at deploy time.
    """
    require_prd()
    return True
