"""Realtime infra: Kraken WS streamer, stress events, Tier-3 slippage advisor.

Extracted from ``routes/admin.py``. Three independent operator
surfaces grouped because they all relate to live realtime
infrastructure (push streams, stress monitor, slippage advisor):

  Kraken WS streamer:
    * ``GET  /api/admin/kraken-ws/status``

  Stress events:
    * ``GET  /api/admin/stress-events``
    * ``POST /api/admin/stress-events/check``

  Tier-3 slippage advisor:
    * ``GET  /api/admin/tier3-slippage-advisor/proposals``
    * ``GET  /api/admin/tier3-slippage-advisor/analysis``
    * ``POST /api/admin/tier3-slippage-advisor/run``
    * ``POST /api/admin/tier3-slippage-advisor/proposals/status``

URLs unchanged. Owner-gated.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/admin", tags=["admin-realtime-infra"])
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


# ── Kraken WS streamer ──────────────────────────────────────────


@router.get("/kraken-ws/status")
async def kraken_ws_status(request: Request):
    """Diagnostic snapshot of the Kraken WS streamer state.

    Reports whether the background task is alive, how many symbols
    have an in-memory snapshot, and the staleness threshold beyond
    which streamed quotes fall through to REST."""
    await _require_owner(request)
    from services.kraken_ws_stream import stream_status, get_streamed_quote
    base = stream_status()
    # Include the ages of each tracked snapshot so the operator can
    # see which symbols are flowing vs which are stale.
    ages: dict[str, float | None] = {}
    for sym in list(base.get("tracked_symbols", [])):
        q = await get_streamed_quote(sym)
        if q is None:
            ages[sym] = None
            continue
        ts = q.get("ts")
        ages[sym] = round(time.time() - float(ts), 2) if ts else None
    base["snapshot_ages_sec"] = ages
    return base


# ── Cross-asset stress events ───────────────────────────────────


@router.get("/stress-events")
async def stress_events_recent(request: Request, limit: int = 20):
    """Recent ``stress_events`` rows (newest first). Each row
    captures a moment when the Spread Watch flagged ≥ N stressed
    symbols simultaneously across crypto + equity."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 200))
    cursor = db.stress_events.find({}, {"_id": 0}).sort(
        "fired_at", -1,
    ).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        for k in ("fired_at", "cooldown_until"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"rows": rows, "count": len(rows)}


@router.post("/stress-events/check")
async def stress_events_manual_check(request: Request):
    """Manually trigger the stress-event monitor. Useful for
    smoke-testing the auto-flatten path."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.stress_event_monitor import run_stress_check
    return await run_stress_check(db)


# ── Tier-3 slippage advisor ─────────────────────────────────────


class _Tier3StatusUpdate(BaseModel):
    segment_key: str
    generated_week: str
    status: str  # accepted / dismissed / pending


@router.get("/tier3-slippage-advisor/proposals")
async def tier3_advisor_list(
    request: Request, status: str | None = None, limit: int = 50,
):
    """List Tier-3 slippage advisor proposals (newest first).
    Optional ``status`` filter (``pending`` / ``accepted`` /
    ``dismissed``)."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.tier3_slippage_advisor import list_proposals
    rows = await list_proposals(db, status=status, limit=limit)
    return {"rows": rows, "count": len(rows), "filter_status": status}


@router.get("/tier3-slippage-advisor/analysis")
async def tier3_advisor_analysis(
    request: Request, lookback_days: int = 30,
):
    """Pure read-only segmentation pass — same math the writer uses
    but with no insert. Lets the operator inspect what the next
    advisor cycle WOULD propose."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.tier3_slippage_advisor import (
        analyze_slippage_segments, detect_outliers, draft_proposals,
    )
    analysis = await analyze_slippage_segments(
        db, lookback_days=lookback_days,
    )
    outliers = detect_outliers(analysis["segments"], analysis["baseline"])
    drafts = draft_proposals(outliers, lookback_days=lookback_days)
    # Strip ``generated_at`` datetime so the response is JSON-safe.
    for d in drafts:
        ga = d.get("generated_at")
        if hasattr(ga, "isoformat"):
            d["generated_at"] = ga.isoformat()
    return {
        **analysis,
        "outliers": outliers,
        "draft_proposals": drafts,
    }


@router.post("/tier3-slippage-advisor/run")
async def tier3_advisor_run(request: Request, lookback_days: int = 30):
    """Manually trigger a full advisor cycle (analyse → draft →
    upsert)."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.tier3_slippage_advisor import run_advisor_cycle
    return await run_advisor_cycle(db, lookback_days=lookback_days)


@router.post("/tier3-slippage-advisor/proposals/status")
async def tier3_advisor_set_status(
    request: Request, body: _Tier3StatusUpdate,
):
    """Mark a proposal accepted / dismissed / pending. Advisory
    only — does NOT apply the proposed env change."""
    user = await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.tier3_slippage_advisor import update_proposal_status
    res = await update_proposal_status(
        db,
        segment_key=body.segment_key,
        generated_week=body.generated_week,
        new_status=body.status,
        actor=user.get("email") or "operator",
    )
    if res is None:
        raise HTTPException(status_code=404, detail="proposal_not_found")
    return res
