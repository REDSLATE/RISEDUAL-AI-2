"""
RISEDUAL Decision Pipeline Guard

Combines:
    J — Proof Chain
    K — Adversarial Enforcement
    M — Failure Mode Intelligence
    I — Authority Risk Budgeting

This file is the glue layer.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from services.adversarial_enforcer import (
    AgentDecision,
    CommanderDecision,
    AdversarialEnforcementConfig,
    AdversarialEnforcementResult,
    enforce_adversarial_decision,
)
from services.failure_mode_classifier import (
    MarketTelemetry,
    ModelTelemetry,
    FailureModeResult,
    classify_failure_mode,
    apply_failure_mode_to_multiplier,
)
from services.proof_chain import (
    ProofChainStore,
    ProofEvent,
    ProofEventType,
    append_proof_event,
)

from services.authority_risk_budget import (
    RiskBudgetRequest,
    RiskBudgetDecision,
    apply_authority_scoped_risk_budget,
)


def run_guarded_decision_pipeline(
    *,
    entity_id: str,
    bull: AgentDecision,
    bear: AgentDecision,
    commander: Optional[CommanderDecision],
    market: MarketTelemetry,
    model: ModelTelemetry,
    risk_request: RiskBudgetRequest,
    proof_store: Optional[ProofChainStore] = None,
    actor: str = "risedual-ai",
) -> dict[str, Any]:
    """
    One-shot guarded pipeline.

    Returns:
        {
            allow: bool,
            action: BUY/SELL/HOLD,
            notional: float,
            risk_multiplier: float,
            reasons: [...],
            adversarial: {...},
            failure_mode: {...},
            risk_budget: {...},
            proof_hashes: [...]
        }
    """

    proof_hashes: list[str] = []

    adversarial = enforce_adversarial_decision(
        bull=bull,
        bear=bear,
        commander=commander,
        config=AdversarialEnforcementConfig(),
    )

    if proof_store:
        block = append_proof_event(
            proof_store,
            ProofEvent(
                event_type=ProofEventType.ADVERSARIAL_DECISION,
                entity_id=entity_id,
                actor=actor,
                payload={
                    "action": adversarial.action,
                    "confidence": adversarial.confidence,
                    "dissent_score": adversarial.dissent_score,
                    "allowed_to_trade": adversarial.allowed_to_trade,
                    "reason": adversarial.reason.value,
                    "bull": asdict(bull),
                    "bear": asdict(bear),
                    "commander": asdict(commander) if commander else None,
                },
            ),
        )
        proof_hashes.append(block.block_hash)

    if not adversarial.allowed_to_trade:
        return _blocked_response(
            action=adversarial.action,
            reasons=[adversarial.reason.value],
            adversarial=adversarial,
            failure=None,
            risk=None,
            proof_hashes=proof_hashes,
        )

    failure = classify_failure_mode(market=market, model=model)

    if proof_store:
        block = append_proof_event(
            proof_store,
            ProofEvent(
                event_type=ProofEventType.FAILURE_MODE_CLASSIFIED,
                entity_id=entity_id,
                actor=actor,
                payload={
                    "mode": failure.mode.value,
                    "confidence": failure.confidence,
                    "risk_multiplier_cap": failure.risk_multiplier_cap,
                    "block_trade": failure.block_trade,
                    "reasons": failure.reasons,
                    "metadata": failure.metadata,
                },
            ),
        )
        proof_hashes.append(block.block_hash)

    adjusted_multiplier, failure_reasons = apply_failure_mode_to_multiplier(
        risk_request.base_multiplier,
        failure,
    )

    risk_request = RiskBudgetRequest(
        action=adversarial.action,
        base_notional=risk_request.base_notional,
        base_multiplier=adjusted_multiplier,
        authority=risk_request.authority,
        track_record=risk_request.track_record,
        daily_realized_loss=risk_request.daily_realized_loss,
        requested_multiplier=risk_request.requested_multiplier,
        loosening_countersignature_hash=risk_request.loosening_countersignature_hash,
        metadata={
            **risk_request.metadata,
            "failure_mode": failure.mode.value,
            "failure_reasons": failure_reasons,
            "adversarial_reason": adversarial.reason.value,
        },
    )

    risk_decision = apply_authority_scoped_risk_budget(risk_request)

    if proof_store:
        block = append_proof_event(
            proof_store,
            ProofEvent(
                event_type=ProofEventType.RISK_BUDGET_APPLIED,
                entity_id=entity_id,
                actor=actor,
                payload={
                    "allowed": risk_decision.allowed,
                    "action": risk_decision.action,
                    "final_notional": risk_decision.final_notional,
                    "final_multiplier": risk_decision.final_multiplier,
                    "authority_tier": risk_decision.authority_tier.value,
                    "tightened": risk_decision.tightened,
                    "loosened": risk_decision.loosened,
                    "reasons": risk_decision.reasons,
                    "audit_hash": risk_decision.audit_hash,
                },
            ),
        )
        proof_hashes.append(block.block_hash)

    return {
        "allow": risk_decision.allowed,
        "action": risk_decision.action,
        "notional": risk_decision.final_notional,
        "risk_multiplier": risk_decision.final_multiplier,
        "reasons": [
            adversarial.reason.value,
            *failure_reasons,
            *risk_decision.reasons,
        ],
        "adversarial": _adversarial_to_dict(adversarial),
        "failure_mode": _failure_to_dict(failure),
        "risk_budget": _risk_to_dict(risk_decision),
        "proof_hashes": proof_hashes,
    }


def _blocked_response(
    *,
    action: str,
    reasons: list[str],
    adversarial: AdversarialEnforcementResult,
    failure: Optional[FailureModeResult],
    risk: Optional[RiskBudgetDecision],
    proof_hashes: list[str],
) -> dict[str, Any]:
    return {
        "allow": False,
        "action": action,
        "notional": 0.0,
        "risk_multiplier": 0.0,
        "reasons": reasons,
        "adversarial": _adversarial_to_dict(adversarial),
        "failure_mode": _failure_to_dict(failure) if failure else None,
        "risk_budget": _risk_to_dict(risk) if risk else None,
        "proof_hashes": proof_hashes,
    }


def _adversarial_to_dict(result: AdversarialEnforcementResult) -> dict[str, Any]:
    return {
        "action": result.action,
        "confidence": result.confidence,
        "dissent_score": result.dissent_score,
        "allowed_to_trade": result.allowed_to_trade,
        "reason": result.reason.value,
        "metadata": result.metadata,
    }


def _failure_to_dict(result: FailureModeResult) -> dict[str, Any]:
    return {
        "mode": result.mode.value,
        "confidence": result.confidence,
        "risk_multiplier_cap": result.risk_multiplier_cap,
        "block_trade": result.block_trade,
        "reasons": result.reasons,
        "metadata": result.metadata,
    }


def _risk_to_dict(result: RiskBudgetDecision) -> dict[str, Any]:
    return {
        "action": result.action,
        "allowed": result.allowed,
        "final_notional": result.final_notional,
        "final_multiplier": result.final_multiplier,
        "authority_tier": result.authority_tier.value,
        "tightened": result.tightened,
        "loosened": result.loosened,
        "reasons": result.reasons,
        "audit_hash": result.audit_hash,
        "created_at": result.created_at.isoformat(),
    }
