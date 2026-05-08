"""Phase 5b — broker wire with 4-gate defense in depth.

The wire connects the executor lane MLs to a real broker call path,
but FOUR independent gates must ALL be satisfied before any live
order is placed. All default to closed/false:

  Gate 1 — Per-lane enforce flag
           ``EQUITY_EXECUTOR_ENFORCE_ENABLED``   default=false
           ``CRYPTO_EXECUTOR_ENFORCE_ENABLED``   default=false

  Gate 2 — Global broker kill-switch
           ``BROKER_LIVE_ORDER_ENABLED``         default=false

  Gate 3 — Live-execution opt-in (legacy)
           ``RISEDUAL_LIVE_EXECUTION=1``         default=unset

  Gate 4 — Calibration Kanban readiness
           Lane must be ``Eligible`` (not Shadow / Calibrate /
           Ready-for-Review). The Kanban reads receipts, RG counts,
           false-block markers etc., so this is a derived gate that
           encodes the user's "let receipts prove first" rule.

Every signal — even when no broker call fires — produces ONE
``phase5b_intents`` doc capturing:

  * Pipeline + RG verdicts
  * Status of each gate
  * ``classification`` ∈ {SHADOW_ONLY, GATE_BLOCK, WOULD_HAVE_FIRED, FIRED}
  * Optional broker order id when FIRED

This is the auditable trail the user reviews before flipping the env
flags. NEVER raises — wire failures degrade to no-op.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.ml.contracts import Verdict

logger = logging.getLogger(__name__)


# ── Output shape ──────────────────────────────────────────────────


@dataclass
class BrokerWireResult:
    """Per-signal report from the wire. Persisted to
    ``phase5b_intents`` and returned to the caller.
    """
    lane: str
    symbol: str
    side: str
    requested_notional_usd: float
    classification: str          # SHADOW_ONLY | GATE_BLOCK | WOULD_HAVE_FIRED | FIRED
    fired: bool
    order_id: Optional[str]
    gates: Dict[str, Any]
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Gate readers ─────────────────────────────────────────────────


def _enforce_flag_for(lane: str) -> bool:
    """Per-lane executor enforce flag (Gate 1)."""
    var = (
        "EQUITY_EXECUTOR_ENFORCE_ENABLED" if lane == "equity"
        else "CRYPTO_EXECUTOR_ENFORCE_ENABLED" if lane == "crypto"
        else None
    )
    if var is None:
        return False
    return os.getenv(var, "false").lower() == "true"


def _broker_live_order_flag() -> bool:
    """Global broker kill-switch (Gate 2)."""
    return os.getenv("BROKER_LIVE_ORDER_ENABLED", "false").lower() == "true"


def _legacy_live_execution_flag() -> bool:
    """Legacy live-execution opt-in (Gate 3)."""
    return os.getenv("RISEDUAL_LIVE_EXECUTION", "") == "1"


async def _kanban_eligible(db, lane: str) -> tuple[bool, Optional[str]]:
    """Gate 4 — read the Kanban and confirm lane is ``Eligible``.

    Returns ``(eligible, current_state)``. If the Kanban call
    fails for any reason, defaults to NOT eligible (fail-safe).
    """
    try:
        from services.calibration_kanban import get_kanban
        snapshot = await get_kanban(db)
        card = (snapshot.get("lanes") or {}).get(lane) or {}
        state = card.get("promotion_state")
        return (state == "Eligible", state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[broker_wire] kanban read failed (fail-safe): %s", exc)
        return (False, None)


# ── Broker dispatch (intentionally not implemented yet) ──────────


async def _dispatch_to_broker(
    db,
    *,
    lane: str,
    symbol: str,
    side: str,
    notional_usd: float,
) -> Optional[str]:
    """Real broker call entrypoint.

    Phase 5b ships this function as a NO-OP body. The four gates
    above already prevent any caller from reaching this code in a
    default install. When the operator is ready to go live, they
    will:

      1. Replace this body with the appropriate broker SDK call
         (Alpaca for equity, Kraken for crypto), routed through
         existing code in ``services/ml_alpaca_broker.py`` etc.
      2. Flip the four env gates one at a time.
      3. Watch ``phase5b_intents`` and the Kanban for behaviour.

    Returning ``None`` from the placeholder is intentional — it means
    the wire did NOT fire even when all gates approved. The caller's
    classification logic treats this as ``WOULD_HAVE_FIRED`` (the
    intent is recorded but no broker contacted).
    """
    logger.info(
        "[broker_wire] dispatch placeholder hit — lane=%s symbol=%s side=%s notional=%.2f",
        lane, symbol, side, notional_usd,
    )
    return None  # placeholder body — no broker SDK call yet


# ── Public entry ─────────────────────────────────────────────────


async def run_broker_wire(
    db,
    *,
    lane: str,
    symbol: str,
    side: str,
    notional_usd: float,
    pipeline_decision,                       # PipelineDecision
    rg_verdict,                              # RoadGuardVerdict | None
) -> Optional[BrokerWireResult]:
    """Phase 5b dispatcher. Always returns a BrokerWireResult on success
    and persists ``phase5b_intents``. NEVER raises.
    """
    try:
        from services.ml.contracts import Verdict as _V

        # --- Compute upstream-pass / RG-pass ------------------
        pipeline_pass = (
            pipeline_decision.blocked_at is None
            and pipeline_decision.final.decision in (_V.BUY.value, _V.SELL.value)
        )
        rg_pass = (
            rg_verdict is not None
            and rg_verdict.decision == "PASS"
        )
        upstream_clear = pipeline_pass and rg_pass

        # --- Read all 4 gates ---------------------------------
        gate1_enforce = _enforce_flag_for(lane)
        gate2_broker_live = _broker_live_order_flag()
        gate3_legacy = _legacy_live_execution_flag()
        gate4_eligible, gate4_state = await _kanban_eligible(db, lane)

        gates = {
            "gate1_enforce_flag": gate1_enforce,
            "gate2_broker_live_order_enabled": gate2_broker_live,
            "gate3_legacy_live_execution": gate3_legacy,
            "gate4_kanban_eligible": gate4_eligible,
            "gate4_kanban_state": gate4_state,
        }

        all_gates_open = (
            upstream_clear
            and gate1_enforce
            and gate2_broker_live
            and gate3_legacy
            and gate4_eligible
        )

        # --- Classify ----------------------------------------
        order_id: Optional[str] = None
        if not upstream_clear:
            classification = "SHADOW_ONLY"   # pipeline or RG already blocked
        elif all_gates_open:
            # All 4 gates open — attempt broker dispatch.
            order_id = await _dispatch_to_broker(
                db, lane=lane, symbol=symbol, side=side,
                notional_usd=notional_usd,
            )
            classification = "FIRED" if order_id else "WOULD_HAVE_FIRED"
        else:
            # Upstream clear but at least one gate is closed —
            # this would have fired if the operator had promoted.
            classification = "GATE_BLOCK"

        result = BrokerWireResult(
            lane=lane,
            symbol=symbol,
            side=side,
            requested_notional_usd=float(notional_usd),
            classification=classification,
            fired=order_id is not None,
            order_id=order_id,
            gates=gates,
            diagnostics={
                "pipeline_pass": pipeline_pass,
                "rg_pass": rg_pass,
                "pipeline_blocked_at": pipeline_decision.blocked_at,
                "pipeline_final_decision": pipeline_decision.final.decision,
                "pipeline_reason": pipeline_decision.final.reason,
                "rg_decision": rg_verdict.decision if rg_verdict else None,
                "rg_gate": rg_verdict.gate if rg_verdict else None,
                "rg_reason": rg_verdict.reason if rg_verdict else None,
            },
        )

        # --- Persist ------------------------------------------
        if db is not None:
            try:
                await db["phase5b_intents"].insert_one({
                    "created_at": datetime.now(timezone.utc),
                    **result.as_dict(),
                    "schema_version": 1,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[broker_wire] phase5b_intents log insert failed: %s", exc,
                )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("[broker_wire] failed (degrading): %s", exc)
        return None


# ── Index helper ─────────────────────────────────────────────────


async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        coll = db["phase5b_intents"]
        await coll.create_index(
            "created_at",
            expireAfterSeconds=30 * 24 * 3600,
            name="phase5b_intents_ttl",
        )
        await coll.create_index(
            [("lane", 1), ("classification", 1), ("created_at", -1)],
            name="phase5b_intents_lane_class_ts",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[broker_wire] ensure_indexes failed: %s", exc)
