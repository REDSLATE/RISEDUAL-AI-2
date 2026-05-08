"""AI Core HTTP routes — `/api/ai-core/*`."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from services.ai_core_engine import learning_engine, registry, set_db as set_engine_db
from services.ai_core_alerts import emit as emit_alert, list_alerts, set_db as set_alerts_db
from services.ai_core_autowire import autowire_sweep
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-core", tags=["ai-core"])

_db = None


def set_db(database) -> None:
    global _db
    _db = database
    set_engine_db(database)
    set_alerts_db(database)


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() in ("admin", "owner")


def _is_dev_env() -> bool:
    return (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower() in (
        "dev", "development", "local",
    )


# ── Read endpoints (open to authenticated users) ─────────────────────


@router.get("/stats")
async def get_stats(request: Request) -> dict:
    await get_current_user(request)
    await learning_engine.hydrate()
    return {
        "stats": learning_engine.stats_snapshot(),
        "conditions": learning_engine.conditions_snapshot(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/trades")
async def get_trades(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    await get_current_user(request)
    await learning_engine.hydrate()
    return {"trades": learning_engine.trades(limit=limit)}


@router.get("/alerts")
async def get_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    await get_current_user(request)
    return {"alerts": await list_alerts(limit=limit)}


# ── Write endpoints ──────────────────────────────────────────────────


@router.post("/trade")
async def post_trade(request: Request) -> dict:
    """Record one resolved trade. Authenticated users only.

    Broadcasts to all registered engines so the candidate observes
    the same firehose. Returns the live engine's result for
    backward-compat; ``engines`` carries every engine's response."""
    await get_current_user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    res = await registry.broadcast_trade(body)
    live = res.get("live_result") or {}
    if not live.get("ok"):
        raise HTTPException(status_code=400, detail=live.get("reason") or "rejected")
    return {**live, "engines": res.get("engines", {})}


@router.post("/reject")
async def post_reject(request: Request) -> dict:
    """Log a rejected signal."""
    await get_current_user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return await learning_engine.record_rejection(body)


@router.post("/cron/nightly")
async def nightly_sweep(request: Request) -> dict:
    """Auto-wire ingestion + dedup-safe daily summary alert.

    Admin-only (cron jobs use the same admin token; manual triggers
    from the admin UI follow the same gate). Same-day re-runs return
    ``alert.deduped=True`` instead of duplicating the alert row."""
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    await learning_engine.hydrate()
    sweep = await autowire_sweep(_db)
    snap = learning_engine.stats_snapshot()
    title = "AI Core Nightly Sweep"
    parts = []
    if snap.get("total_resolved"):
        parts.append(f"{snap['total_resolved']} resolved")
    if snap.get("win_rate") is not None:
        parts.append(f"win-rate {round(snap['win_rate'] * 100, 1)}%")
    if sweep.get("ingested"):
        ing = sweep["ingested"]
        parts.append(f"new trades: {ing.get('paper_trades', 0)} paper + {ing.get('predictions', 0)} preds")
    msg = "; ".join(parts) or "no resolved trades yet"
    alert = await emit_alert(
        "nightly_sweep",
        title=title,
        message=msg,
        metadata={"stats": snap, "sweep": sweep},
    )
    return {
        "ok": True,
        "alert": alert,
        "stats": snap,
        "sweep": sweep,
    }


@router.post("/reset")
async def reset(request: Request) -> dict:
    """Wipe AI Core state. In dev: open. In prod: admin-only.

    Resets every engine's collection so candidate stats don't
    survive a wipe of live."""
    user = await get_current_user(request)
    if not _is_dev_env() and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await registry.reset_all()


# ── Engine registry endpoints ────────────────────────────────────────


@router.get("/engines")
async def list_engines(request: Request) -> dict:
    """Side-by-side comparison of every registered engine."""
    await get_current_user(request)
    out = []
    for engine in registry.all():
        await engine.hydrate()
        out.append({
            "name": engine.name,
            "schema_name": engine.schema.name,
            "schema_dimensions": engine.schema.dimensions,
            "confidence_buckets": [b[0] for b in engine.schema.confidence_buckets],
            "is_live": engine.name == registry.live_name,
            "trades_collection": engine.trades_collection,
            "stats": engine.stats_snapshot(),
        })
    return {"live": registry.live_name, "engines": out}


@router.get("/engines/compare")
async def compare_engines(
    request: Request,
    dimension: str = Query("agent", description="Dimension to compare on"),
    min_total: int = Query(30, ge=1),
) -> dict:
    """Bucket-lift comparison: per-engine, max-min win-rate spread on
    the chosen dimension. Lift = max(win_rate) - min(win_rate) across
    buckets within the dimension that have ≥ min_total trades.

    Higher lift = the engine's schema is *separating* good buckets
    from bad ones more clearly, which is the whole point of a
    candidate's bucketing changes.
    """
    await get_current_user(request)
    out = []
    for engine in registry.all():
        await engine.hydrate()
        cond = engine.conditions_snapshot(min_total=min_total)
        rows = cond.get(dimension, [])
        wrs = [r["win_rate"] for r in rows if r["win_rate"] is not None]
        lift = round(max(wrs) - min(wrs), 4) if len(wrs) >= 2 else None
        out.append({
            "name": engine.name,
            "is_live": engine.name == registry.live_name,
            "dimension": dimension,
            "buckets_meeting_min": len(rows),
            "lift": lift,
            "top_bucket": rows[0] if rows else None,
            "bottom_bucket": rows[-1] if rows else None,
        })
    return {
        "dimension": dimension,
        "min_total": min_total,
        "live": registry.live_name,
        "engines": out,
    }


@router.get("/engines/{name}")
async def get_engine(request: Request, name: str) -> dict:
    """Full stats payload for a single engine (live or candidate)."""
    await get_current_user(request)
    engine = registry.get(name)
    if engine is None:
        raise HTTPException(status_code=404, detail="unknown_engine")
    await engine.hydrate()
    return {
        "name": engine.name,
        "schema_name": engine.schema.name,
        "schema_dimensions": engine.schema.dimensions,
        "is_live": engine.name == registry.live_name,
        "stats": engine.stats_snapshot(),
        "conditions": engine.conditions_snapshot(),
    }


@router.post("/engines/{name}/promote")
async def promote_engine(request: Request, name: str) -> dict:
    """Manually flip the live tag to a candidate engine.

    Admin-only. The previous live becomes a candidate (never
    retired). This is a registry-level label flip ONLY; it does
    NOT cross the dual-stack firewall to influence Council.
    """
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    res = registry.promote(name)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "promote_failed")
    return res
