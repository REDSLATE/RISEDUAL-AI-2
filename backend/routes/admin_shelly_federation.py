"""Admin route — Shelly Federation inspection (2026-02-26).

Read-only owner endpoint exposing the 5-Shelly federation state.
Surfaces per-brain memory counts, MC shared-memory size, latest
reasoning receipts, and lets the operator dry-run a reasoning
verdict against a hypothetical case.

Strict no-write surface — there are NO POST endpoints that can
mutate Shelly state from HTTP. Memory writes happen ONLY through
the brain-receipt emission path; rollups happen ONLY through the
scheduled job.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/shelly-federation",
    tags=["admin-shelly-federation"],
)

db: Any = None
pipeline: Any = None


def set_db(database) -> None:
    global db, pipeline
    db = database
    if database is not None:
        try:
            from shelly import ShellyPipeline
            pipeline = ShellyPipeline(database)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[shelly_fed_route] pipeline init failed: %s", exc)


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/state")
async def get_federation_state(request: Request) -> dict[str, Any]:
    """Per-brain memory + receipt counts + MC shared totals."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from shelly.config import MEMORY_REASONING_ONLY, NODE_NAMES

    out: dict[str, Any] = {
        "authority": MEMORY_REASONING_ONLY,
        "nodes": {},
    }
    for node in NODE_NAMES:
        mem_coll = f"shelly_{node.lower()}_memories"
        rec_coll = f"shelly_{node.lower()}_reasoning_receipts"
        try:
            total = await db[mem_coll].count_documents({})
            unrolled = await db[mem_coll].count_documents(
                {"rolled_to_mc": {"$ne": True}}
            )
            receipts = await db[rec_coll].count_documents({})
        except Exception as exc:  # noqa: BLE001
            out["nodes"][node] = {"error": str(exc)}
            continue
        out["nodes"][node] = {
            "memories_total": total,
            "memories_pending_rollup": unrolled,
            "reasoning_receipts_total": receipts,
            "is_mc_node": node == "MC",
        }
    try:
        out["mc_shared"] = {
            "memories_total": await db["shelly_mc_shared_memory"].count_documents({}),
            "reasoning_receipts_total": await db[
                "shelly_mc_reasoning_receipts"].count_documents({}),
        }
    except Exception as exc:  # noqa: BLE001
        out["mc_shared"] = {"error": str(exc)}
    return out


@router.get("/recent-receipts")
async def get_recent_receipts(
    request: Request, limit: int = 20,
) -> dict[str, Any]:
    """Latest MC reasoning receipts — newest first."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    limit = max(1, min(int(limit or 20), 200))
    cursor = db["shelly_mc_reasoning_receipts"].find(
        {}, {"_id": 0},
    ).sort("created_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    return {
        "count": len(rows),
        "receipts": rows,
        "authority": "memory_reasoning_only",
    }


@router.post("/reason")
async def reason_dry_run(
    request: Request, body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Stateless dry-run of MC reasoning against a hypothetical case.

    Body schema::

        {"symbol": "AAPL", "direction": "LONG"}

    Returns the MC reasoning receipt that WOULD be emitted right
    now for that case. Does NOT write — provided for live operator
    spot-checks ("does the federation know anything about AAPL
    LONG right now?").
    """
    await _require_owner(request)
    if db is None or pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline unavailable")
    symbol = (body.get("symbol") or "").strip().upper()
    direction = (body.get("direction") or "").strip().upper()
    if not symbol or not direction:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_symbol_or_direction"},
        )
    return await pipeline.mc_shelly.reason_across_shellys(
        {"symbol": symbol, "direction": direction},
    )


@router.post("/similarity")
async def federation_similarity_search(
    request: Request, body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Phase 3-style cross-Shelly retrieval over Chroma vectors.

    Body schema::

        {"symbol": "AAPL", "direction": "LONG", "decision": "BUY",
         "features": {...}, "top_k_per_node": 3}

    Returns per-node top-K nearest-neighbour memories from every
    federation node's Chroma collection. Caller can merge / rank /
    detect conflicts in their own UI.
    """
    await _require_owner(request)
    if db is None or pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline unavailable")
    from shelly.vector_sidecar import find_similar_federation
    top_k = max(1, min(int(body.get("top_k_per_node", 3) or 3), 25))
    matches = find_similar_federation(body, top_k_per_node=top_k)
    return {
        "authority": "memory_reasoning_only",
        "query": {
            "symbol": body.get("symbol"),
            "direction": body.get("direction"),
            "decision": body.get("decision"),
        },
        "matches_by_node": matches,
        "total_matches": sum(len(v) for v in matches.values()),
    }


__all__ = ["router", "set_db"]
