"""Receipt → MC intent payload bridge.

The 2026-05-15 doctrine receipt is emitted by
``services.multi_model_hypothesis_service._weighted_consensus`` with
keys like ``raw_action``, ``would_have_traded_without_gates``,
``individual_weights``, etc. MC's ``IntentIn`` schema accepts a
similar (but not identical) shape: flat per-source weight fields
instead of an ``individual_weights`` dict, integer-percent confidences
flipped to 0-1 unit scale, etc.

This module is the single translation layer between the two.

Why a dedicated module
----------------------
* Keeps the consensus assembly free of MC's wire format.
* Keeps the MC client free of doctrine-receipt knowledge.
* Single place to grep when MC's schema evolves.

Doctrine map
------------
* Council weights (alpha / camaro / chevelle / redeye) → MC's
  5-role weight slots (strategist / auditor / commander / regime /
  memory). The mapping is **not** semantic — it's positional. RISEDUAL
  uses a 4-brain council; MC keeps its 5-slot schema for parity with
  the larger fleet. We map alpha→strategist, camaro→auditor,
  chevelle→commander, redeye→regime. ``memory_weight`` stays at 1.0
  until we wire the local-memory layer in.
"""
from __future__ import annotations

from typing import Any, Mapping


# Fixed positional mapping — see module docstring for rationale.
_BRAIN_TO_ROLE = {
    "alpha":    "strategist_weight",
    "camaro":   "auditor_weight",
    "chevelle": "commander_weight",
    "redeye":   "regime_weight",
}


def _pct_to_unit(value: Any) -> float | None:
    """Convert a 0-100 integer / float confidence to 0-1 unit scale.

    Returns None when the input can't be interpreted as a finite
    number — the caller drops the field rather than ship a noisy
    default that pollutes MC's calibration metrics.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Tolerate either scale on input: if it already looks unit-scale
    # (<= 1.0 and > 0) leave it alone; otherwise treat as percent.
    if 0.0 < v <= 1.0:
        return v
    if 0.0 <= v <= 100.0:
        return v / 100.0
    return None


def consensus_receipt_to_intent_fields(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate a doctrine receipt into MC-schema honesty fields.

    Input
    -----
    ``receipt`` — the dict returned by ``_weighted_consensus``. Keys
    consumed:
        raw_action, raw_confidence,
        final_action / display_action,
        market_decision, execution_decision,
        hold_reason, blocked_by,
        would_have_traded_without_gates,
        pre_weight_confidence, post_weight_confidence,
        council_penalty,
        individual_weights (dict of brain → multiplier).

    Output
    ------
    A dict containing only the honesty fields ready to splat into
    :func:`MCClient.post_intent`. Missing receipt fields are simply
    omitted from the output (matches the optional-field contract of
    the MC schema).
    """
    if not isinstance(receipt, Mapping):
        return {}

    out: dict[str, Any] = {}

    # Action-domain fields — direct passthrough after upper-casing.
    for src, dst in (
        ("raw_action", "raw_action"),
        ("market_decision", "market_decision"),
        ("display_action", "display_action"),
        ("final_action", "display_action"),  # fallback if display_action absent
    ):
        if dst in out:
            continue  # already filled by an earlier alias
        v = receipt.get(src)
        if v is None:
            continue
        out[dst] = str(v).upper()

    if "execution_decision" in receipt and receipt["execution_decision"]:
        out["execution_decision"] = str(receipt["execution_decision"]).upper()

    # Bounded confidences — receipt uses 0-100 percent, MC expects unit.
    for src, dst in (
        ("raw_confidence", "raw_confidence"),
        ("pre_weight_confidence", "pre_weight_confidence"),
        ("post_weight_confidence", "post_weight_confidence"),
    ):
        unit = _pct_to_unit(receipt.get(src))
        if unit is not None:
            out[dst] = unit

    # council_penalty is a signed delta. Receipt emits it as
    # percentage-point delta (e.g. -8.0 meaning -8 pp); MC expects
    # a unit-scale signed delta (-0.08). Same divide-by-100 rule.
    cp = receipt.get("council_penalty")
    if cp is not None:
        try:
            cpf = float(cp)
            if -100.0 <= cpf <= 100.0:
                out["council_penalty"] = cpf / 100.0
        except (TypeError, ValueError):
            pass

    # Per-brain → per-role weight remap.
    weights = receipt.get("individual_weights")
    if isinstance(weights, Mapping):
        for brain_key, role_field in _BRAIN_TO_ROLE.items():
            wv = weights.get(brain_key)
            if wv is None:
                continue
            try:
                out[role_field] = float(wv)
            except (TypeError, ValueError):
                continue
        # memory_weight has no brain analogue today — leave at 1.0
        # ONLY if at least one mapped weight was emitted (otherwise
        # we ship nothing rather than fake a balanced council).
        if any(role in out for role in _BRAIN_TO_ROLE.values()):
            out.setdefault("memory_weight", 1.0)

    # Flat passthroughs — these have no scale conversion.
    if receipt.get("hold_reason") is not None:
        out["hold_reason"] = str(receipt["hold_reason"])
    blocked = receipt.get("blocked_by")
    if isinstance(blocked, (list, tuple)):
        out["blocked_by"] = [str(b) for b in blocked]
    if "would_have_traded_without_gates" in receipt:
        out["would_have_traded_without_gates"] = bool(
            receipt["would_have_traded_without_gates"],
        )

    return out


__all__ = ["consensus_receipt_to_intent_fields"]
