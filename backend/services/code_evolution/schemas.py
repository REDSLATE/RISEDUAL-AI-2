"""Schemas for the Code Evolution layer.

Internal types live as ``@dataclass`` (used by the auditor +
invariant gate). API I/O lives as Pydantic models (used by the
FastAPI router). Keeping these split avoids leaking
``datetime.utcnow`` ambiguity into the API surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Status enum (single source of truth) ────────────────────────────


PatchStatus = Literal[
    # Initial — the gate has not yet judged the patch.
    "PROPOSED",
    # AST scan caught a forbidden pattern. Cannot be promoted at
    # any signature level — operator must rewrite the patch.
    "BLOCKED_FORBIDDEN_PATTERN",
    # The patch touches the Code Evolution gate itself. Operator
    # only — no API-side promotion path exists by construction.
    "BLOCKED_OPERATOR_ONLY",
    # Patch needs two distinct operator signatures (CRITICAL —
    # live execution / broker / promotion / code gate).
    "REQUIRES_DUAL_OPERATOR_SIGNATURE",
    # Patch needs one operator signature (HIGH — risk or
    # direction logic).
    "REQUIRES_OPERATOR_SIGNATURE",
    # Patch has accumulated all required signatures. Operator must
    # still apply + run tests by hand — the gate ONLY records.
    "SIGNED_AWAITING_OPS",
    # Operator explicitly rejected the patch.
    "REJECTED",
]


RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ── Internal dataclasses (pure-python core) ─────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CodePatchProposal:
    patch_id: str
    title: str
    target_files: tuple[str, ...]
    rationale: str
    diff_text: str
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class ASTInvariantResult:
    patch_id: str
    passed: bool
    blocks_operator_only: bool
    forbidden_patterns_hit: tuple[str, ...]
    protected_paths_hit: tuple[str, ...]
    execution_paths_hit: tuple[str, ...]
    risk_paths_hit: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskClassification:
    patch_id: str
    risk_level: RiskLevel
    rationale: str
    required_tests: tuple[str, ...]


@dataclass(frozen=True)
class PromotionPolicyResult:
    patch_id: str
    auto_promote: bool          # ALWAYS False in v0.
    status: PatchStatus
    required_signatures: int
    reason: str


@dataclass(frozen=True)
class OperatorSignature:
    operator_id: str
    operator_email: str
    signed_at: datetime
    note: str = ""


# ── Pydantic API models ─────────────────────────────────────────────


class PatchEvaluateRequest(BaseModel):
    patch_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=256)
    target_files: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=4000)
    diff_text: str = Field(..., max_length=200_000)


class CountersignRequest(BaseModel):
    patch_id: str
    decision: Literal["APPROVE", "REJECT"] = "APPROVE"
    note: str = Field(default="", max_length=1000)


class PatchReceipt(BaseModel):
    """Mongo-persisted shape — _id is excluded from API responses."""
    patch_id: str
    title: str
    target_files: list[str]
    rationale: str
    diff_sha256: str            # we never echo the full diff back
    diff_size_bytes: int
    status: PatchStatus
    risk_level: RiskLevel
    invariant_violations: list[str]
    protected_paths_hit: list[str]
    execution_paths_hit: list[str]
    risk_paths_hit: list[str]
    required_tests: list[str]
    required_signatures: int
    signatures: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    auto_promote: bool = False  # invariant — flips to True is forbidden.
    final_policy: str = "SHADOW_ONLY_UNLESS_MANUALLY_PROMOTED"


class EvaluateResponse(BaseModel):
    patch_id: str
    status: PatchStatus
    risk_level: RiskLevel
    required_signatures: int
    invariant_violations: list[str]
    protected_paths_hit: list[str]
    execution_paths_hit: list[str]
    risk_paths_hit: list[str]
    required_tests: list[str]
    auto_promote: bool = False
    final_policy: str = "SHADOW_ONLY_UNLESS_MANUALLY_PROMOTED"
    receipt_id: Optional[str] = None
