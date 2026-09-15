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
from datetime import datetime, timezone

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


@router.get("/why-not-trade")
async def why_not_trade(
    request: Request,
    since_seconds: int = Query(300, ge=60, le=604_800),
    sample_per_gate: int = Query(3, ge=1, le=20),
):
    """Diagnostic: which gate is killing Alpha's candidates?

    Reads the SQLite hot store (per-tick lifecycle) plus Mongo
    ``alpha_outcomes`` (resolved rollup) inside the requested
    window and groups rejections by gate. Includes symbols, top
    sub-reasons, and a small sample of raw payloads so the
    operator can pattern-match without opening the timeline for
    every setup.
    """
    await _require_admin(request)
    from services.alpha_why_not_trade import compile_why_not_trade
    return await compile_why_not_trade(
        db,
        since_seconds=since_seconds,
        sample_per_gate=sample_per_gate,
    )


@router.get("/data-provenance")
async def data_provenance(
    request: Request,
    symbols: str = Query("AAPL,QQQ,SPY,NVDA,TSLA,AMD"),
):
    """Diagnostic: WHERE does market data become stale?

    For each liquid symbol, fetches the LIVE source (bypassing cache)
    and lays it side-by-side with Alpha's 5-min quote cache and the
    snapshot ``_snapshot_symbol`` actually builds — so we can tell
    whether staleness ORIGINATES at the provider/feed or is introduced
    INSIDE Alpha (caching / pairing a fresh quote with an old bar /
    an endpoint with no live book). Read-only; does NOT change any
    freshness tolerance. Each row carries a plain-English ``verdict``.
    """
    await _require_admin(request)
    from services.market_data_pool import probe_provenance
    from services import broker_freshness_profile as bfp
    from services.provider_policy import execution_session
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:12]
    rows = []
    for s in syms:
        try:
            rows.append(await probe_provenance(s))
        except Exception as exc:  # noqa: BLE001
            rows.append({"symbol": s, "error": str(exc)[:200]})
    # Route-wide broker_data_limited: group live quote observations by
    # execution route and ask the profile whether the whole route looks
    # degraded (distinct from one symbol being stale).
    session = execution_session()
    by_route: dict[str, list[dict]] = {}
    for r in rows:
        q = r.get("quote") or {}
        if q.get("error"):
            continue
        route = bfp.route_key(q.get("provider"))
        by_route.setdefault(route, []).append({
            "age_seconds": q.get("broker_quote_age_s"),
            "has_book": bool(q.get("has_book")),
        })
    route_health = [
        bfp.assess_route_health(route, session, obs)
        for route, obs in by_route.items()
    ]
    return {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "note": "LIVE columns bypass cache; snapshot is Alpha's real cache-first view. "
                "Verdict is triage-only and does not alter Alpha's freshness gate.",
        "route_health": route_health,
        "rows": rows,
    }


@router.get("/broker-freshness-profile")
async def broker_freshness_profile(request: Request):
    """Per-(broker route, session) measured quote-lag profile
    (p50/p95/p99) that drives the adaptive execution-freshness gate,
    plus the freshness limit each session would currently apply.
    Read-only measurement plumbing — enabling the gate is a separate
    env flag (EXECUTION_ADAPTIVE_FRESHNESS)."""
    await _require_admin(request)
    from services import broker_freshness_profile as bfp
    from services import provider_policy as pp
    profiles = bfp.all_profiles()
    current = pp.execution_session()
    # Show the limit each session would enforce for the routes we've
    # measured (falls back to the default route names when empty).
    routes = sorted({p["broker"] for p in profiles}) or ["public", "moomoo"]
    sessions = ["CORE", "PREMARKET", "AFTER_HOURS", "OVERNIGHT", "CRYPTO"]
    limits = {
        route: {sess: pp.compute_freshness_limit(route, sess) for sess in sessions}
        for route in routes
    }
    return {
        "adaptive_enabled": pp.ADAPTIVE_FRESHNESS_ENABLED,
        "legacy_freshness_secs": pp.EXECUTION_FRESHNESS_SECS,
        "core_min_secs": pp.EXECUTION_FRESHNESS_MIN_SECS,
        "current_session": current,
        "profiles": profiles,
        "computed_limits_secs": limits,
        "note": "Limits shown are what WOULD apply; adaptive gate is "
                + ("ON" if pp.ADAPTIVE_FRESHNESS_ENABLED else "OFF (legacy 5s in force)"),
    }


@router.get("/broker-circuit")
async def broker_circuit_state(request: Request):
    """Broker execution circuit-breaker state.

    Returns the state machine (CLOSED / OPEN / HALF_OPEN), how
    many failures are in the rolling window, cooldown remaining,
    and the last transition reason. Read-only.
    """
    await _require_admin(request)
    from services import broker_circuit_breaker
    return broker_circuit_breaker.snapshot()


@router.post("/broker-circuit/reset")
async def broker_circuit_reset(request: Request):
    """Force the broker circuit breaker back to CLOSED.

    Owner-only — used after a known-transient broker outage to
    resume execution without waiting for the automatic cooldown.
    """
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services import broker_circuit_breaker
    return broker_circuit_breaker.reset()


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


@router.get("/pattern-research")
async def pattern_research(
    request: Request,
    symbol: str = Query("", max_length=16),
    pattern: str = Query("", max_length=48),
    state: str = Query("", max_length=16),
    limit: int = Query(100, ge=1, le=1000),
):
    """Recent classical-pattern assessments.

    Every Alpha tick with ≥5 recent daily bars writes ALL six
    IGNISpilot-ported pattern verdicts (bullish + bearish, including
    ``blocked``/``forming``). This endpoint lets us look at the raw
    log and (later) build models on which patterns actually pay.
    """
    await _require_admin(request)
    from services.alpha_pattern_research import recent as _recent, counts_by_state
    rows = await _recent(
        db,
        symbol=(symbol.strip() or None),
        pattern=(pattern.strip() or None),
        state=(state.strip() or None),
        limit=limit,
    )
    rollup = await counts_by_state(db, symbol=(symbol.strip() or None))
    return {
        "rows": rows,
        "counts_by_pattern_state": rollup,
        "returned": len(rows),
    }


@router.get("/fingerprint-dedup")
async def fingerprint_dedup(
    request: Request,
    symbol: str = Query("", max_length=16),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent economic-fingerprint entries + the dedup window.

    Shows which intents were fingerprinted and (implicitly) which
    ones were skipped. Every time you see the same fingerprint
    appear twice within 15 min, the second one WAS the dedup save.
    """
    await _require_admin(request)
    if db is None:
        return {"rows": [], "returned": 0, "window_min": 15}
    import os as _os
    from services.alpha_fingerprint_index import COLLECTION as _FP_COLL
    q: dict = {}
    if symbol.strip():
        q["symbol"] = symbol.strip().upper()
    try:
        cursor = db[_FP_COLL].find(q, {"_id": 0}).sort(
            "created_at", -1,
        ).limit(int(limit))
        rows = await cursor.to_list(length=int(limit))
    except Exception:  # noqa: BLE001
        rows = []
    return {
        "rows": rows,
        "returned": len(rows),
        "window_min": int(_os.environ.get("ALPHA_INTENT_DEDUP_WINDOW_MIN") or 15),
    }


@router.get("/wave-observations")
async def wave_observations(
    request: Request,
    symbol: str = Query("", max_length=16),
    mode: str = Query("", max_length=32),
    since_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=1000),
):
    """Per-symbol Wave Intelligence observations.

    Wave Intelligence classifies each symbol's own bars into one of
    ``WAIT / TREND_FOLLOW / RANGE_GRID / DANGER_PAUSE``. DANGER_PAUSE
    is a hard veto — Alpha refuses to arm any setup on symbols in
    that mode.

    Returns:
    * ``rows``: newest N observations (filterable)
    * ``mode_counts``: rollup of mode counts across ``since_hours``
    * ``danger_leaderboard``: top symbols by max danger score
    """
    await _require_admin(request)
    from services.alpha_wave_persistence import (
        recent as _recent, mode_counts as _mc,
        danger_leaderboard as _dl,
    )
    rows = await _recent(
        db,
        symbol=(symbol.strip() or None),
        mode=(mode.strip() or None),
        limit=limit,
    )
    counts = await _mc(db, since_hours=since_hours)
    leaderboard = await _dl(db, since_hours=min(since_hours, 12), limit=10)
    return {
        "rows": rows,
        "returned": len(rows),
        "mode_counts": counts,
        "danger_leaderboard": leaderboard,
        "since_hours": since_hours,
    }



# ─────────────────────────────────────────────
#  Trade Discernment Layer — same-session postmortem
# ─────────────────────────────────────────────


class DiscernmentPostmortemRequest(BaseModel):
    session_date: str            # ISO YYYY-MM-DD, US/Eastern
    better_group: list[str]
    poorer_group: list[str]
    invoke_llm: bool = True


@router.post("/discernment-postmortem")
async def run_discernment_postmortem(
    request: Request, body: DiscernmentPostmortemRequest,
):
    """Run a same-session postmortem for an operator-labelled
    better vs poorer group. Persists per-setup feature rows and
    the session-level comparison to SQLite (Mongo is untouched).
    See ``services.alpha_discernment_postmortem`` for the design
    contract (no ticker rules, no new gates from small samples).
    """
    await _require_admin(request)
    from services import alpha_discernment_postmortem as _pm
    result = await _pm.run_postmortem(
        db,
        session_date=body.session_date,
        better_group=body.better_group,
        poorer_group=body.poorer_group,
        invoke_llm=body.invoke_llm,
    )
    return result


@router.get("/discernment-postmortem")
async def get_discernment_postmortem(
    request: Request, session_date: str = Query(..., regex=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Return the most recent stored postmortem for a session date,
    or ``{"present": false}`` when none has been run yet."""
    await _require_admin(request)
    from services import alpha_discernment_postmortem as _pm
    row = _pm.get_latest_postmortem(session_date)
    if row is None:
        return {"present": False, "session_date": session_date}
    return {"present": True, **row}


# ─────────────────────────────────────────────
#  Compact Authority Receipts
# ─────────────────────────────────────────────

@router.get("/authority-receipts")
async def list_authority_receipts(
    request: Request, limit: int = Query(50, ge=1, le=500),
):
    """List recent compact authority receipts. One doc per resolved
    trade, chain-summarised. See ``services.alpha_authority_receipt``
    for the design contract."""
    await _require_admin(request)
    from services.alpha_authority_receipt import list_receipts
    receipts = await list_receipts(db, limit=limit)
    return {"receipts": receipts, "returned": len(receipts)}


@router.get("/authority-receipts/{setup_id}")
async def get_authority_receipt(request: Request, setup_id: str):
    """Fetch the compact authority receipt for a single setup."""
    await _require_admin(request)
    from services.alpha_authority_receipt import get_receipt
    receipt = await get_receipt(db, setup_id=setup_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="no receipt for that setup_id")
    return receipt


@router.post("/authority-receipts/rebuild/{setup_id}")
async def rebuild_authority_receipt(request: Request, setup_id: str):
    """Force-rebuild a receipt from the SQLite hot store. Useful
    when the raw lifecycle stream has been updated after the
    original outcome landed (e.g. late-arriving broker events)."""
    await _require_admin(request)
    from services.alpha_authority_receipt import build_and_persist
    receipt = await build_and_persist(db, setup_id=setup_id)
    if receipt is None:
        raise HTTPException(
            status_code=404,
            detail="no lifecycle events for that setup_id",
        )
    return receipt


# ─────────────────────────────────────────────
#  Session state (NYSE + holidays)
# ─────────────────────────────────────────────

@router.get("/session-state")
async def alpha_session_state(request: Request):
    """Return the current US equity session phase with holiday
    context. Consumed by the panel to render a MARKET CLOSED
    banner instead of a bare "0% intent → broker" that looks
    like a bug on holidays."""
    await _require_admin(request)
    from services.alpha_session_state import get_session_state
    return get_session_state()


# ─────────────────────────────────────────────
#  Hardware kill switch (fail-closed)
# ─────────────────────────────────────────────

@router.get("/hw-kill-switch")
async def get_hw_kill_switch(request: Request):
    """Read the fail-closed hardware kill switch state. Distinct
    from the operator-facing discipline profile — this is a
    hardware halt (corrupt state, repeated errors, drawdown)."""
    await _require_admin(request)
    from services import alpha_hardware_kill_switch as _hw
    return _hw.get_state()


class HwKillTripBody(BaseModel):
    reason: str


@router.post("/hw-kill-switch/trip")
async def trip_hw_kill_switch(request: Request, body: HwKillTripBody):
    await _require_admin(request)
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="reason required")
    from services import alpha_hardware_kill_switch as _hw
    _hw.trip(body.reason.strip(), by="admin_api")
    return _hw.get_state()


@router.post("/hw-kill-switch/reset")
async def reset_hw_kill_switch(request: Request):
    """Operator-confirmed reset. The confirmed_by is the auth user
    id; missing auth was already caught by _require_admin."""
    user = await _require_admin(request)
    from services import alpha_hardware_kill_switch as _hw
    _hw.reset(confirmed_by=str(user.get("email") or user.get("id") or "admin"))
    return _hw.get_state()



# ─────────────────────────────────────────────
#  Funnel — state + config
# ─────────────────────────────────────────────

@router.get("/funnel-state")
async def get_funnel_state(request: Request):
    """Return the current funnel state grouped by candidate state.
    Powers the future 'why did the ranking change' panel."""
    await _require_admin(request)
    from services import alpha_funnel_state as fs, alpha_funnel
    ranked = fs.rank_candidates()
    grouped: dict[str, list[dict]] = {}
    for c in ranked:
        grouped.setdefault(c.state, []).append(c.to_dict())
    return {
        "config": alpha_funnel.get_config().__dict__,
        "counts": {k: len(v) for k, v in grouped.items()},
        "by_state": grouped,
        "total": len(ranked),
    }


class FunnelConfigOverride(BaseModel):
    discovery_universe: int | None = None
    preliminary_survivors: int | None = None
    broker_research_max: int | None = None
    deep_discernment_max: int | None = None
    promoted_armed: int | None = None


@router.post("/funnel-config")
async def update_funnel_config(request: Request, body: FunnelConfigOverride):
    """Runtime override of funnel stage sizes. Pass ``null`` (or
    omit a field) to leave it alone; the boot env default remains
    in effect for fields not overridden."""
    await _require_admin(request)
    from services import alpha_funnel
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        alpha_funnel.set_runtime_override(key, int(value))
    return {"effective": alpha_funnel.get_config().__dict__, "applied": changes}


@router.get("/funnel-config")
async def get_funnel_config(request: Request):
    await _require_admin(request)
    from services import alpha_funnel
    return {"effective": alpha_funnel.get_config().__dict__}



# ── Broker order-event watchdog ────────────────────────────────────


@router.get("/broker-watchdog")
async def broker_watchdog_state(request: Request, only_frozen: bool = Query(False)):
    """List broker-event-watchdog entries. ``?only_frozen=1`` filters to
    submissions currently frozen at ``BROKER_EVENT_STALE`` or
    ``BROKER_STATE_UNKNOWN`` — i.e. the intents an operator must
    reconcile before Alpha will re-arm for that symbol/account."""
    await _require_admin(request)
    from services import alpha_broker_event_watchdog as _watchdog
    entries = _watchdog.list_entries(only_frozen=bool(only_frozen))
    return {"entries": entries, "count": len(entries)}


@router.post("/broker-watchdog/clear/{broker}/{client_order_id}")
async def broker_watchdog_clear(request: Request, broker: str, client_order_id: str):
    """Manually clear a frozen watchdog entry after the operator has
    verified the true broker state out-of-band. Owner-only — clearing
    a frozen intent lets Alpha submit again for that (symbol, account)."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    from services import alpha_broker_event_watchdog as _watchdog
    cleared = _watchdog.clear_frozen(broker, client_order_id, reason="operator_cleared_via_admin")
    if not cleared:
        raise HTTPException(status_code=404, detail="No frozen entry for this (broker, client_order_id)")
    return {"cleared": True, "broker": broker.lower(), "client_order_id": client_order_id}



# ── MooMoo Bridge (Option 2 — local OpenD + web control panel) ────


@router.get("/moomoo-bridge/health")
async def moomoo_bridge_health(request: Request):
    """Read-only readiness ladder for the local MooMoo OpenD bridge.

    Returns the current rung (DISCONNECTED / CONNECTED / DATA_READY /
    RESEARCH_READY / EXECUTION_READY) plus granular diagnostics so
    the admin card can render OpenD reachability, quote latency,
    trading permission, and the execution gate."""
    await _require_admin(request)
    from services.moomoo_bridge_health import compute_health
    return compute_health().as_dict()


@router.post("/moomoo-bridge/smoke-test")
async def moomoo_bridge_smoke_test(request: Request, symbol: str | None = Query(None)):
    """Run a READ-ONLY smoke test through the whole bridge:
    tunnel → OpenD → account/permission → fresh quote → normalized
    BrokerResearchSnapshot. Never submits an order."""
    await _require_admin(request)
    from services.moomoo_bridge_health import run_smoke_test
    return run_smoke_test(symbol=symbol)


class MoomooEndpointOverride(BaseModel):
    host: str | None = None
    port: int | None = None
    canary_symbol: str | None = None
    execution_ready: bool | None = None


@router.post("/moomoo-bridge/endpoint")
async def moomoo_bridge_endpoint(request: Request, body: MoomooEndpointOverride):
    """Owner-only runtime override for the tunnel endpoint and
    execution-ready flag. Updates process env so the change takes
    effect immediately without a restart. Backend .env is NOT
    written — set values there for persistence across restarts."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    import os as _os
    applied: dict[str, str] = {}
    if body.host is not None:
        _os.environ["MOOMOO_OPEND_HOST"] = str(body.host).strip()
        applied["MOOMOO_OPEND_HOST"] = _os.environ["MOOMOO_OPEND_HOST"]
    if body.port is not None:
        _os.environ["MOOMOO_OPEND_PORT"] = str(int(body.port))
        applied["MOOMOO_OPEND_PORT"] = _os.environ["MOOMOO_OPEND_PORT"]
    if body.canary_symbol is not None:
        _os.environ["MOOMOO_CANARY_SYMBOL"] = str(body.canary_symbol).strip().upper()
        applied["MOOMOO_CANARY_SYMBOL"] = _os.environ["MOOMOO_CANARY_SYMBOL"]
    if body.execution_ready is not None:
        _os.environ["MOOMOO_EXECUTION_READY"] = "1" if body.execution_ready else "0"
        applied["MOOMOO_EXECUTION_READY"] = _os.environ["MOOMOO_EXECUTION_READY"]
    # Force the cached trade context to reopen against the new endpoint.
    try:
        from services import moomoo_broker_adapter
        moomoo_broker_adapter.close()
    except Exception:  # noqa: BLE001
        pass
    return {"applied": applied}


# ── Orphaned-intent reaper (fail-visible invariant) ────────────────


@router.post("/orphan-reaper/sweep")
async def orphan_reaper_sweep(request: Request):
    """Manually trigger the orphan-intent reaper. Also runs
    automatically at the end of each Alpha tick — this endpoint is
    for on-demand cleanup when the operator suspects a stuck intent."""
    await _require_admin(request)
    from services import alpha_orphan_reaper
    from server import db as _db  # local import to avoid cycle
    return await alpha_orphan_reaper.sweep_orphaned_intents(_db)


# ── Rejection taxonomy (protection vs infrastructure vs session) ──


@router.get("/rejection-taxonomy")
async def rejection_taxonomy(request: Request, since_seconds: int = Query(86400)):
    """Return today's rejection counts folded into three classes:
    ``protection`` (Alpha correctly refusing), ``infrastructure``
    (plumbing failed), ``session`` (market/config state).

    Reads from ``intent_skip_log`` (executor gate rejections) merged
    with ``alpha_observations.execution_blocked`` (day-trader gate
    rejections). Aggregates by ``reason`` then classifies."""
    await _require_admin(request)
    from server import db as _db
    from datetime import datetime, timezone, timedelta
    from services.alpha_rejection_taxonomy import summarize, health_hint

    since = max(60, min(int(since_seconds or 86400), 7 * 24 * 3600))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=since)
    counts: dict[str, int] = {}
    if _db is not None:
        try:
            skip_cursor = _db.intent_skip_log.find(
                {"ts": {"$gte": cutoff}}, {"reason": 1, "_id": 0},
            )
            async for row in skip_cursor:
                reason = str(row.get("reason") or "unknown")
                counts[reason] = counts.get(reason, 0) + 1
        except Exception:  # noqa: BLE001
            pass
        try:
            obs_cursor = _db.alpha_observations.find(
                {"ts": {"$gte": cutoff},
                 "event": {"$in": ["executor_rejected", "execution_blocked",
                                    "execution_quote_blocked", "invalidated"]}},
                {"event": 1, "payload.reject_reason": 1, "_id": 0},
            )
            async for row in obs_cursor:
                payload = row.get("payload") or {}
                reason = str(payload.get("reject_reason") or row.get("event") or "unknown")
                counts[reason] = counts.get(reason, 0) + 1
        except Exception:  # noqa: BLE001
            pass

    taxonomy = summarize(counts)
    return {
        "since_seconds": since,
        "total": sum(taxonomy[k]["total"] for k in taxonomy),
        "taxonomy": taxonomy,
        "health_hint": health_hint(taxonomy),
    }


# ── Chasing-filter forensic audit ─────────────────────────────────


@router.get("/chasing-filter-audit")
async def chasing_filter_audit(request: Request, since_seconds: int = Query(86400)):
    """Read chasing_filter rejections in the window, classify each into
    ``clearly_extended`` / ``marginally_over`` / ``stale_signal`` /
    ``reference_anomaly`` / ``insufficient_data``, and return per-bucket
    counts + representative samples with the full reference-anchor
    payload. Powers the operator's "before we loosen the cap, prove
    the anchors are honest" audit."""
    await _require_admin(request)
    from server import db as _db
    from services.alpha_chasing_audit import audit_rejections
    from services.public_equity_live_executor import _max_intraday_move_pct
    env_cap = _max_intraday_move_pct()
    return await audit_rejections(_db, env_cap_pct=env_cap, since_seconds=since_seconds)

