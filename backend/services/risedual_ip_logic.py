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
import os
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


@dataclass(frozen=True)
class EnforcementPolicy:
    """Per-patent enforcement toggles for a staged rollout.

    The default policy enforces every gate. Each ``enforce_*`` flag,
    when ``False``, causes the gate to STILL run (so the proof chain
    captures what would have happened) but its rejection is downgraded
    to a passthrough — the trade continues. This is the staged
    rollout mechanic the operator's playbook calls for:

    Stage 1: J on, K/M/I shadow      → only proof chain is mandatory
    Stage 2: + M (failure-mode hard blocks)
    Stage 3: + K (adversarial enforcement)
    Stage 4: + I (full Patent I risk budgeting)

    Read via ``EnforcementPolicy.from_env()``. Env var names are
    ``PATENT_K_ENFORCE``, ``PATENT_M_ENFORCE``, ``PATENT_I_ENFORCE``,
    ``AUDITOR_ENFORCE``, ``AUTHORITY_ENFORCE``. ``PATENT_J_ENFORCE``
    is absent on purpose: J is the proof chain itself and runs
    unconditionally — it is *what* "log everything" means.
    """
    enforce_adversarial: bool = True   # Patent K
    enforce_auditor: bool = True       # Auditor calibration veto
    enforce_authority: bool = True     # Patent H/I expiry+countersig
    enforce_failure_mode: bool = True  # Patent M block_trade
    enforce_risk_budget: bool = True   # Patent I rejection

    @staticmethod
    def from_env() -> "EnforcementPolicy":
        """Build the policy from env-var defaults + Mongo runtime
        overrides (set via the Guard Shadow admin UI's
        Promote/Demote/Clear actions).

        Env vars are the deploy-time seed; Mongo overrides are the
        runtime-mutated truth. The store's process-local cache (TTL
        30s) keeps this hot path cheap — at most one Mongo read per
        30s per process, regardless of trade frequency.
        """
        def _flag(name: str, default: bool = True) -> bool:
            raw = os.environ.get(name)
            if raw is None or raw == "":
                return default
            return raw.lower() not in ("0", "false", "no", "off")

        # Lazy import — keeps this dataclass importable in unit tests
        # that don't go through service startup.
        try:
            from services.guard_policy_store import get_cached_overrides
            overrides = get_cached_overrides()
        except Exception:  # noqa: BLE001
            overrides = {}

        return EnforcementPolicy(
            enforce_adversarial=overrides.get(
                "enforce_adversarial", _flag("PATENT_K_ENFORCE"),
            ),
            enforce_auditor=overrides.get(
                "enforce_auditor", _flag("AUDITOR_ENFORCE"),
            ),
            enforce_authority=overrides.get(
                "enforce_authority", _flag("AUTHORITY_ENFORCE"),
            ),
            enforce_failure_mode=overrides.get(
                "enforce_failure_mode", _flag("PATENT_M_ENFORCE"),
            ),
            enforce_risk_budget=overrides.get(
                "enforce_risk_budget", _flag("PATENT_I_ENFORCE"),
            ),
        )

    def all_enforced(self) -> bool:
        return all((
            self.enforce_adversarial, self.enforce_auditor,
            self.enforce_authority, self.enforce_failure_mode,
            self.enforce_risk_budget,
        ))


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
    # Per-patent staged-rollout policy. Defaults to all-enforce; pass
    # ``EnforcementPolicy.from_env()`` to read PATENT_*_ENFORCE flags.
    policy: EnforcementPolicy = field(default_factory=EnforcementPolicy)
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
    # Refresh the per-process policy override cache once per
    # decision. Costs at most one Mongo read every 30s (TTL); the
    # admin-UI Promote button writes here, this read picks it up.
    try:
        from services.guard_policy_store import refresh_cache_if_stale
        await refresh_cache_if_stale()
    except Exception:  # noqa: BLE001
        pass

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
    if adversarial.allowed_to_trade:
        # Gate accepted — its action becomes operational.
        effective_action = adversarial.action
    elif ctx.policy.enforce_adversarial:
        return await _reject(
            ctx, reason="adversarial_rejection",
            detail={"adversarial": _adversarial_to_dict(adversarial)},
            proof_hashes=proof_hashes,
        )
    else:
        # Shadow: log the would-block but keep the signal's original
        # action. Downstream gates inspect what the operator actually
        # wanted to do, not the gate's safer fallback.
        logger.info(
            "[ip_contract] adversarial would-block (shadow): %s",
            adversarial.reason.value,
        )
        effective_action = ctx.signal.action

    # ── Step 4 — Auditor calibration veto ────────────────────────────
    audit = review_calibration(
        rolling_accuracy=ctx.signal.rolling_accuracy,
        calibration_gap=ctx.signal.calibration_gap,
        signal_confidence=ctx.signal.bull.confidence
            if effective_action == "BUY"
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
        if ctx.policy.enforce_auditor:
            return await _reject(
                ctx, reason="auditor_veto",
                detail={"audit": audit.to_dict()},
                proof_hashes=proof_hashes,
            )
        logger.info("[ip_contract] auditor would-veto (shadow)")

    # ── Step 5 — Authority validation (Patent H/I) ───────────────────
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    _expires_at = getattr(ctx.authority, "expires_at", None)
    _expired = _expires_at is None or _expires_at <= _now
    if ctx.authority is None or _expired:
        if ctx.policy.enforce_authority:
            return await _reject(
                ctx, reason="invalid_authority",
                detail={
                    "authority_tier": getattr(ctx.authority, "tier", None).value
                        if ctx.authority and getattr(ctx.authority, "tier", None) else None,
                    "expired": _expired,
                },
                proof_hashes=proof_hashes,
            )
        logger.info("[ip_contract] invalid authority (shadow)")
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
        if ctx.policy.enforce_failure_mode:
            return await _reject(
                ctx, reason="failure_mode_block",
                detail={"failure_mode": _failure_to_dict(failure)},
                proof_hashes=proof_hashes,
            )
        logger.info(
            "[ip_contract] failure_mode would-block (shadow): %s",
            failure.mode.value,
        )

    # ── Step 7 — Adaptive risk budget (Patent I) ─────────────────────
    adjusted_multiplier, fm_reasons = apply_failure_mode_to_multiplier(
        ctx.risk_request.base_multiplier, failure,
    )
    risk_request = RiskBudgetRequest(
        action=effective_action,
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
        if ctx.policy.enforce_risk_budget:
            return await _reject(
                ctx, reason="risk_budget_rejection",
                detail={"risk": _risk_to_dict(risk)},
                proof_hashes=proof_hashes,
            )
        # Shadow: risk gate would have rejected, but enforcement is off.
        # Fall back to the operator's pre-guard sizing so downstream
        # execution behaves as if the gate weren't there. The proof
        # chain still records the gate's verdict for offline review.
        logger.info("[ip_contract] risk_budget would-reject (shadow)")
        effective_notional = float(ctx.signal.base_notional)
        effective_multiplier = float(ctx.signal.base_multiplier)
    else:
        effective_notional = risk.final_notional
        effective_multiplier = risk.final_multiplier

    # ── Invariants — non-negotiable safety net ───────────────────────
    _assert_invariants(
        signal=ctx.signal,
        action=effective_action,
        final_notional=effective_notional,
        final_multiplier=effective_multiplier,
        loosened=risk.loosened,
        authority=ctx.authority,
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
                action=effective_action,
                notional=effective_notional,
                risk_multiplier=effective_multiplier,
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
        "action": effective_action,
        "notional": effective_notional,
        "risk_multiplier": effective_multiplier,
        "filled": execution.filled,
        "reasons": list(risk.reasons),
        "proof_hashes": proof_hashes,
        "proof_hash": proof_hashes[-1] if proof_hashes else None,
        "adversarial": _adversarial_to_dict(adversarial),
        "audit": audit.to_dict(),
        "failure_mode": _failure_to_dict(failure),
        "risk": _risk_to_dict(risk),
        "policy": _policy_to_dict(ctx.policy),
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
    action: str,
    final_notional: float,
    final_multiplier: float,
    loosened: bool,
    authority: AuthorityScope,
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
    assert action in {"BUY", "SELL", "HOLD"}, (
        f"action invariant: {action!r}"
    )
    assert final_multiplier <= authority.max_multiplier + 1e-9, (
        f"final_multiplier ({final_multiplier}) exceeded "
        f"authority.max_multiplier ({authority.max_multiplier})"
    )
    assert not (action == "HOLD" and final_notional > 0), (
        "HOLD with non-zero notional is forbidden"
    )
    assert not (loosened and not getattr(authority, "countersignature_hash", None)), (
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


def _policy_to_dict(p: EnforcementPolicy) -> dict[str, bool]:
    return {
        "enforce_adversarial": p.enforce_adversarial,
        "enforce_auditor": p.enforce_auditor,
        "enforce_authority": p.enforce_authority,
        "enforce_failure_mode": p.enforce_failure_mode,
        "enforce_risk_budget": p.enforce_risk_budget,
    }
