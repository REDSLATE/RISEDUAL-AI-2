"""Code Auditor — risk classifier + required-test recommender.

Reads the AST invariant result + the path categorisations and
produces a ``RiskClassification``. Never decides whether the patch
gets promoted — that's the promotion policy's job.

Risk rubric (RISEDUAL doctrine):
  CRITICAL = live execution, broker, public API, promotion path,
             code evolution gate itself, or any forbidden-pattern
             hit (regardless of file).
  HIGH     = risk sizing, direction logic, calibration, ADL,
             ml_paper_trader, kill_switch, risk_guard, or any AST
             "destructive call" hit.
  MEDIUM   = analytics, dashboards, logging, admin tools.
  LOW      = docs / UI / non-critical cleanup.
"""
from __future__ import annotations

from .schemas import (
    ASTInvariantResult,
    CodePatchProposal,
    RiskClassification,
)


# ── Required-test mapping ────────────────────────────────────────────


# Each category contributes a tier of recommended tests. Operator
# may add more; the gate never auto-runs them (test runner is
# explicitly out of scope for v0).
EXECUTION_TESTS: tuple[str, ...] = (
    "tests/test_no_local_direction_tuples.py",
    "tests/test_strong_direction_grading.py",
    "tests/test_shadow_no_live_writes.py",
    "tests/test_observability_call_safety.py",
)

RISK_TESTS: tuple[str, ...] = (
    "tests/test_council_risk_bounds.py",
    "tests/test_calibration_layer.py",
    "tests/test_ml_paper_trader_adl_receipts.py",
    "tests/test_alpha_decision_log_record_decision.py",
)

CODE_GATE_TESTS: tuple[str, ...] = (
    "tests/test_code_evolution_v0.py",
    "tests/test_code_size.py",
)


# Heuristic substrings — analytics / dashboards / logging are
# MEDIUM unless they hit one of the higher categories first.
MEDIUM_HINTS: tuple[str, ...] = (
    "dashboard", "analytics", "logger", "logging",
    "admin", "kanban", "panel", "tile", "card", "pill",
)

LOW_HINTS: tuple[str, ...] = (
    "readme", ".md", "docs/", "frontend/public/",
    "comment", "typo",
)


def _path_hits_any(target_files: tuple[str, ...] | list[str], hints: tuple[str, ...]) -> bool:
    return any(
        any(h in (f or "").lower() for h in hints)
        for f in target_files
    )


def audit_patch(
    proposal: CodePatchProposal,
    invariants: ASTInvariantResult,
) -> RiskClassification:
    """Classify ``proposal`` and recommend the test set the
    operator should run before applying."""
    rationale_parts: list[str] = []
    required: set[str] = set()

    # CRITICAL triggers — any one is enough.
    is_protected = invariants.blocks_operator_only
    is_execution = bool(invariants.execution_paths_hit)
    has_forbidden = bool(invariants.forbidden_patterns_hit)

    if is_protected:
        rationale_parts.append(
            f"protected paths hit: {list(invariants.protected_paths_hit)}"
        )
        required.update(CODE_GATE_TESTS)
    if is_execution:
        rationale_parts.append(
            f"execution paths hit: {list(invariants.execution_paths_hit)}"
        )
        required.update(EXECUTION_TESTS)
    if has_forbidden:
        rationale_parts.append(
            f"forbidden patterns matched: "
            f"{list(invariants.forbidden_patterns_hit)}"
        )
        required.update(EXECUTION_TESTS)

    # HIGH triggers.
    is_risk = bool(invariants.risk_paths_hit)
    has_ast_destructive = any(
        "destructive call" in n or "direct insert" in n
        for n in invariants.notes
    )
    if is_risk:
        rationale_parts.append(
            f"risk/direction paths hit: {list(invariants.risk_paths_hit)}"
        )
        required.update(RISK_TESTS)
    if has_ast_destructive:
        rationale_parts.append("AST scan flagged destructive Mongo calls")
        required.update(RISK_TESTS)

    # Tier resolution (highest wins).
    if is_protected or is_execution or has_forbidden:
        risk_level = "CRITICAL"
    elif is_risk or has_ast_destructive:
        risk_level = "HIGH"
    elif _path_hits_any(proposal.target_files, MEDIUM_HINTS):
        risk_level = "MEDIUM"
        rationale_parts.append("medium-tier paths (admin/analytics/logging)")
    elif _path_hits_any(proposal.target_files, LOW_HINTS):
        risk_level = "LOW"
        rationale_parts.append("low-tier paths (docs/UI/non-critical)")
    else:
        risk_level = "MEDIUM"
        rationale_parts.append(
            "uncategorised — defaulted to MEDIUM (operator must "
            "review manually)"
        )

    if not rationale_parts:
        rationale_parts.append("no risk indicators detected")

    return RiskClassification(
        patch_id=proposal.patch_id,
        risk_level=risk_level,
        rationale="; ".join(rationale_parts),
        required_tests=tuple(sorted(required)),
    )
