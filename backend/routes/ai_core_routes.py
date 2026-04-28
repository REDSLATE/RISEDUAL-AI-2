"""AI Core HTTP routes — `/api/ai-core/*`."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from services.ai_core_engine import learning_engine, set_db as set_engine_db
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
    """Record one resolved trade. Authenticated users only."""
    await get_current_user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    res = await learning_engine.record_trade(body)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "rejected")
    return res


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
    """Wipe AI Core state. In dev: open. In prod: admin-only."""
    user = await get_current_user(request)
    if not _is_dev_env() and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await learning_engine.reset()
