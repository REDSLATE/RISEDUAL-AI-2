"""Admin Alpha Day Trader — read-only observability.

Endpoints
---------
* ``GET  /api/admin/alpha-daytrader/counters`` — daily rolling counters
  (candidates_seen, setups_created, triggers, intents_created,
  broker_submitted, filled) + env-flag state.
* ``GET  /api/admin/alpha-daytrader/setups?limit=50`` — active + recent
  setups with lifecycle timestamps.
* ``GET  /api/admin/alpha-daytrader/outcomes?limit=50`` — resolved
  outcomes including the no-trade paths (Alpha saw the move, did not
  fire → we still record it).
* ``POST /api/admin/alpha-daytrader/tick`` — force one manual tick
  (owner-only; useful for debugging without waiting 5 min).
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/alpha-daytrader", tags=["admin-alpha-daytrader"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/counters")
async def counters(request: Request):
    await _require_admin(request)
    from services.alpha_day_trader import get_counters
    return await get_counters(db)


@router.get("/setups")
async def setups(request: Request, limit: int = Query(50, ge=1, le=500)):
    await _require_admin(request)
    from services.alpha_day_trader import get_active_setups
    return {"setups": await get_active_setups(db, limit=limit)}


@router.get("/outcomes")
async def outcomes(request: Request, limit: int = Query(50, ge=1, le=500)):
    await _require_admin(request)
    if db is None:
        return {"outcomes": []}
    out: list[dict] = []
    cursor = db.alpha_outcomes.find({}, {"_id": 0}).sort("created_at", -1).limit(int(limit))
    async for row in cursor:
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].isoformat()
        out.append(row)
    return {"outcomes": out}


@router.post("/tick")
async def force_tick(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required for manual tick")
    from services.alpha_day_trader import run_alpha_day_trader_tick
    return await run_alpha_day_trader_tick(db)


@router.get("/runtime")
async def runtime_state(request: Request):
    await _require_admin(request)
    from services.alpha_runtime_state import get_state
    return await get_state(db)


@router.post("/runtime")
async def runtime_state_set(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    body = await request.json()
    from services.alpha_runtime_state import set_state
    return await set_state(
        db,
        scan_enabled=body.get("scan_enabled"),
        execute_enabled=body.get("execute_enabled"),
        operator=user.get("email") or user.get("id") or "owner",
    )


@router.get("/pattern-performance")
async def pattern_performance(request: Request):
    await _require_admin(request)
    from services.alpha_pattern_performance import read_rollups
    return {"rollups": await read_rollups(db)}


@router.post("/pattern-performance/recompute")
async def pattern_performance_recompute(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.alpha_pattern_performance import compute_rollups
    return await compute_rollups(db)


@router.get("/setup/{setup_id}/timeline")
async def setup_timeline(request: Request, setup_id: str):
    """Full lifecycle events for a single setup, read from the SQLite hot store."""
    await _require_admin(request)
    from services import alpha_hot_store
    return {
        "setup_id": setup_id,
        "events": alpha_hot_store.events_for_setup(setup_id),
        "latency_ms": alpha_hot_store.latency_samples(setup_id),
    }


@router.post("/breakeven/run")
async def run_breakeven(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.alpha_breakeven import evaluate_and_apply
    return await evaluate_and_apply(db)


@router.get("/regime")
async def regime_state(request: Request):
    await _require_admin(request)
    from services.market_regime import get_current
    return await get_current(db)


@router.post("/regime/refit")
async def regime_refit(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.market_regime import refit
    snap = await refit(db)
    return snap.to_dict()


@router.get("/edge")
async def edge_rollups(request: Request):
    await _require_admin(request)
    from services.alpha_edge_engine import read_rollups
    return {"rollups": await read_rollups(db)}


@router.post("/edge/recompute")
async def edge_recompute(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.alpha_edge_engine import compute_rollups
    return await compute_rollups(db)


@router.get("/fast-regime")
async def fast_regime_state(request: Request):
    await _require_admin(request)
    from services.fast_intraday_regime import get_current
    return await get_current(db)


@router.post("/fast-regime/refresh")
async def fast_regime_refresh(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.fast_intraday_regime import snapshot
    return await snapshot(db)


@router.post("/fills/resolve")
async def fills_resolve(request: Request):
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services.alpha_fill_writer import resolve_closed_outcomes, track_open_excursions
    excursions = await track_open_excursions(db)
    resolved = await resolve_closed_outcomes(db)
    return {"excursions": excursions, "resolved": resolved}


@router.get("/resolved-trades")
async def resolved_trades(request: Request,
                           limit: int = Query(50, ge=1, le=500),
                           include_unmeasured: bool = Query(False)):
    """First Resolved Trade Report — per-trade fill economics with
    latency samples from the SQLite hot store and edge-agreement flag."""
    await _require_admin(request)
    from services.alpha_trade_report import resolved_report
    return await resolved_report(db, limit=limit, only_measured=not include_unmeasured)


@router.get("/broker-comparison")
async def broker_comparison(
    request: Request,
    window: str = Query("1d"),
    symbol: str = Query("", max_length=16),
):
    """Public vs MooMoo latency + slippage aggregates for the Mission
    Control Broker Comparison panel."""
    await _require_admin(request)
    from services.broker_comparison_service import compare
    return compare(window=window, symbol=(symbol or None))


@router.get("/broker-comparison/recent")
async def broker_comparison_recent(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    symbol: str = Query("", max_length=16),
):
    """Raw last-N broker_comparison rows for the side-by-side table."""
    await _require_admin(request)
    from services.broker_comparison_service import recent_rows
    return {"rows": recent_rows(limit=limit, symbol=(symbol or None))}


@router.get("/broker-audit")
async def broker_audit(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    bot_id: str = Query("", max_length=64),
):
    """Recent per-bot broker switches. Newest first."""
    await _require_admin(request)
    from services.broker_router_audit import recent
    return {"switches": await recent(db, limit=limit, bot_id=(bot_id or None))}


@router.get("/slippage-alerts")
async def slippage_alerts(
    request: Request,
    multiplier: float = Query(2.0, ge=1.1, le=10.0),
    recent_window_sec: int = Query(3600, ge=60, le=86_400),
    baseline_window_sec: int = Query(1_209_600, ge=86_400, le=90 * 86_400),
    record: bool = Query(False),
):
    """Evaluate per-broker rolling slippage vs. historical baseline.

    ``triggered=true`` when the recent p50 slippage crosses
    ``multiplier × baseline p50`` for that broker. Returns raw metrics
    so the UI never has to guess why the alert fired.
    """
    await _require_admin(request)
    from services.slippage_anomaly import evaluate_all, recent_alerts
    live = await evaluate_all(
        db,
        multiplier=multiplier,
        recent_window_sec=recent_window_sec,
        baseline_window_sec=baseline_window_sec,
        record=record,
    )
    history = await recent_alerts(db, limit=10)
    return {**live, "history": history}


# ── Account-Aware Decision Overlay ─────────────────────────────────
@router.get("/overlay/state")
async def overlay_state(request: Request):
    """Return overlay mode + 7-day maturation counter + fault state."""
    await _require_admin(request)
    from services.alpha_account_overlay import status
    return await status(db)


@router.get("/overlay/preview")
async def overlay_preview(
    request: Request,
    broker: str = Query(..., pattern="^(?i)(public|moomoo)$"),
    symbol: str = Query(..., min_length=1, max_length=16),
    side: str = Query(..., pattern="^(?i)(buy|sell|add|cover)$"),
    notional: float = Query(..., ge=1.0, le=1_000_000.0),
):
    """Read-only preview of what the overlay would decide for a
    hypothetical order against the routed broker's live account.
    Public orders use Public account state; MooMoo orders use MooMoo
    account state. Never unions the two."""
    await _require_admin(request)
    from services.alpha_account_overlay import (
        get_account_context_for, evaluate_account_fit,
    )
    ctx = await get_account_context_for(broker.lower(), db=db)
    if ctx is None:
        return {
            "available": False,
            "broker": broker.lower(),
            "reason": "broker_account_unreachable",
        }
    fit = evaluate_account_fit(
        context=ctx, symbol=symbol.upper(), side=side.upper(),
        desired_notional=float(notional),
    )
    return {
        "available": True,
        "broker": broker.lower(),
        "symbol": symbol.upper(),
        "side": side.upper(),
        "desired_notional": float(notional),
        "verdict": fit.verdict,
        "size_multiplier": fit.size_multiplier,
        "reasons": list(fit.reasons),
        "proposed_notional": round(float(notional) * fit.size_multiplier, 2),
        "account_context": ctx.to_model_payload(),
    }


@router.get("/overlay/transition-report")
async def overlay_transition_report(request: Request):
    """Day-7 transition report — PASS/REDUCE/BLOCK counts, avoided
    losses, blocked winners, sizing deltas, broker read failures."""
    await _require_admin(request)
    from services.alpha_account_overlay import transition_report
    return await transition_report(db)


class _OverlayModePatch(BaseModel):
    mode: str  # "SHADOW" | "HARD_GATE"
    reset_counter: bool = False


@router.post("/overlay/mode")
async def overlay_set_mode(request: Request, body: _OverlayModePatch):
    """Force the overlay mode.

    Emergency operator control — used when HARD_GATE is silently
    blocking trades and you need to fall back to SHADOW instantly
    without waiting for a code redeploy. Resetting the counter also
    resets ``overlay_started_at`` so the 7-day maturation restarts
    from zero.
    """
    await _require_admin(request)
    from services.alpha_account_overlay import (
        MODE_SHADOW, MODE_HARD, _save_state,
    )
    mode = (body.mode or "").upper()
    if mode not in (MODE_SHADOW, MODE_HARD):
        raise HTTPException(status_code=400, detail="mode must be SHADOW or HARD_GATE")
    from datetime import datetime, timezone
    patch: dict = {"mode": mode}
    if mode == MODE_SHADOW:
        # Clear the hard-gate timestamp so state is consistent.
        patch["hard_gate_at"] = None
    else:
        patch["hard_gate_at"] = datetime.now(timezone.utc).isoformat()
    if body.reset_counter:
        patch["trading_days_completed"] = 0
        patch["last_trading_day_counted"] = None
        patch["overlay_started_at"] = datetime.now(timezone.utc).isoformat()
    await _save_state(db, patch)
    from services.alpha_account_overlay import status
    return await status(db)

