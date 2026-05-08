"""RoadGuard pair + Calibration Kanban — read-only promotion-
readiness surfaces. All endpoints under ``/api/admin/ml/v2``.

Read-only by contract. NEVER mutates RoadGuard flags. NEVER calls
a broker.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException, Query, Request

from ._auth import require_admin
from ._routers import get_db, router


# ── RoadGuard pair (lane-isolated) ───────────────────────────────


@router.get("/roadguard/pair-status")
async def roadguard_pair_status(request: Request) -> Dict[str, Any]:
    """Surface the closed-loop RG pair: per-lane decision counts +
    last verdict per lane. Lets the operator audit equity vs crypto
    promotion-readiness independently."""
    await require_admin(request)
    from services.ml.roadguard import CryptoRoadGuard, EquityRoadGuard

    db = get_db()
    out: Dict[str, Any] = {"pair": {}}
    for cls in (EquityRoadGuard, CryptoRoadGuard):
        coll_name = cls.DECISIONS_COLLECTION
        entry: Dict[str, Any] = {
            "lane": cls.LANE,
            "collection": coll_name,
            "total": 0,
            "by_decision": {"PASS": 0, "REDUCE": 0, "BLOCK": 0},
            "last_verdict": None,
        }
        if db is not None:
            try:
                cursor = db[coll_name].aggregate([
                    {"$group": {"_id": "$verdict.decision", "n": {"$sum": 1}}},
                ])
                async for row in cursor:
                    decision = row.get("_id")
                    if decision in entry["by_decision"]:
                        entry["by_decision"][decision] = int(row.get("n") or 0)
                    entry["total"] += int(row.get("n") or 0)
                last = await db[coll_name].find_one(
                    {}, projection={"_id": 0}, sort=[("created_at", -1)],
                )
                if last is not None:
                    ca = last.get("created_at")
                    if hasattr(ca, "isoformat"):
                        last["created_at"] = ca.isoformat()
                    entry["last_verdict"] = last
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
        out["pair"][cls.LANE] = entry
    return out


@router.get("/roadguard/decisions/recent")
async def roadguard_recent_decisions(
    request: Request,
    lane: str = Query(..., pattern="^(equity|crypto)$"),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    await require_admin(request)
    from services.ml.roadguard import CryptoRoadGuard, EquityRoadGuard
    coll = (
        EquityRoadGuard.DECISIONS_COLLECTION if lane == "equity"
        else CryptoRoadGuard.DECISIONS_COLLECTION
    )
    db = get_db()
    if db is None:
        return {"items": [], "count": 0, "lane": lane, "collection": coll}
    items: List[Dict[str, Any]] = []
    try:
        cursor = db[coll].find({}, projection={"_id": 0}).sort("created_at", -1).limit(limit)
        async for doc in cursor:
            ca = doc.get("created_at")
            if hasattr(ca, "isoformat"):
                doc["created_at"] = ca.isoformat()
            items.append(doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"items": items, "count": len(items), "lane": lane, "collection": coll}


# ── Calibration Kanban (read-only promotion-readiness tile) ──────


@router.get("/calibration/kanban")
async def calibration_kanban(request: Request) -> Dict[str, Any]:
    """Per-lane promotion-readiness snapshot for the admin tile.

    Read-only. NEVER mutates flags. NEVER calls a broker. Buttons on
    the tile only display ``Eligible`` / ``Blocked`` / ``Ready for
    Review`` — they do not flip enforcement.
    """
    await require_admin(request)
    from services.calibration_kanban import get_kanban
    return await get_kanban(get_db())
