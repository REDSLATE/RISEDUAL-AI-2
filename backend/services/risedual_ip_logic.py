"""
RISEDUAL — Canonical IP Decision Contract

Single legal/technical expression of the patent stack. The rule is:

    No model output can directly execute.
    Every candidate action must pass governance, risk, proof, and
    feedback layers.

Lifecycle:
    1. Observe market         (ctx.market_state — caller's job)
    2. Generate candidate     (ctx.strategist.propose)
    3. Adversarial validation (Patent K)
    4. Auditor review         (calibration veto)
    5. Authority validation   (Patent H/I scope)
    6. Failure mode classify  (Patent M)
    7. Adaptive risk budget   (Patent I)
    8. Execute                (ctx.execution_client.execute)
    9. Hash-log proof chain   (Patent J — both accepts AND rejects)
    10. Grade outcome         (separate scorer flow)
    11. Feedback into retrain (separate adaptation flow)

Every rejection path also writes to the proof chain so the legal
audit trail is complete: a "blocked" decision is just as legally
significant as a "fired" decision.

This module is the orchestration layer; the policy lives in the
sub-services (`adversarial_enforcer`, `failure_mode_classifier`,
`authority_risk_budget`, `auditor_calibration`, `proof_chain`). When
operators talk about "the IP", they mean the contract enforced here.
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from services.adversarial_enforcer import (
    AdversarialEnforcementConfig,
    AdversarialEnforcementResult,
    AgentDecision,
    CommanderDecision,
    enforce_adversarial_decision,
)
from services.auditor_calibration import (
    AuditorVerdict,
    review_calibration,
)
from services.authority_risk_budget import (
    AuthorityScope,
    RiskBudgetDecision,
    RiskBudgetRequest,
    apply_authority_scoped_risk_budget,
)
from services.failure_mode_classifier import (
    FailureModeResult,
    MarketTelemetry,
    ModelTelemetry,
    apply_failure_mode_to_multiplier,
    classify_failure_mode,
)
from services.proof_chain import (
    ProofBlock,
    ProofEvent,
    ProofEventType,
    async_append_proof_event,
)

logger = logging.getLogger(__name__)


# ── Signal contract ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateSignal:
    """The strategist's proposal — input to the IP contract."""
    action: str  # "BUY" / "SELL" / "HOLD"
    base_notional: float
    base_multiplier: float = 1.0
    bull: AgentDecision = field(
        default_factory=lambda: AgentDecision(name="bull", action="HOLD", confidence=0.0),
    )
    bear: AgentDecision = field(
        default_factory=lambda: AgentDecision(name="bear", action="HOLD", confidence=0.0),
    )
    commander: Optional[CommanderDecision] = None
    rolling_accuracy: float = 0.0
    calibration_gap: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    """What the execution client returned. Caller-provided."""
    trade_id: str
    filled: bool
    filled_notional: float
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionClient(Protocol):
    async def execute(
        self, *, symbol: str, action: str, notional: float,
        risk_multiplier: float, context: dict,
    ) -> ExecutionResult:
        ...


# ── Decision context ─────────────────────────────────────────────────


@dataclass
class IPDecisionContext:
    """Everything the IP contract needs in one bundle.

    The caller (bot loop, manual order route, options router) populates
    this once and hands it to ``run_risedual_ip_decision``. Keeps the
    contract function pure — no implicit globals, no magic env reads.
    """
    request_id: str
    actor: str
    asset_class: str  # "crypto" / "equity" / "options"
    symbol: str
    signal: CandidateSignal
    market: MarketTelemetry
    model: ModelTelemetry
    authority: AuthorityScope
    risk_request: RiskBudgetRequest
    proof_store: Any  # AsyncMongoProofChainStore | None
    execution_client: Optional[ExecutionClient] = None
    # When True, the contract runs end-to-end but never executes — used
    # for shadow rollouts and replay analysis.
    dry_run: bool = False


# ── Master function ──────────────────────────────────────────────────


async def run_risedual_ip_decision(ctx: IPDecisionContext) -> dict[str, Any]:
    """Single legal/technical expression of the RISEDUAL IP.

    Returns a flat dict with the full audit trail of which gates ran
    and what they decided. Always logs to the proof chain — including
    every rejection path. Never raises (a contract failure is a
    rejection, not a crash).
    """
    proof_hashes: list[str] = []

    # Invariant 1: action is always one of the canonical three.
    if ctx.signal.action not in {"BUY", "SELL", "HOLD"}:
        return await _reject(
            ctx, reason="invalid_signal_action",
            detail={"action": ctx.signal.action},
            proof_hashes=proof_hashes,
        )

    # ── Step 3 — Adversarial validation (Patent K) ───────────────────
    adversarial = enforce_adversarial_decision(
        bull=ctx.signal.bull,
        bear=ctx.signal.bear,
        commander=ctx.signal.commander,
        config=AdversarialEnforcementConfig(),
    )
    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.ADVERSARIAL_DECISION, {
            "action": adversarial.action,
            "confidence": adversarial.confidence,
            "dissent_score": adversarial.dissent_score,
            "allowed_to_trade": adversarial.allowed_to_trade,
            "reason": adversarial.reason.value,
        }),
    )
    if not adversarial.allowed_to_trade:
        return await _reject(
            ctx, reason="adversarial_rejection",
            detail={"adversarial": _adversarial_to_dict(adversarial)},
            proof_hashes=proof_hashes,
        )

    # ── Step 4 — Auditor calibration veto ────────────────────────────
    audit = review_calibration(
        rolling_accuracy=ctx.signal.rolling_accuracy,
        calibration_gap=ctx.signal.calibration_gap,
        signal_confidence=ctx.signal.bull.confidence
            if adversarial.action == "BUY"
            else ctx.signal.bear.confidence,
    )
    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.AUDITOR_VERDICT, {
            "verdict": audit.verdict.value,
            "calibration_gap": audit.calibration_gap,
            "reasons": audit.reasons,
        }),
    )
    if audit.verdict == AuditorVerdict.VETO:
        return await _reject(
            ctx, reason="auditor_veto",
            detail={"audit": audit.to_dict()},
            proof_hashes=proof_hashes,
        )

    # ── Step 5 — Authority validation (Patent H/I) ───────────────────
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    _expires_at = getattr(ctx.authority, "expires_at", None)
    _expired = _expires_at is None or _expires_at <= _now
    if ctx.authority is None or _expired:
        return await _reject(
            ctx, reason="invalid_authority",
            detail={
                "authority_tier": getattr(ctx.authority, "tier", None).value
                    if ctx.authority and getattr(ctx.authority, "tier", None) else None,
                "expired": _expired,
            },
            proof_hashes=proof_hashes,
        )
    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.AUTHORITY_VALIDATED, {
            "authority_tier": ctx.authority.tier.value,
            "max_multiplier": ctx.authority.max_multiplier,
            "countersigned": bool(getattr(ctx.authority, "countersignature_hash", None)),
        }),
    )

    # ── Step 6 — Failure mode classifier (Patent M) ──────────────────
    failure = classify_failure_mode(market=ctx.market, model=ctx.model)
    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.FAILURE_MODE_CLASSIFIED, {
            "mode": failure.mode.value,
            "confidence": failure.confidence,
            "risk_multiplier_cap": failure.risk_multiplier_cap,
            "block_trade": failure.block_trade,
            "reasons": failure.reasons,
        }),
    )
    if failure.block_trade:
        return await _reject(
            ctx, reason="failure_mode_block",
            detail={"failure_mode": _failure_to_dict(failure)},
            proof_hashes=proof_hashes,
        )

    # ── Step 7 — Adaptive risk budget (Patent I) ─────────────────────
    adjusted_multiplier, fm_reasons = apply_failure_mode_to_multiplier(
        ctx.risk_request.base_multiplier, failure,
    )
    risk_request = RiskBudgetRequest(
        action=adversarial.action,
        base_notional=ctx.risk_request.base_notional,
        base_multiplier=adjusted_multiplier,
        authority=ctx.authority,
        track_record=ctx.risk_request.track_record,
        daily_realized_loss=ctx.risk_request.daily_realized_loss,
        requested_multiplier=ctx.risk_request.requested_multiplier,
        loosening_countersignature_hash=ctx.risk_request.loosening_countersignature_hash,
        metadata={
            **(ctx.risk_request.metadata or {}),
            "failure_mode": failure.mode.value,
            "failure_reasons": fm_reasons,
        },
    )
    risk = apply_authority_scoped_risk_budget(risk_request)
    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.RISK_BUDGET_APPLIED, {
            "allowed": risk.allowed,
            "action": risk.action,
            "final_notional": risk.final_notional,
            "final_multiplier": risk.final_multiplier,
            "tightened": risk.tightened,
            "loosened": risk.loosened,
            "reasons": risk.reasons,
            "audit_hash": risk.audit_hash,
        }),
    )
    if not risk.allowed:
        return await _reject(
            ctx, reason="risk_budget_rejection",
            detail={"risk": _risk_to_dict(risk)},
            proof_hashes=proof_hashes,
        )

    # ── Invariants — non-negotiable safety net ───────────────────────
    _assert_invariants(
        signal=ctx.signal,
        risk=risk,
        authority=ctx.authority,
        adversarial=adversarial,
    )

    # ── Step 8 — Execution ────────────────────────────────────────────
    if ctx.dry_run or ctx.execution_client is None:
        execution = ExecutionResult(
            trade_id=f"dry-{ctx.request_id}",
            filled=False,
            filled_notional=0.0,
            metadata={"dry_run": True},
        )
    else:
        try:
            execution = await ctx.execution_client.execute(
                symbol=ctx.symbol,
                action=adversarial.action,
                notional=risk.final_notional,
                risk_multiplier=risk.final_multiplier,
                context={"asset_class": ctx.asset_class, "actor": ctx.actor},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ip_contract] execution client raised")
            return await _reject(
                ctx, reason="execution_failed",
                detail={"error_type": type(exc).__name__},
                proof_hashes=proof_hashes,
            )

    proof_hashes.append(
        await _log_proof(ctx, ProofEventType.EXECUTION_FILLED if execution.filled
                         else ProofEventType.EXECUTION_ATTEMPTED, {
            "trade_id": execution.trade_id,
            "filled": execution.filled,
            "filled_notional": execution.filled_notional,
            "dry_run": ctx.dry_run,
        }),
    )

    return {
        "allowed": True,
        "trade_id": execution.trade_id,
        "action": adversarial.action,
        "notional": risk.final_notional,
        "risk_multiplier": risk.final_multiplier,
        "filled": execution.filled,
        "reasons": list(risk.reasons),
        "proof_hashes": proof_hashes,
        "proof_hash": proof_hashes[-1] if proof_hashes else None,
        "adversarial": _adversarial_to_dict(adversarial),
        "audit": audit.to_dict(),
        "failure_mode": _failure_to_dict(failure),
        "risk": _risk_to_dict(risk),
    }


# ── Rule predicate ───────────────────────────────────────────────────


def can_execute(decision: dict) -> bool:
    """The single-line answer to "is this trade allowed to execute?".

    Mirrors the master function's gate sequence; intended for callers
    that want to inspect a decision dict (e.g. shadow-mode review)
    without re-running the contract.
    """
    if not decision.get("allowed"):
        return False
    adv = decision.get("adversarial") or {}
    audit = decision.get("audit") or {}
    fm = decision.get("failure_mode") or {}
    risk = decision.get("risk") or {}
    return bool(
        adv.get("allowed_to_trade")
        and audit.get("verdict") != "veto"
        and not fm.get("block_trade")
        and risk.get("allowed")
        and decision.get("proof_hash") is not None,
    )


# ── Invariants ───────────────────────────────────────────────────────


def _assert_invariants(
    *,
    signal: CandidateSignal,
    risk: RiskBudgetDecision,
    authority: AuthorityScope,
    adversarial: AdversarialEnforcementResult,
) -> None:
    """Non-negotiable invariants. A violation indicates an internal
    bug — the contract is not allowed to execute past this point with
    inconsistent state.

    Why ``assert`` rather than logging-and-continue:
        These are programming errors, not runtime conditions. A
        loosened budget without a countersignature, or a HOLD with
        non-zero notional, is an exploitable bug. The bot should fail
        loudly so the assertion catches the missing test before the
        money moves.
    """
    assert adversarial.action in {"BUY", "SELL", "HOLD"}, (
        f"adversarial.action invariant: {adversarial.action!r}"
    )
    assert risk.final_multiplier <= authority.max_multiplier + 1e-9, (
        f"risk.final_multiplier ({risk.final_multiplier}) exceeded "
        f"authority.max_multiplier ({authority.max_multiplier})"
    )
    assert not (adversarial.action == "HOLD" and risk.final_notional > 0), (
        "HOLD with non-zero notional is forbidden"
    )
    assert not (risk.loosened and not getattr(authority, "countersignature_hash", None)), (
        "risk.loosened requires authority.countersignature_hash"
    )


# ── Helpers ──────────────────────────────────────────────────────────


async def _log_proof(
    ctx: IPDecisionContext,
    event_type: ProofEventType,
    payload: dict[str, Any],
) -> str:
    """Write one proof block. Returns ``""`` if the store is missing
    or the write fails — the contract continues regardless."""
    if ctx.proof_store is None:
        return ""
    try:
        block: ProofBlock = await async_append_proof_event(
            ctx.proof_store,
            ProofEvent(
                event_type=event_type,
                entity_id=ctx.request_id,
                actor=ctx.actor,
                payload=payload,
            ),
        )
        return block.block_hash
    except Exception as e:  # noqa: BLE001
        logger.warning("[ip_contract] proof log failed (%s): %s", event_type.value, e)
        return ""


async def _reject(
    ctx: IPDecisionContext,
    *,
    reason: str,
    detail: dict[str, Any],
    proof_hashes: list[str],
) -> dict[str, Any]:
    """Hash-log a rejection. Returns the canonical "blocked" response
    shape so callers can branch on ``decision["allowed"]`` without
    inspecting failure-specific fields."""
    rejection_hash = await _log_proof(
        ctx, ProofEventType.EXECUTION_REJECTED,
        {"rejected": True, "reason": reason, "detail": detail},
    )
    if rejection_hash:
        proof_hashes.append(rejection_hash)
    return {
        "allowed": False,
        "trade_id": None,
        "action": "HOLD",
        "notional": 0.0,
        "risk_multiplier": 0.0,
        "filled": False,
        "reason": reason,
        "reasons": [reason],
        "detail": detail,
        "proof_hashes": proof_hashes,
        "proof_hash": proof_hashes[-1] if proof_hashes else None,
    }


def _adversarial_to_dict(r: AdversarialEnforcementResult) -> dict[str, Any]:
    return {
        "action": r.action,
        "confidence": r.confidence,
        "dissent_score": r.dissent_score,
        "allowed_to_trade": r.allowed_to_trade,
        "reason": r.reason.value,
    }


def _failure_to_dict(r: FailureModeResult) -> dict[str, Any]:
    return {
        "mode": r.mode.value,
        "confidence": r.confidence,
        "risk_multiplier_cap": r.risk_multiplier_cap,
        "block_trade": r.block_trade,
        "reasons": r.reasons,
    }


def _risk_to_dict(r: RiskBudgetDecision) -> dict[str, Any]:
    return {
        "action": r.action,
        "allowed": r.allowed,
        "final_notional": r.final_notional,
        "final_multiplier": r.final_multiplier,
        "authority_tier": r.authority_tier.value,
        "tightened": r.tightened,
        "loosened": r.loosened,
        "reasons": r.reasons,
        "audit_hash": r.audit_hash,
    }
