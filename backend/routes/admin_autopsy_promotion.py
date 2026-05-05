"""Post-trade autopsy + AI promotion-history audit-trail admin routes.

Extracted from ``routes/admin.py``. Both surfaces are read-mostly
audit trails — the autopsy explains "why did this closed trade
move that way", the promotion history captures "when did we flip
which core's phase".

  * ``GET  /api/admin/post-trade-autopsy/{trade_id}``
  * ``GET  /api/admin/post-trade-autopsy``
  * ``GET  /api/admin/promotion-history``
  * ``POST /api/admin/promotion-history/record``

URLs unchanged. Owner-gated.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/admin", tags=["admin-autopsy-promotion"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── Post-trade autopsy ──────────────────────────────────────────


@router.get("/post-trade-autopsy/{trade_id}")
async def post_trade_autopsy(trade_id: str, request: Request):
    """Retrieve the autopsy overlay for a specific closed trade.

    Searches both ``paper_trades`` (equity) and
    ``crypto_paper_trades`` (crypto) so the operator doesn't need
    to know which collection the trade lives in. Returns the
    ``autopsy`` field stamped by the respective closer.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    for coll in ("paper_trades", "crypto_paper_trades"):
        doc = await db[coll].find_one(
            {"trade_id": trade_id}, {"_id": 0},
        )
        if doc is not None:
            # Rebuild on-the-fly if the row predates the autopsy
            # overlay — keeps historical trades queryable.
            if not doc.get("autopsy"):
                from services.post_trade_autopsy import build_post_trade_autopsy
                doc["autopsy"] = build_post_trade_autopsy(doc)
            return {
                "trade_id": trade_id,
                "lane": "equity" if coll == "paper_trades" else "crypto",
                "status": doc.get("status"),
                "autopsy": doc.get("autopsy"),
                "symbol": doc.get("symbol") or doc.get("ticker"),
                "direction": doc.get("direction"),
                "outcome": doc.get("outcome"),
                "close_reason": (
                    doc.get("close_reason") or doc.get("auto_close_reason")
                ),
            }
    raise HTTPException(status_code=404, detail="trade_not_found")


@router.get("/post-trade-autopsy")
async def post_trade_autopsy_recent(
    request: Request, lane: str = "all", limit: int = 20,
):
    """Recent closed trades with their autopsy overlays attached.

    Lane filter: ``equity`` / ``crypto`` / ``all`` (default).
    Returns newest-first to match the operator's mental model of
    "what closed most recently".
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    lane = (lane or "all").strip().lower()
    limit = max(1, min(int(limit), 100))
    colls: list[tuple[str, str]] = []
    if lane in ("equity", "all"):
        colls.append(("paper_trades", "equity"))
    if lane in ("crypto", "all"):
        colls.append(("crypto_paper_trades", "crypto"))

    rows: list[dict] = []
    from services.post_trade_autopsy import build_post_trade_autopsy
    for coll, label in colls:
        cursor = db[coll].find(
            {"status": "closed"}, {"_id": 0},
        ).sort("closed_at", -1).limit(limit)
        for r in await cursor.to_list(length=limit):
            if not r.get("autopsy"):
                r["autopsy"] = build_post_trade_autopsy(r)
            closed_at = r.get("closed_at")
            rows.append({
                "trade_id": r.get("trade_id"),
                "lane": label,
                "symbol": r.get("symbol") or r.get("ticker"),
                "direction": r.get("direction"),
                "outcome": r.get("outcome"),
                "pnl": r.get("pnl_usd", r.get("pnl")),
                "closed_at": (
                    closed_at.isoformat()
                    if hasattr(closed_at, "isoformat") else closed_at
                ),
                "reason_codes": r.get("autopsy", {}).get("reason_codes", []),
                "summary": r.get("autopsy", {}).get("summary"),
            })
    # Merged sort by closed_at desc across lanes.
    rows.sort(key=lambda x: x.get("closed_at") or "", reverse=True)
    rows = rows[:limit]
    return {"rows": rows, "count": len(rows), "lane": lane}


# ── AI promotion history ────────────────────────────────────────


class _PromotionManualRecord(BaseModel):
    core: str
    from_phase: str
    to_phase: str
    reason: str = ""


@router.get("/promotion-history")
async def promotion_history_list(
    request: Request, core: str | None = None, limit: int = 50,
):
    """Return newest-first ``ai_promotion_history`` rows. Optional
    ``core`` filter (``adversarial`` / ``sovereign_equity`` /
    ``sovereign_crypto`` / ``equity_shadow_commander``)."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.promotion_history import (
        get_promotion_history, CORE_REGISTRY, _read_current_phase,
    )
    limit = max(1, min(int(limit), 500))
    rows = await get_promotion_history(db, core=core, limit=limit)
    # Include the current live phase per core so the operator can
    # immediately see "what the env says right now".
    current_phases = {
        c: _read_current_phase(c) for c in CORE_REGISTRY
    }
    return {
        "rows": rows,
        "count": len(rows),
        "filter_core": core,
        "current_phases": current_phases,
    }


@router.post("/promotion-history/record")
async def promotion_history_record(
    request: Request, body: _PromotionManualRecord,
):
    """Manually append a row to the promotion-history audit trail.

    Used when the operator flips an env phase and wants to annotate
    *why* in real time (e.g., "rate cleared 60% at 120 rows").
    The actor is stamped with the operator's email from the auth
    cookie."""
    user = await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.promotion_history import (
        record_promotion, CORE_REGISTRY, _snapshot_metrics,
    )
    if body.core not in CORE_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"unknown core: {body.core}. Known: {list(CORE_REGISTRY)}",
        )
    metrics = await _snapshot_metrics(db, body.core)
    row = await record_promotion(
        db, core=body.core,
        from_phase=body.from_phase, to_phase=body.to_phase,
        actor=user.get("email") or "operator",
        reason=body.reason,
        metrics=metrics,
    )
    at = row.get("at")
    if hasattr(at, "isoformat"):
        row["at"] = at.isoformat()
    return row
