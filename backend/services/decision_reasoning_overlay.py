"""
Decision Reasoning Overlay (READ-ONLY)

Adds human + machine-readable reasoning on top of finalized decisions.
DOES NOT influence action, confidence, or sizing.

Safe for production: pure function, no side effects.
"""

from typing import Dict, Any, List


def _safe(v, default=None):
    return v if v is not None else default


def _upper(v):
    return str(v or "").upper()


def _bool(v):
    return bool(v)


def build_reasoning_overlay(decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a reasoning overlay for a finalized decision payload.

    Expected minimal keys in `decision`:
    - symbol
    - action
    - confidence
    - calibrated_confidence (optional)
    - regime (optional)
    - passed_gates (list)
    - failed_gates (list)
    - risk_adjustments (list)
    - commander_shadow (dict, optional)
    """

    symbol = decision.get("symbol")
    action = _upper(decision.get("action"))
    confidence = float(_safe(decision.get("confidence"), 0.0))
    calibrated = float(_safe(decision.get("calibrated_confidence"), confidence))
    regime = _upper(decision.get("regime"))

    passed = decision.get("passed_gates") or []
    failed = decision.get("failed_gates") or []
    adjustments = decision.get("risk_adjustments") or []

    commander = decision.get("commander_shadow") or {}
    commander_action = _upper(commander.get("action"))
    commander_conf = float(_safe(commander.get("confidence"), 0.0))
    commander_auth = _upper(commander.get("authority"))

    reason_codes: List[str] = []

    # --- Bull / Bear classification ---
    is_long = "BUY" in action or "LONG" in action
    is_short = "SELL" in action or "SHORT" in action

    # --- Base reasoning ---
    if is_long:
        reason_codes.append("REGIME_SUPPORTS_LONG")
    elif is_short:
        reason_codes.append("REGIME_SUPPORTS_SHORT")
    else:
        reason_codes.append("NEUTRAL_ACTION")

    # --- Calibration awareness ---
    if calibrated > confidence:
        reason_codes.append("UNDERCONFIDENT_MODEL")
    elif calibrated < confidence:
        reason_codes.append("OVERCONFIDENT_MODEL")

    # --- Gate results ---
    if failed:
        reason_codes.append("FAILED_ONE_OR_MORE_GATES")
    else:
        reason_codes.append("PASSED_ALL_GATES")

    # --- Risk adjustments ---
    for adj in adjustments:
        if "small_sample" in adj:
            reason_codes.append("SMALL_SAMPLE_DISCOUNT")
        if "integrity" in adj:
            reason_codes.append("INTEGRITY_MITIGATION_ACTIVE")

    # --- Commander logic ---
    if commander_action:
        if commander_action != action:
            reason_codes.append("COMMANDER_DISAGREES")
        else:
            reason_codes.append("COMMANDER_AGREES")

        if commander_auth != "ACTIVE":
            reason_codes.append("COMMANDER_NO_AUTHORITY")

    # --- Human-readable summaries ---
    summary_parts = []

    if is_long:
        summary_parts.append("Strategist favored a long position")
    elif is_short:
        summary_parts.append("Strategist favored a short position")
    else:
        summary_parts.append("No directional bias")

    if regime:
        summary_parts.append(f"under {regime} regime")

    if commander_action and commander_action != action:
        summary_parts.append(
            f"while Commander suggested {commander_action} (shadow only)"
        )

    summary = " ".join(summary_parts) + "."

    # --- Bull case ---
    bull_case = "Momentum and model signals supported the selected direction."

    # --- Bear case ---
    bear_case_parts = []

    if "COMMANDER_DISAGREES" in reason_codes:
        bear_case_parts.append("Commander disagreed with the trade")

    if "SMALL_SAMPLE_DISCOUNT" in reason_codes:
        bear_case_parts.append("limited high-confidence sample size")

    if "UNDERCONFIDENT_MODEL" in reason_codes:
        bear_case_parts.append("model confidence may be understated")

    if not bear_case_parts:
        bear_case_parts.append("no major opposing signals detected")

    bear_case = ", ".join(bear_case_parts) + "."

    # --- What would change decision ---
    what_changes = [
        "Commander reaching Tier-3 authority",
        "Regime shift detection",
        "Recent symbol-specific failures",
        "Different confidence calibration outcome",
    ]

    return {
        "summary": summary,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "what_would_change_decision": what_changes,
        "reason_codes": reason_codes,
        "meta": {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "calibrated_confidence": calibrated,
            "commander_action": commander_action,
            "commander_confidence": commander_conf,
        },
    }



# ── Trade-doc adapters ────────────────────────────────────────────
#
# These helpers convert the rich, project-specific trade documents
# into the overlay's expected `decision` shape. They are pure
# functions — they never mutate their inputs and produce no side
# effects. The paper-trade write paths use them to capture the
# gate-trace context (passed_gates / failed_gates /
# risk_adjustments / commander_shadow) at the moment the trade
# fires, then stamp the resulting overlay onto the trade row.


def _normalise_equity_direction(direction: Any) -> str:
    """Map the equity ML signal's ``"up"`` / ``"down"`` vocabulary
    onto the canonical ``"LONG"`` / ``"SHORT"`` tokens the overlay's
    bull/bear classifier reads.

    Pure function. Unknown values pass through unchanged so the
    overlay's defensive ``NEUTRAL_ACTION`` path still triggers.
    """
    if direction is None:
        return ""
    s = str(direction).upper()
    if s == "UP":
        return "LONG"
    if s == "DOWN":
        return "SHORT"
    return s


def build_equity_paper_trade_decision_view(
    trade_doc: Dict[str, Any],
    *,
    snapshot: Any = None,
    patterns: List[str] | None = None,
    dynamic_conf_threshold: float | None = None,
) -> Dict[str, Any]:
    """Adapt an equity ``paper_trades`` row into the overlay shape.

    The row already carries denormalised fields the overlay needs
    (``ticker`` / ``direction`` / ``confidence`` / ``regime``) plus
    the ``commander_phase2_brake`` block when the brake fired and
    ``sovereign_contribution`` / ``symbol_failure_penalty`` blocks
    when those modulators were active.

    Pure — never mutates ``trade_doc``.
    """
    brake = trade_doc.get("commander_phase2_brake") or {}
    sov = trade_doc.get("sovereign_contribution") or {}
    failure = trade_doc.get("symbol_failure_penalty") or {}

    passed_gates: List[str] = []
    failed_gates: List[str] = []
    risk_adjustments: List[str] = []

    # Confidence gate — landed if we reached the trade-row write path
    # at all (the dynamic_confidence threshold was already cleared
    # before the caller decided to fill).
    passed_gates.append("confidence_gate")
    if dynamic_conf_threshold is not None:
        passed_gates.append(
            f"dynamic_confidence_threshold_{dynamic_conf_threshold:.2f}"
        )

    # Pattern gate — landed if patterns is non-empty OR conviction
    # was high enough that the patterns check was skipped.
    if patterns:
        passed_gates.append("pattern_match")

    # Regime gate.
    regime = trade_doc.get("regime")
    if regime:
        passed_gates.append(f"regime_{str(regime).upper()}")

    # Phase-2 brake — when present, the brake EVALUATED the trade.
    # ``decision`` field on the brake_log shows pass/veto/throttle.
    if brake:
        decision = (brake.get("decision") or "").upper()
        if decision in ("VETO", "BLOCK"):
            failed_gates.append("phase2_brake_veto")
        elif decision in ("THROTTLE", "DOWNSIZE"):
            risk_adjustments.append("phase2_brake_throttle")
        else:
            passed_gates.append("phase2_brake_pass")

    # Sovereign contribution — if present + active, it modulated
    # confidence. We surface that as a risk adjustment so the
    # operator can see "this trade rode a sovereign upweight".
    if sov:
        delta = sov.get("delta_confidence")
        if delta is not None:
            try:
                d = float(delta)
                if abs(d) >= 0.001:
                    risk_adjustments.append(
                        f"sovereign_contribution_{d:+.3f}"
                    )
            except (TypeError, ValueError):
                pass

    # Symbol-failure penalty — recent losses on this ticker shrunk
    # the position. Operator wants to see this in the bear case.
    if failure:
        risk_adjustments.append("integrity_mitigation_symbol_failures")

    # Commander shadow — equity brake log persists ``commander_decision``
    # (canonical LONG/SHORT/NO_TRADE) not ``commander_action``. Read
    # both for forward-compat.
    commander_shadow: Dict[str, Any] = {}
    if brake:
        c_action = brake.get("commander_decision") or brake.get("commander_action")
        if c_action:
            commander_shadow = {
                "action": c_action,
                "confidence": brake.get("commander_confidence"),
                # Commander is shadow-only until the
                # ``CRYPTO_ADVERSARIAL_PHASE`` reaches "full" AND
                # the equity pipeline grants it authority — which
                # the brake log captures as ``promotion_phase``.
                "authority": (
                    "ACTIVE"
                    if str(brake.get("promotion_phase", "")).lower() == "full"
                    else "SHADOW_ONLY"
                ),
            }

    view: Dict[str, Any] = {
        "symbol": trade_doc.get("ticker"),
        # Equity paper traders persist ``direction`` as
        # ``"up"`` / ``"down"`` (the ML signal's enum value), but the
        # overlay's bull/bear classifier reads BUY/SELL/LONG/SHORT
        # vocabulary. Normalise here so the overlay sees a canonical
        # token without us muddying its purity. Unknown values pass
        # through and land in NEUTRAL_ACTION — exactly what we want.
        "action": _normalise_equity_direction(trade_doc.get("direction")),
        "confidence": trade_doc.get("confidence"),
        "regime": regime,
        "passed_gates": passed_gates,
        "failed_gates": failed_gates,
        "risk_adjustments": risk_adjustments,
    }
    if commander_shadow:
        view["commander_shadow"] = commander_shadow
    # Calibrated confidence — equity paper trades don't carry this
    # field directly today, but readers downstream might attach
    # ``calibrated_confidence`` to the snapshot before calling.
    if snapshot is not None and hasattr(snapshot, "calibrated_confidence"):
        view["calibrated_confidence"] = getattr(
            snapshot, "calibrated_confidence", None,
        )
    return view


def build_crypto_paper_trade_decision_view(
    trade_doc: Dict[str, Any],
    *,
    signal: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Adapt a ``crypto_paper_trades`` row into the overlay shape.

    The crypto trade row carries an ``adversarial_action`` field
    (``full_trigger`` / ``full_override`` / None) plus the
    ``adversarial_decision_id`` linking back to
    ``crypto_adversarial_decision_log``. The signal kwarg (when
    provided) carries the pre-fill ``commander_action`` /
    ``commander_authority`` fields the closer + log don't store
    inline.

    Pure — never mutates ``trade_doc`` or ``signal``.
    """
    sig = signal or {}
    sov = trade_doc.get("sovereign_contribution") or sig.get("sovereign_contribution") or {}
    failure = trade_doc.get("symbol_failure_penalty") or sig.get("symbol_failure_penalty") or {}

    passed_gates: List[str] = []
    failed_gates: List[str] = []
    risk_adjustments: List[str] = []

    # Strategist + auditor: both must have agreed to land here.
    if trade_doc.get("strategist_conf") is not None:
        passed_gates.append("strategist_confidence")
    if trade_doc.get("auditor_conf") is not None:
        passed_gates.append("auditor_verdict")

    regime = trade_doc.get("regime")
    if regime:
        passed_gates.append(f"regime_{str(regime).upper()}")

    # Adversarial overlay — `adversarial_action` is non-None when
    # Commander participated in the decision.
    adv_action = trade_doc.get("adversarial_action")
    if adv_action:
        if adv_action == "full_override":
            risk_adjustments.append("commander_full_override_active")
        elif adv_action == "full_trigger":
            risk_adjustments.append("commander_full_trigger_active")

    # Sovereign contribution surfaced as a risk adjustment so the
    # operator's bear-case picks it up if the trade later loses.
    if sov:
        delta = sov.get("delta_confidence")
        if delta is not None:
            try:
                d = float(delta)
                if abs(d) >= 0.001:
                    risk_adjustments.append(
                        f"sovereign_contribution_{d:+.3f}"
                    )
            except (TypeError, ValueError):
                pass
    if failure:
        risk_adjustments.append("integrity_mitigation_symbol_failures")

    # Commander shadow on crypto — pulled from the signal payload
    # since the trade row stores only the decision_id, not the
    # commander vote/confidence fields.
    commander_shadow: Dict[str, Any] = {}
    c_action = sig.get("commander_action") or sig.get("adversarial_commander_action")
    if c_action:
        commander_shadow = {
            "action": c_action,
            "confidence": sig.get(
                "commander_confidence",
                sig.get("adversarial_commander_confidence"),
            ),
            "authority": (
                "ACTIVE"
                if adv_action in ("full_trigger", "full_override")
                else "SHADOW_ONLY"
            ),
        }

    view: Dict[str, Any] = {
        "symbol": trade_doc.get("symbol"),
        "action": trade_doc.get("direction"),
        "confidence": trade_doc.get("confidence"),
        "regime": regime,
        "passed_gates": passed_gates,
        "failed_gates": failed_gates,
        "risk_adjustments": risk_adjustments,
    }
    if commander_shadow:
        view["commander_shadow"] = commander_shadow
    return view
