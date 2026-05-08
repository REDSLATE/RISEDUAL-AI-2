"""RISEDUAL Learning Core — Phase 3 consumer.

Patent M Phase 3: let Commander/Auditor *consume* the
``learning_core`` shadow field that Phase 2 attaches.

Hard contract (load-bearing for the IP claim — DO NOT relax)
------------------------------------------------------------

The consumer **MAY**:
  * adjust ``payload["confidence"]`` by a bounded delta,
  * dampen ``payload["risk_multiplier"]`` when the core surfaces
    a pre-tell warning (only ever DOWN — never up),
  * attach an audit envelope under ``payload["learning_core_consumed"]``
    so the operator can see exactly what changed.

The consumer **MUST NOT**:
  * change ``payload["decision"]`` (direction is Commander's
    sole authority),
  * promote a HOLD / UNKNOWN / NO_TRADE into a trade,
  * INCREASE the risk multiplier (operator preference: the core
    can only ever recommend less risk, never more),
  * raise an exception (best-effort wrapper in the route handler
    catches anyway, but the consumer itself is exception-free).

These constraints are pinned by ``test_learning_core_consumer.py``;
every relaxation requires deleting an explicit invariant test.

Bounded deltas
--------------

* ``MAX_CONFIDENCE_DELTA = 0.10`` — tighter than the core's own
  ±0.15 cap because here we're consuming the core's *output*
  delta, and a tighter outer bound keeps Phase-3 wiring strictly
  conservative. Operators can dial this up later once the
  shadow-audit window proves calibration.
* ``RISK_DAMPING_ON_PRETELL = 0.15`` — multiplies risk by 0.85.
* ``MIN_RISK_MULTIPLIER_FLOOR = 0.50`` — never push below this
  floor (matches the canonical engine's ``REGIME_MEMORY_MIN_RISK_MULTIPLIER``
  default).

Env flag
--------

``LEARNING_CORE_CONSUME_ENABLED`` (default ``false``). The Phase-2
shadow attachment continues to run even when consume is off — the
operator wants the data flowing for audit either way.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


MAX_CONFIDENCE_DELTA = 0.10
RISK_DAMPING_ON_PRETELL = 0.15
MIN_RISK_MULTIPLIER_FLOOR = 0.50

# Canonical trade-side tokens — anything else means "do not trade",
# and the consumer must keep its hands off the payload.
_TRADE_SIDES = {"LONG", "SHORT"}


def _consume_enabled() -> bool:
    """Read the env flag fresh each call so operator toggles take
    effect without a process restart (matches the kill-switch
    pattern used elsewhere in the adversarial flow)."""
    return os.getenv("LEARNING_CORE_CONSUME_ENABLED", "false").lower() == "true"


def consume_learning_core_into_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply bounded confidence + risk adjustments from the
    ``learning_core`` shadow field. Mutates ``payload`` in place
    and returns it so the caller can chain.

    No-op cases (returns payload unchanged):
      * env flag off
      * ``payload["learning_core"]`` missing or non-dict
      * canonical direction is not LONG/SHORT (HOLD, UNKNOWN, …)
      * ``payload["decision"]`` is missing (we won't blindly
        attach to a decision-less payload)

    Active cases:
      * confidence delta blended with bounded cap
      * risk multiplier dampened only when pre-tell warning present
    """
    if not _consume_enabled():
        return payload

    lc = payload.get("learning_core")
    if not isinstance(lc, dict):
        return payload

    direction = lc.get("direction_canonical")
    if direction not in _TRADE_SIDES:
        # HOLD / UNKNOWN / anything else — never consume.
        return payload

    decision = payload.get("decision")
    if decision is None:
        return payload

    # ── confidence adjustment ────────────────────────────────────
    base_conf = _safe_float(payload.get("confidence"), default=0.0)
    adj_conf = _safe_float(lc.get("adjusted_confidence"), default=base_conf)
    raw_delta = adj_conf - base_conf
    bounded_delta = max(
        -MAX_CONFIDENCE_DELTA,
        min(MAX_CONFIDENCE_DELTA, raw_delta),
    )
    new_conf = max(0.0, min(1.0, base_conf + bounded_delta))
    confidence_delta = new_conf - base_conf

    # ── risk multiplier dampening (one-way, downward only) ──────
    base_rm = _safe_float(payload.get("risk_multiplier"), default=1.0)
    pretell = lc.get("pretell_warning")
    new_rm = base_rm
    if pretell:  # truthy dict / object
        damped = base_rm * (1.0 - RISK_DAMPING_ON_PRETELL)
        # Floor — never sink below the canonical engine's bound,
        # never RAISE above the original (operator: "core can only
        # ever recommend less risk, never more").
        new_rm = max(MIN_RISK_MULTIPLIER_FLOOR, min(base_rm, damped))
    risk_delta = new_rm - base_rm

    # Apply mutations only after both deltas are computed so a
    # mid-flight exception leaves payload internally consistent.
    payload["confidence"] = new_conf
    payload["risk_multiplier"] = new_rm

    # Audit trail — the operator should always be able to see
    # *why* the values moved.
    payload["learning_core_consumed"] = {
        "consumed_at": datetime.now(timezone.utc).isoformat(),
        "confidence_before": base_conf,
        "confidence_after": new_conf,
        "confidence_delta": confidence_delta,
        "risk_multiplier_before": base_rm,
        "risk_multiplier_after": new_rm,
        "risk_multiplier_delta": risk_delta,
        "pretell_warning_present": bool(pretell),
        "direction_canonical": direction,
        "max_confidence_delta": MAX_CONFIDENCE_DELTA,
        "risk_damping_on_pretell": RISK_DAMPING_ON_PRETELL,
        "min_risk_multiplier_floor": MIN_RISK_MULTIPLIER_FLOOR,
    }
    return payload


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Best-effort float coercion. The shadow attachment is
    schema-loose by design (any caller can populate ``payload``);
    we never want a stray ``None`` or ``"NaN"`` string to crash
    the consumer."""
    try:
        f = float(v)
        # NumPy/JSON sometimes hands back NaN — treat as default.
        if f != f:  # NaN check without importing math
            return default
        return f
    except (TypeError, ValueError):
        return default
