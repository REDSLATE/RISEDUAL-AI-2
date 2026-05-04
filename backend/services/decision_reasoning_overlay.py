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
