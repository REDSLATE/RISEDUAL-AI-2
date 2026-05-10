"""Promotion policy — the doctrine that says "AI may not promote".

This module owns the single hard rule:

    def may_auto_promote(*args, **kwargs) -> bool:
        return False

That literal definition is asserted by the v0 test suite —
flipping it to ``return True`` is a CI fail. The rest of the
policy maps the AST + audit verdicts onto a ``PatchStatus`` and
a required-signature count.
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    ASTInvariantResult,
    PatchStatus,
    PromotionPolicyResult,
    RiskClassification,
)


# ── The Doctrine ────────────────────────────────────────────────────


def may_auto_promote(*args: Any, **kwargs: Any) -> bool:
    """Return False. Always. Forever.

    AI in this codebase does NOT have authority to promote code.
    The Code Evolution layer can audit, classify, recommend tests,
    and write receipts — but the actual ``git apply`` + supervisor
    restart is operator-only.

    This function is intentionally constant. Any future evolution
    that wants to lift the rule must replace the entire function
    + rewrite ``test_promotion_policy_doctrine`` — not paper over
    a flag.
    """
    return False


# ── Signature requirements per risk level ───────────────────────────


def required_signatures_for(risk_level: str) -> int:
    """How many distinct operator countersigns the receipt must
    accumulate before its status flips to ``SIGNED_AWAITING_OPS``.

    LOW / MEDIUM   → 0 (operator may apply unilaterally)
    HIGH           → 1
    CRITICAL       → 2
    """
    if risk_level == "CRITICAL":
        return 2
    if risk_level == "HIGH":
        return 1
    return 0


# ── Status resolution ───────────────────────────────────────────────


def resolve_initial_status(
    invariants: ASTInvariantResult,
    audit: RiskClassification,
) -> tuple[PatchStatus, str]:
    """Map the gate's findings onto an initial ``PatchStatus``.

    Order matters — the most restrictive verdict wins.
    """
    # Hard block — patch touches the gate itself.
    if invariants.blocks_operator_only:
        return (
            "BLOCKED_OPERATOR_ONLY",
            "Patch targets the Code Evolution gate. The gate cannot "
            "be modified via the gate itself — operator-only path.",
        )
    # Hard block — forbidden pattern in the additions.
    if invariants.forbidden_patterns_hit:
        return (
            "BLOCKED_FORBIDDEN_PATTERN",
            f"Diff contains forbidden patterns: "
            f"{list(invariants.forbidden_patterns_hit)}",
        )
    # AST flagged destructive Mongo calls — block too. The audit
    # tier may say "HIGH" but data-loss-shaped patches are not
    # something v0 will let through with a single signature.
    if any(
        "destructive call" in n or "direct insert" in n
        for n in invariants.notes
    ):
        return (
            "BLOCKED_FORBIDDEN_PATTERN",
            f"AST flagged destructive / direct-insert calls: "
            f"{list(invariants.notes)}",
        )

    # Soft routing by risk tier.
    if audit.risk_level == "CRITICAL":
        return (
            "REQUIRES_DUAL_OPERATOR_SIGNATURE",
            "CRITICAL patch — two operator signatures required.",
        )
    if audit.risk_level == "HIGH":
        return (
            "REQUIRES_OPERATOR_SIGNATURE",
            "HIGH-risk patch — one operator signature required.",
        )
    return (
        "PROPOSED",
        "Patch passed automated checks. Operator may apply directly.",
    )


def evaluate(
    invariants: ASTInvariantResult,
    audit: RiskClassification,
) -> PromotionPolicyResult:
    """Compose the policy verdict. ``auto_promote`` is hardcoded
    to ``may_auto_promote()`` so a single flag flip can never
    bypass the gate."""
    status, reason = resolve_initial_status(invariants, audit)
    return PromotionPolicyResult(
        patch_id=invariants.patch_id,
        auto_promote=may_auto_promote(),
        status=status,
        required_signatures=required_signatures_for(audit.risk_level),
        reason=reason,
    )
