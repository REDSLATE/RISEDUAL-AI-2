"""Admin endpoints for the Patent J proof chain.

Exposes the immutable decision-trail audit log to the admin UI:

  * GET  /api/admin/proof-chain/entities      — list all entities + counts
  * GET  /api/admin/proof-chain/{entity_id}   — full chain + verify result
  * GET  /api/admin/proof-chain/stats         — aggregate counts by event_type / actor

All routes are owner-only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user
from services.proof_chain import (
    GENESIS_HASH,
    ProofBlock,
    ProofEventType,
    stable_hash,
)

router = APIRouter(prefix="/api/admin/proof-chain", tags=["admin", "proof-chain"])

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


def _serialize_block(doc: dict) -> dict:
    """Project a Mongo doc into the API shape — strip ``_id``,
    isoformat ``created_at``, and pass everything else through."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    if isinstance(out.get("created_at"), datetime):
        out["created_at"] = out["created_at"].isoformat()
    return out


@router.get("/stats")
async def proof_chain_stats(request: Request):
    """Return aggregate stats for the proof-chain admin dashboard."""
    await _require_owner(request)
    if _db is None:
        return {"total": 0, "by_event_type": {}, "by_actor": {}}

    total = await _db.decision_proof_chain.count_documents({})
    by_event = {}
    async for row in _db.decision_proof_chain.aggregate([
        {"$group": {"_id": "$event_type", "n": {"$sum": 1}}},
    ]):
        by_event[row["_id"]] = row["n"]
    by_actor = {}
    async for row in _db.decision_proof_chain.aggregate([
        {"$group": {"_id": "$actor", "n": {"$sum": 1}}},
    ]):
        by_actor[row["_id"]] = row["n"]
    return {"total": total, "by_event_type": by_event, "by_actor": by_actor}


@router.get("/entities")
async def list_entities(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
):
    """List entities (one row per ``entity_id``) with per-entity counts.

    Each row shows: entity_id, n_blocks, latest_event_type,
    latest_block_hash, latest_at. Ordered by most-recent first.
    """
    await _require_owner(request)
    if _db is None:
        return {"entities": [], "total": 0}

    match: dict = {}
    if search:
        match["entity_id"] = {"$regex": search, "$options": "i"}

    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$entity_id",
            "n_blocks": {"$sum": 1},
            "latest_event_type": {"$first": "$event_type"},
            "latest_block_hash": {"$first": "$block_hash"},
            "latest_at": {"$first": "$created_at"},
            "actor": {"$first": "$actor"},
        }},
        {"$sort": {"latest_at": -1}},
        {"$limit": limit},
    ]
    rows = []
    async for r in _db.decision_proof_chain.aggregate(pipeline):
        latest_at = r.get("latest_at")
        rows.append({
            "entity_id": r["_id"],
            "n_blocks": r["n_blocks"],
            "latest_event_type": r.get("latest_event_type"),
            "latest_block_hash": r.get("latest_block_hash"),
            "actor": r.get("actor"),
            "latest_at": latest_at.isoformat() if isinstance(latest_at, datetime) else latest_at,
        })

    # Total entity count for paging info (cheap — covered by index)
    distinct_count = len(await _db.decision_proof_chain.distinct("entity_id", match))
    return {"entities": rows, "total": distinct_count}


@router.get("/{entity_id}")
async def get_chain(entity_id: str, request: Request):
    """Return the full hash-linked chain for one entity, plus a
    verification result. Walks each block, recomputes its block hash
    from the canonical material, and reports any mismatches.
    """
    await _require_owner(request)
    if _db is None:
        raise HTTPException(status_code=503, detail="db unavailable")

    docs = await _db.decision_proof_chain.find(
        {"entity_id": entity_id}, {"_id": 0},
    ).sort("created_at", 1).to_list(500)

    if not docs:
        raise HTTPException(status_code=404, detail="entity not found")

    # Verify chain integrity in-place, mirroring services.proof_chain.verify_chain
    issues: list[str] = []
    for idx, doc in enumerate(docs):
        expected_prev = GENESIS_HASH if idx == 0 else docs[idx - 1]["block_hash"]
        if doc.get("prev_hash") != expected_prev:
            issues.append(f"prev_hash_mismatch_at_index_{idx}")
        # Recompute the payload hash from the persisted payload
        expected_payload_hash = stable_hash(doc.get("payload") or {})
        if doc.get("payload_hash") != expected_payload_hash:
            issues.append(f"payload_hash_mismatch_at_index_{idx}")
        # Recompute the block hash
        created = doc.get("created_at")
        created_iso = created.isoformat() if isinstance(created, datetime) else str(created)
        expected_block_hash = stable_hash({
            "prev_hash": doc.get("prev_hash"),
            "event_type": doc.get("event_type"),
            "entity_id": doc.get("entity_id"),
            "payload_hash": doc.get("payload_hash"),
            "actor": doc.get("actor"),
            "schema_version": doc.get("schema_version"),
            "created_at": created_iso,
        })
        if doc.get("block_hash") != expected_block_hash:
            issues.append(f"block_hash_mismatch_at_index_{idx}")

    return {
        "entity_id": entity_id,
        "n_blocks": len(docs),
        "valid": len(issues) == 0,
        "issues": issues,
        "blocks": [_serialize_block(d) for d in docs],
    }
