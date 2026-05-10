"""FastAPI router — owner-only Code Evolution v0 endpoints.

Endpoints
---------
POST /api/admin/code-evolution/evaluate
    Run AST + audit + promotion policy, persist a Mongo receipt,
    and return the structured verdict. NEVER promotes.

POST /api/admin/code-evolution/countersign
    Operator countersigns a receipt. Each unique operator may
    sign once. When ``signatures.length >= required_signatures``
    the status flips to ``SIGNED_AWAITING_OPS`` — the gate has
    no other promotion power.

GET  /api/admin/code-evolution/receipts
    List the most recent receipts (paginated, capped at 100).

GET  /api/admin/code-evolution/receipts/{patch_id}
    Fetch a single receipt.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from routes.auth import get_current_user
from .ast_invariants import scan_patch
from .code_auditor import audit_patch
from .promotion_policy import evaluate as evaluate_policy
from .schemas import (
    CodePatchProposal,
    CountersignRequest,
    EvaluateResponse,
    PatchEvaluateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/code-evolution",
    tags=["admin", "code-evolution"],
)


COLLECTION = "code_evolution_receipts"
MAX_LIST = 100


# ── DB injection (mirrors the route_registry pattern) ───────────────


_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _get_db():
    if _db is None:
        raise HTTPException(
            status_code=503, detail="code-evolution: db not wired",
        )
    return _db


# ── Auth helper ─────────────────────────────────────────────────────


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


# ── Receipt helpers ─────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _diff_sha(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def _strip_id(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_patch(
    body: PatchEvaluateRequest,
    request: Request,
):
    user = await _require_owner(request)
    db = _get_db()

    proposal = CodePatchProposal(
        patch_id=body.patch_id,
        title=body.title,
        target_files=tuple(body.target_files),
        rationale=body.rationale,
        diff_text=body.diff_text,
    )

    invariants = scan_patch(proposal)
    audit = audit_patch(proposal, invariants)
    policy = evaluate_policy(invariants, audit)

    now = _utc_now()
    receipt = {
        "patch_id": proposal.patch_id,
        "title": proposal.title,
        "target_files": list(proposal.target_files),
        "rationale": proposal.rationale,
        "diff_sha256": _diff_sha(proposal.diff_text),
        "diff_size_bytes": len(proposal.diff_text.encode("utf-8")),
        "status": policy.status,
        "risk_level": audit.risk_level,
        "invariant_violations": list(invariants.forbidden_patterns_hit),
        "protected_paths_hit": list(invariants.protected_paths_hit),
        "execution_paths_hit": list(invariants.execution_paths_hit),
        "risk_paths_hit": list(invariants.risk_paths_hit),
        "ast_notes": list(invariants.notes),
        "required_tests": list(audit.required_tests),
        "required_signatures": policy.required_signatures,
        "signatures": [],
        "audit_rationale": audit.rationale,
        "policy_reason": policy.reason,
        "created_at": now,
        "updated_at": now,
        "created_by": str(user.get("_id")),
        "auto_promote": False,  # invariant — never True.
        "final_policy": "SHADOW_ONLY_UNLESS_MANUALLY_PROMOTED",
    }

    # Idempotent upsert by patch_id.
    await db[COLLECTION].update_one(
        {"patch_id": proposal.patch_id},
        {"$set": receipt},
        upsert=True,
    )

    return EvaluateResponse(
        patch_id=proposal.patch_id,
        status=policy.status,
        risk_level=audit.risk_level,
        required_signatures=policy.required_signatures,
        invariant_violations=list(invariants.forbidden_patterns_hit),
        protected_paths_hit=list(invariants.protected_paths_hit),
        execution_paths_hit=list(invariants.execution_paths_hit),
        risk_paths_hit=list(invariants.risk_paths_hit),
        required_tests=list(audit.required_tests),
        auto_promote=False,
        final_policy="SHADOW_ONLY_UNLESS_MANUALLY_PROMOTED",
        receipt_id=proposal.patch_id,
    )


@router.post("/countersign")
async def countersign(
    body: CountersignRequest,
    request: Request,
):
    user = await _require_owner(request)
    db = _get_db()

    doc = await db[COLLECTION].find_one({"patch_id": body.patch_id})
    if not doc:
        raise HTTPException(status_code=404, detail="receipt not found")

    if doc.get("status") == "BLOCKED_OPERATOR_ONLY":
        raise HTTPException(
            status_code=409,
            detail=(
                "patch is BLOCKED_OPERATOR_ONLY — it touches the "
                "Code Evolution gate itself. Cannot be promoted "
                "via the API."
            ),
        )

    if body.decision == "REJECT":
        await db[COLLECTION].update_one(
            {"patch_id": body.patch_id},
            {"$set": {
                "status": "REJECTED",
                "updated_at": _utc_now(),
                "rejection_note": body.note,
                "rejected_by": str(user.get("_id")),
            }},
        )
        updated = await db[COLLECTION].find_one({"patch_id": body.patch_id})
        return _strip_id(updated)

    # APPROVE — append the operator's signature (de-duplicated).
    operator_email = user.get("email", "")
    operator_id = str(user.get("_id"))
    existing_sigs = doc.get("signatures") or []
    if any(s.get("operator_id") == operator_id for s in existing_sigs):
        raise HTTPException(
            status_code=409,
            detail="this operator has already signed this receipt",
        )

    new_sig = {
        "operator_id": operator_id,
        "operator_email": operator_email,
        "signed_at": _utc_now(),
        "note": body.note,
    }
    new_sigs = existing_sigs + [new_sig]
    required = int(doc.get("required_signatures") or 0)

    new_status = doc.get("status")
    if len(new_sigs) >= required:
        new_status = "SIGNED_AWAITING_OPS"

    await db[COLLECTION].update_one(
        {"patch_id": body.patch_id},
        {"$set": {
            "signatures": new_sigs,
            "status": new_status,
            "updated_at": _utc_now(),
        }},
    )

    updated = await db[COLLECTION].find_one({"patch_id": body.patch_id})
    return _strip_id(updated)


@router.get("/receipts")
async def list_receipts(
    request: Request,
    limit: int = 50,
    status: Optional[str] = None,
):
    await _require_owner(request)
    db = _get_db()
    limit = max(1, min(int(limit or 50), MAX_LIST))
    q: dict = {}
    if status:
        q["status"] = status
    cursor = (
        db[COLLECTION]
        .find(q, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    out = [doc async for doc in cursor]
    return {"receipts": out, "count": len(out)}


@router.get("/receipts/{patch_id}")
async def get_receipt(patch_id: str, request: Request):
    await _require_owner(request)
    db = _get_db()
    doc = await db[COLLECTION].find_one({"patch_id": patch_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="receipt not found")
    return doc
