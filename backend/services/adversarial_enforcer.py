"""
RISEDUAL Patent K — Structured Adversarial Enforcement

Purpose:
    Converts Bull/Bear/Commander disagreement into a disciplined decision.

Core invariants:
    1. Low dissent means low conviction.
    2. Contradictory high-confidence agents force HOLD unless Commander override is valid.
    3. HOLD cannot be promoted into BUY/SELL by this layer.
    4. The enforcer emits structured reasons for audit/proof-chain logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


Action = Literal["BUY", "SELL", "HOLD"]


class EnforcementReason(str, Enum):
    LOW_DISSENT_NO_CONVICTION = "low_dissent_no_conviction"
    HIGH_CONFLICT_FORCE_HOLD = "high_conflict_force_hold"
    COMMANDER_OVERRIDE_ACCEPTED = "commander_override_accepted"
    BULL_WINS = "bull_wins"
    BEAR_WINS = "bear_wins"
    BOTH_HOLD = "both_hold"
    INVALID_CONFIDENCE = "invalid_confidence"
    HOLD_NOT_PROMOTED = "hold_not_promoted"


@dataclass(frozen=True)
class AgentDecision:
    name: str
    action: Action
    confidence: float
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommanderDecision:
    action: Action
    confidence: float
    override: bool = False
    reason: str = ""
    signature_hash: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdversarialEnforcementConfig:
    min_dissent: float = 0.15
    high_conflict_confidence: float = 0.70
    commander_override_min_confidence: float = 0.75
    require_commander_signature_for_override: bool = True


@dataclass(frozen=True)
class AdversarialEnforcementResult:
    action: Action
    confidence: float
    dissent_score: float
    allowed_to_trade: bool
    reason: EnforcementReason
    bull: AgentDecision
    bear: AgentDecision
    commander: Optional[CommanderDecision]
    metadata: dict[str, Any]


def _valid_confidence(value: float) -> bool:
    return 0.0 <= value <= 1.0


def _normalize_action(action: str) -> Action:
    value = action.upper()
    if value not in {"BUY", "SELL", "HOLD"}:
        return "HOLD"
    return value  # type: ignore[return-value]


def _commander_override_valid(
    commander: Optional[CommanderDecision],
    config: AdversarialEnforcementConfig,
) -> bool:
    if commander is None:
        return False

    if not commander.override:
        return False

    if commander.action == "HOLD":
        return True

    if commander.confidence < config.commander_override_min_confidence:
        return False

    if config.require_commander_signature_for_override and not commander.signature_hash:
        return False

    return True


def enforce_adversarial_decision(
    bull: AgentDecision,
    bear: AgentDecision,
    commander: Optional[CommanderDecision] = None,
    config: Optional[AdversarialEnforcementConfig] = None,
) -> AdversarialEnforcementResult:
    config = config or AdversarialEnforcementConfig()

    bull_action = _normalize_action(bull.action)
    bear_action = _normalize_action(bear.action)

    if not _valid_confidence(bull.confidence) or not _valid_confidence(bear.confidence):
        return AdversarialEnforcementResult(
            action="HOLD",
            confidence=0.0,
            dissent_score=0.0,
            allowed_to_trade=False,
            reason=EnforcementReason.INVALID_CONFIDENCE,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={"error": "confidence_out_of_range"},
        )

    dissent_score = abs(bull.confidence - bear.confidence)

    if bull_action == "HOLD" and bear_action == "HOLD":
        return AdversarialEnforcementResult(
            action="HOLD",
            confidence=max(bull.confidence, bear.confidence),
            dissent_score=dissent_score,
            allowed_to_trade=False,
            reason=EnforcementReason.BOTH_HOLD,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={},
        )

    if _commander_override_valid(commander, config):
        action = _normalize_action(commander.action)  # type: ignore[union-attr]
        return AdversarialEnforcementResult(
            action=action,
            confidence=commander.confidence,  # type: ignore[union-attr]
            dissent_score=dissent_score,
            allowed_to_trade=action != "HOLD",
            reason=EnforcementReason.COMMANDER_OVERRIDE_ACCEPTED,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={"commander_reason": commander.reason if commander else ""},
        )

    opposite_trade_actions = {bull_action, bear_action} == {"BUY", "SELL"}
    both_high_confidence = (
        bull.confidence >= config.high_conflict_confidence
        and bear.confidence >= config.high_conflict_confidence
    )

    if opposite_trade_actions and both_high_confidence:
        return AdversarialEnforcementResult(
            action="HOLD",
            confidence=min(bull.confidence, bear.confidence),
            dissent_score=dissent_score,
            allowed_to_trade=False,
            reason=EnforcementReason.HIGH_CONFLICT_FORCE_HOLD,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={"conflict": "opposite_high_confidence_trade_actions"},
        )

    if dissent_score < config.min_dissent:
        return AdversarialEnforcementResult(
            action="HOLD",
            confidence=max(bull.confidence, bear.confidence),
            dissent_score=dissent_score,
            allowed_to_trade=False,
            reason=EnforcementReason.LOW_DISSENT_NO_CONVICTION,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={"min_dissent": config.min_dissent},
        )

    if bull.confidence > bear.confidence:
        final_action = bull_action
        reason = EnforcementReason.BULL_WINS
        confidence = bull.confidence
    else:
        final_action = bear_action
        reason = EnforcementReason.BEAR_WINS
        confidence = bear.confidence

    if final_action == "HOLD":
        return AdversarialEnforcementResult(
            action="HOLD",
            confidence=confidence,
            dissent_score=dissent_score,
            allowed_to_trade=False,
            reason=EnforcementReason.HOLD_NOT_PROMOTED,
            bull=bull,
            bear=bear,
            commander=commander,
            metadata={},
        )

    return AdversarialEnforcementResult(
        action=final_action,
        confidence=confidence,
        dissent_score=dissent_score,
        allowed_to_trade=True,
        reason=reason,
        bull=bull,
        bear=bear,
        commander=commander,
        metadata={},
    )
