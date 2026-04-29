"""Council Risk Modulator — bounded second-engine influence on
Commander's emitted ``risk_multiplier``.

Architecture (Option 3, hierarchical with bounded modulator)
------------------------------------------------------------
Adversarial Commander owns the fill decision. Council acts as a
**risk modulator** — it can scale Commander's risk_multiplier within
hard bounds, but it can NEVER:

    1. Change direction (LONG ↔ SHORT)
    2. Promote a HOLD into a trade
    3. Increase risk above 1.25× the Commander's emitted multiplier
    4. Reduce risk below 0.5× the Commander's emitted multiplier

Modulation table
----------------
Inputs: Commander action + risk_multiplier, Council action + confidence,
``council_tier_open`` boolean (from :mod:`services.council_tier_gate`).

    | Council says     | Modulation              | Floor / ceiling |
    |------------------|-------------------------|-----------------|
    | (modulator off)  | no change               | —               |
    | (tier closed)    | no change               | —               |
    | Commander HOLD   | no change               | — (cannot promote) |
    | Same direction   | × 1.10                  | cap at 1.25     |
    | Opposite + ≥0.7  | × 0.50                  | floor at 0.50   |
    | Opposite + <0.7  | no change               | —               |
    | HOLD vs LONG     | no change               | — (see note)    |

The "Council HOLD vs Commander LONG" case is intentionally a no-op:
Commander's HOLD-to-LONG promotion is already double-gated by the
existing Adversarial phase logic, and adding a Council voice there
would double-count. If real-world data later shows Council is right
about "this setup isn't there" calls, we can introduce a small
downweight (e.g., 0.85×) — but that's a v2 tuning, not a v1 bound.

Failure modes
-------------
Pure function. Cannot raise. Worst case Council goes haywire: risk
caps at 0.5× the Commander's signal, never zero, never opposite
direction. The hard bounds are pinned in code, not env, so an
operator-induced misconfiguration can't unlock them.

DTD-tagged: this module is part of the decision-time stack.
"""
from __future__ import annotations

__domain__ = "DTD"

import os
from typing import Any, Dict, Optional


# Activation flag — second gate alongside the data-driven
# council_tier_open. Both must be true for any modulation to apply.
COUNCIL_RISK_MODULATOR_ENABLED: bool = (
    os.getenv("COUNCIL_RISK_MODULATOR_ENABLED", "false").lower() == "true"
)


# Hard bounds — pinned in code, not env. An operator who wants to
# loosen them must do it through code review.
MAX_COUNCIL_UPWEIGHT: float = 1.25
AGREEMENT_UPWEIGHT: float = 1.10
HIGH_CONFIDENCE: float = 0.70
OPPOSITE_DISAGREE_DOWNWEIGHT: float = 0.50
MIN_COUNCIL_DOWNWEIGHT_FLOOR: float = 0.50


# Action canonicalisation — Adversarial Commander emits
# {LONG, SHORT_OR_AVOID, HOLD}; Council emits {LONG, SHORT, HOLD};
# the modulator works in {BUY, SELL, HOLD} space. Map both
# direction-emitting variants of "negative" onto SELL so the
# is_opposite check works regardless of source engine.
_ACTION_MAP: Dict[Optional[str], str] = {
    "LONG": "BUY",
    "BUY": "BUY",
    "buy": "BUY",
    "SHORT": "SELL",
    "SHORT_OR_AVOID": "SELL",
    "SELL": "SELL",
    "sell": "SELL",
    "NO_TRADE": "HOLD",
    "HOLD": "HOLD",
    "hold": "HOLD",
    "WAIT": "HOLD",
    "wait": "HOLD",
    None: "HOLD",
}


def normalize_action(action: Optional[str]) -> str:
    """Map any engine's action string onto {BUY, SELL, HOLD}.
    Unknown values fall through to HOLD — defensively safe (HOLD
    can't trigger any modulation, ever)."""
    if action is None:
        return "HOLD"
    return _ACTION_MAP.get(action, _ACTION_MAP.get(action.upper(), "HOLD"))


def is_opposite(a: str, b: str) -> bool:
    """True iff the two actions are direction-opposite (one BUY,
    one SELL). HOLD vs anything is NOT opposite — that case
    intentionally falls through to no-op modulation."""
    return {a, b} == {"BUY", "SELL"}


def apply_council_risk_modulation(
    *,
    commander_action: str,
    commander_risk_multiplier: float,
    council_action: str,
    council_confidence: float,
    council_tier_open: bool,
) -> Dict[str, Any]:
    """Apply the modulation table. Pure function — no I/O, no
    exceptions. Returns a result dict with the new
    ``risk_multiplier``, a ``council_applied`` flag for the operator
    drawer, and a short ``reason`` tag for log-grep + analytics.

    The result dict shape is fixed regardless of modulation outcome
    so downstream consumers can rely on key presence.

    Optional bridge calibration: if ``bridge_v1_council_calibration``
    is active, its value multiplies the post-modulation risk_multiplier
    within the bridge's hard-clamped [0.90, 1.10] bounds. The bridge
    contributes a finer nudge ON TOP of the Council's existing
    [0.50, 1.25] bounds — it never overrides the modulator's
    fundamental decision (agree/disagree/HOLD logic is untouched).
    """
    # Late import to avoid circular dependency between Council (DTD)
    # and the BRIDGE-tagged promotion_bridge module at module-load time.
    try:
        from services.promotion_bridge import get_calibration as _bridge_get
    except ImportError:  # pragma: no cover — defensive
        _bridge_get = None

    base_rm = max(float(commander_risk_multiplier or 0.0), 0.0)

    def _apply_bridge(out: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the optional bridge nudge AFTER the Council's own
        modulation. ``None`` from the bridge = no calibration active,
        return ``out`` unchanged (the safe default per spec §5)."""
        if _bridge_get is None:
            return out
        cal = _bridge_get("bridge_v1_council_calibration")
        if cal is None:
            return out
        rm = float(out.get("risk_multiplier") or 0.0)
        # Bridge bounds are [0.90, 1.10] — already enforced inside
        # promotion_bridge.get_calibration() but defence-in-depth.
        cal_clamped = max(0.90, min(1.10, cal))
        new_rm = rm * cal_clamped
        out = {**out, "risk_multiplier": new_rm, "bridge_calibration": cal_clamped}
        return out

    if not COUNCIL_RISK_MODULATOR_ENABLED:
        return _apply_bridge({
            "risk_multiplier": base_rm,
            "council_applied": False,
            "reason": "council_modulator_disabled",
        })

    if not council_tier_open:
        return _apply_bridge({
            "risk_multiplier": base_rm,
            "council_applied": False,
            "reason": "council_tier_not_open",
        })

    commander = normalize_action(commander_action)
    council = normalize_action(council_action)
    confidence = float(council_confidence or 0.0)

    # Council may never promote HOLD into a trade. This is the
    # firmest of the four hard rules — Commander's HOLD-to-LONG
    # logic is owned exclusively by the Adversarial phase progression.
    if commander == "HOLD":
        return _apply_bridge({
            "risk_multiplier": base_rm,
            "council_applied": False,
            "reason": "commander_hold_not_promoted",
        })

    # Agreement: small bounded upweight. Capped at 1.25× regardless
    # of how high Commander's base multiplier already was.
    if commander == council:
        return _apply_bridge({
            "risk_multiplier": min(
                base_rm * AGREEMENT_UPWEIGHT, MAX_COUNCIL_UPWEIGHT,
            ),
            "council_applied": True,
            "reason": "council_agreed_small_upweight",
        })

    # High-confidence opposite disagreement: bounded downweight.
    # The 0.5× floor protects against runaway modulator behaviour.
    if is_opposite(commander, council) and confidence >= HIGH_CONFIDENCE:
        return _apply_bridge({
            "risk_multiplier": max(
                base_rm * OPPOSITE_DISAGREE_DOWNWEIGHT,
                MIN_COUNCIL_DOWNWEIGHT_FLOOR,
            ),
            "council_applied": True,
            "reason": "council_high_confidence_disagreement_downweight",
        })

    # Everything else: low-confidence disagreement, or HOLD-vs-direction
    # disagreement. Observe-only — log it as a near-miss but don't
    # change the multiplier.
    return _apply_bridge({
        "risk_multiplier": base_rm,
        "council_applied": False,
        "reason": "council_disagreement_low_confidence_no_change",
    })
