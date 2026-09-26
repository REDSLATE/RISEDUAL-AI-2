"""Admin route — Alpha Core v2 (Milestone 1).

Deliberately minimal per the operator directive: structured receipts + ONE
health endpoint + a manual cycle trigger for the eventual canary. No panels,
no diagnostics frameworks.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/alpha-v2", tags=["admin-alpha-v2"])

# Single-flight guard: a fresh engine is built per request, so the per-symbol
# locks / in-flight map cannot guard across overlapping requests. Serialize
# every order-submitting operation (live cycle + close) process-wide so two
# concurrent calls can never submit duplicate live orders.
_live_cycle_lock = asyncio.Lock()

db: Any = None


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _config():
    from services.alpha_core_v2.config import Config
    return Config.load()


def _store(cfg):
    from services.alpha_core_v2.receipts import ReceiptStore
    return ReceiptStore(cfg.db_path)


@router.get("/health")
async def health(request: Request) -> dict:
    await _require_owner(request)
    cfg = _config()
    store = _store(cfg)
    broker_ok = False
    account = None
    try:
        from services.alpha_core_v2.broker import PublicBroker
        broker = await PublicBroker.from_db(db)
        if broker is not None:
            acct = broker.get_account()
            broker_ok = acct.ok
            account = {"equity": acct.equity, "buying_power": acct.buying_power} if acct.ok else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[core-v2] health broker probe failed: %s", exc)
    return {
        "engine": "alpha_core_v2",
        "enabled": cfg.enabled,
        "universe_size": len(cfg.universe),
        "sizing": {
            "allocation_base": "available_buying_power",
            "alloc_pct": cfg.alloc_pct,
            "per_trade_risk_cap": cfg.desired_notional,
            "cash_reserve": cfg.cash_reserve,
            "min_trade": cfg.min_trade,
            "position_count_limit": None,
        },
        "broker_reachable": broker_ok,
        "account": account,
        "receipt_counts": store.counts(),
    }


@router.get("/preflight")
async def preflight(request: Request) -> dict:
    """READ-ONLY arm preflight against the live Public account.

    Verifies the full Core v2 execution surface (auth, account, buying
    power, positions, live quote + freshness, reconciliation, order-endpoint
    availability, idempotency, hardware kill switch, all interlocks) WITHOUT
    submitting any order. Returns ``READY_TO_ARM`` or explicit blocking
    reasons.
    """
    await _require_owner(request)
    from services.alpha_core_v2.preflight import run_preflight
    return await run_preflight(db)


@router.get("/autonomy")
async def autonomy_status(request: Request) -> dict:
    """Read-only Alpha-native autonomy state (above Core v2, MC-independent).

    Reports execution authority, provider roles (metadata only), the explicit
    live arm, and the effective live decision. NO order is placed.
    """
    await _require_owner(request)
    from services.alpha_core_v2.autonomy import load_state
    st = load_state(_config())
    out = st.as_dict()
    out["mc_independent"] = True
    out["note"] = (
        "Authority is separate from provider role and from Core v2 arming. A "
        "live autonomous order requires authority in {toehold,autonomous} AND "
        "ALPHA_AUTONOMY_LIVE=1 AND ALPHA_CORE_V2=1. Provider PRIMARY never grants "
        "trading authority. When execute_live=false the loop runs Core v2 DRY only."
    )
    return out


@router.get("/receipts")
async def receipts(request: Request, limit: int = Query(50, ge=1, le=500)) -> dict:
    await _require_owner(request)
    cfg = _config()
    store = _store(cfg)
    return {"receipts": store.recent(limit=limit), "counts": store.counts()}


@router.post("/run-cycle")
async def run_cycle(request: Request,
                    live: bool = Query(False)) -> dict:
    """Run one v2 cycle. ``live`` defaults False (dry): full pipeline, no
    submit. ``live=true`` requires ALPHA_CORE_V2=1 (the canary flag)."""
    await _require_owner(request)
    cfg = _config()
    if live and not cfg.enabled:
        raise HTTPException(
            status_code=409,
            detail="live cycle refused — set ALPHA_CORE_V2=1 to arm the canary",
        )
    from services.alpha_core_v2.broker import PublicBroker
    from services.alpha_core_v2.engine import CoreV2Engine

    async def _run() -> dict:
        broker = await PublicBroker.from_db(db)
        if broker is None:
            raise HTTPException(status_code=503, detail="Public broker not connected")
        engine = CoreV2Engine(broker, _store(cfg), cfg)
        result = await engine.run_cycle(live=live)
        return result.to_dict()

    if not live:
        return await _run()
    # Live cycles must be single-flight — never two overlapping submits.
    if _live_cycle_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="a live v2 cycle is already running — retry shortly",
        )
    async with _live_cycle_lock:
        return await _run()


@router.post("/reconcile")
async def reconcile(request: Request) -> dict:
    await _require_owner(request)
    cfg = _config()
    from services.alpha_core_v2.broker import PublicBroker
    from services.alpha_core_v2.engine import CoreV2Engine
    broker = await PublicBroker.from_db(db)
    if broker is None:
        raise HTTPException(status_code=503, detail="Public broker not connected")
    engine = CoreV2Engine(broker, _store(cfg), cfg)
    return await engine.reconcile_outstanding()


@router.post("/close")
async def close_position(request: Request, symbol: str = Query(...)) -> dict:
    """Exit one position (broker-authoritative sell). Requires the canary
    flag armed, same as live entries."""
    await _require_owner(request)
    cfg = _config()
    if not cfg.enabled:
        raise HTTPException(
            status_code=409,
            detail="close refused — set ALPHA_CORE_V2=1 to arm v2 execution",
        )
    from services.alpha_core_v2.broker import PublicBroker
    from services.alpha_core_v2.engine import CoreV2Engine
    if _live_cycle_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="a live v2 cycle is already running — retry shortly",
        )
    async with _live_cycle_lock:
        broker = await PublicBroker.from_db(db)
        if broker is None:
            raise HTTPException(status_code=503, detail="Public broker not connected")
        engine = CoreV2Engine(broker, _store(cfg), cfg)
        r = await engine.close_position(symbol.upper())
        return r.to_dict()


@router.post("/close-all")
async def close_all(request: Request) -> dict:
    await _require_owner(request)
    cfg = _config()
    if not cfg.enabled:
        raise HTTPException(
            status_code=409,
            detail="close refused — set ALPHA_CORE_V2=1 to arm v2 execution",
        )
    from services.alpha_core_v2.broker import PublicBroker
    from services.alpha_core_v2.engine import CoreV2Engine
    broker = await PublicBroker.from_db(db)
    if broker is None:
        raise HTTPException(status_code=503, detail="Public broker not connected")
    engine = CoreV2Engine(broker, _store(cfg), cfg)
    return await engine.close_all_positions()
