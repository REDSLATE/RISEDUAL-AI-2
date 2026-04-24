"""Admin-only routes: cache monitoring, system diagnostics, broker OAuth config."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
import logging

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)

db = None

def set_db(database):
    global db
    db = database


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def _require_owner(request: Request):
    """Stricter gate than `_require_admin`: only the owner role passes.
    Used for endpoints that expose upstream-provider load signals, which
    are business-sensitive (reveals API budget pressure, traffic patterns,
    which symbols are hottest) and shouldn't be visible to lower-tier
    admins or any public caller.
    """
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ============================================================
# CACHE MANAGEMENT
# ============================================================

@router.get("/cache-stats")
async def get_cache_stats(request: Request):
    await _require_admin(request)
    from services.cache import cache
    return cache.stats()


@router.post("/cache-invalidate/{key}")
async def invalidate_cache_key(key: str, request: Request):
    await _require_admin(request)
    from services.cache import cache
    cache.invalidate(key)
    return {"ok": True, "invalidated": key}


@router.post("/cache-clear")
async def clear_all_cache(request: Request):
    await _require_admin(request)
    from services.cache import cache
    cache.clear()
    return {"ok": True, "message": "All cache cleared"}


@router.get("/price-cache-stats")
async def get_price_cache_stats(request: Request):
    """Stats on the sliding-TTL price cache shared by all price_provider
    entrypoints. Owner-only — exposes upstream-provider load patterns.

    - `size` / `alive`: total entries tracked vs. currently valid
    - `at_reset_cap`: entries that have hit `max_resets` — they still
      serve reads but won't extend expiry any more. If this climbs
      toward `alive` under load, upstream providers are about to be hit
      by a wave of refresh fetches — good leading indicator.
    - `ttl_seconds`, `max_resets`: effective cache policy
    """
    await _require_owner(request)
    from services.sliding_cache import price_cache
    return price_cache.stats()


@router.post("/price-cache-invalidate/{symbol}")
async def invalidate_price_cache(symbol: str, request: Request):
    """Force a fresh upstream fetch on the next read of `symbol`. Owner-only.
    Invalidates both quote and crypto keys since we don't know which applies."""
    await _require_owner(request)
    from services.sliding_cache import price_cache
    upper = symbol.upper()
    for key in (f"quote_{upper}", f"crypto_{upper}"):
        price_cache.invalidate(key)
    return {"ok": True, "invalidated": upper}


@router.get("/quiver-status")
async def quiver_status(request: Request):
    """Per-endpoint health + circuit-breaker state for QuiverQuant. Owner-only.

    Use this when gov filings / congress trading data looks thin — it tells
    you whether we're looking at a Quiver outage (circuit open) or just a
    quiet news day. Also shows cache pressure.
    """
    await _require_owner(request)
    from services.quiver_service import get_endpoint_health
    return get_endpoint_health()


@router.post("/usaspending-warmup")
async def usaspending_warmup(request: Request, limit: int = 500):
    """Manually trigger the USASpending.gov recipient→ticker warm-up job.
    Owner-only. Pre-resolves the top federal contractors so the first
    gov-contracts dashboard load is instant.

    Scheduled to run nightly at 03:30 UTC automatically — this endpoint
    is for on-demand re-runs (e.g. after clearing the cache, after a
    new contract cycle, or as a smoke test during ops review).
    """
    await _require_owner(request)
    from services.usaspending_service import warmup_top_recipients
    return await warmup_top_recipients(limit=min(max(limit, 10), 500))


@router.get("/usaspending-health")
async def usaspending_health(request: Request):
    """Cache + config snapshot for USASpending service. Owner-only."""
    await _require_owner(request)
    from services.usaspending_service import get_health
    return get_health()


@router.post("/retrain-now")
async def retrain_now(request: Request, max_samples: int = 50000):
    """Manually trigger a nightly ML retrain. Owner-only.

    Use this after a big data ingestion, when rolling forward from a bad
    model version, or simply to validate the pipeline end-to-end. Writes
    a new versioned artefact like the scheduled run — does NOT overwrite
    any prior model.
    """
    await _require_owner(request)
    from services.ml_retrain_service import run_nightly_retrain
    return await run_nightly_retrain(db, max_samples=max(200, min(max_samples, 200000)))


@router.post("/label-now")
async def label_now(request: Request):
    """Force a labeler run. Owner-only. Normally hourly via APScheduler."""
    await _require_owner(request)
    from services.ml_retrain_service import run_backfill_labeling
    return await run_backfill_labeling(db)


@router.get("/ml-training-history")
async def ml_training_history(request: Request, limit: int = 20):
    """Recent ML retrain runs with sample counts, versions, and errors.
    Owner-only. Pair with `GET /ml-latest-model` to see what's currently
    deployed."""
    await _require_owner(request)
    from services.ml_retrain_service import get_training_history
    runs = await get_training_history(db, limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/ml-latest-model")
async def ml_latest_model(request: Request):
    """Report the newest on-disk signal model artefact. Owner-only."""
    await _require_owner(request)
    from services.ml_retrain_service import get_latest_model_info
    info = get_latest_model_info()
    return info or {"version": None, "message": "No trained model artefacts on disk"}


# ============================================================
# CONVICTION CALIBRATION
# ============================================================

# Buckets mirror the tiers produced by `_compute_conviction()` in
# risk_calculator.py — boundaries chosen so "Strong" reliably trips the
# full-size multiplier and "Weak" reliably trips the 0x veto path.
CONVICTION_BUCKETS = [
    {"label": "Weak",     "min": 0.0, "max": 0.35, "tier": "weak"},
    {"label": "Moderate", "min": 0.35, "max": 0.65, "tier": "moderate"},
    {"label": "Strong",   "min": 0.65, "max": 1.01, "tier": "strong"},
]

# Fallback buckets for legacy predictions (no conviction attached). We bucket
# by raw `confidence` at the same cutoffs so the admin can still see a
# calibration curve while we accumulate conviction-tagged data.
CONFIDENCE_BUCKETS = [
    {"label": "Low",    "min": 0.0, "max": 0.35},
    {"label": "Medium", "min": 0.35, "max": 0.65},
    {"label": "High",   "min": 0.65, "max": 1.01},
]


@router.get("/conviction/calibration")
async def conviction_calibration(request: Request, days: int = 30):
    """Win-rate bucketed by Conviction score (and, as fallback, by raw
    confidence for legacy rows without conviction). Owner-only.

    A healthy model has monotonically rising win-rate across Weak →
    Moderate → Strong. If Moderate wins more than Strong, the hand-tuned
    CONVICTION_WEIGHTS in risk_calculator.py are miscalibrated — we
    should retrain them against actual outcomes.

    Also returns a 4-week trend series (oldest→newest) per bucket so the
    admin UI can sparkline-check for regime drift BEFORE the window-wide
    monotonicity badge flips.
    """
    await _require_owner(request)
    from datetime import timedelta
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    # Trend always covers the last 4 ISO-style weeks regardless of
    # `days` — this gives the sparkline a consistent x-axis while the
    # headline buckets respect the user-selected window.
    trend_weeks = 4
    trend_since = now - timedelta(days=trend_weeks * 7)
    fetch_since = min(since, trend_since)

    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$in": [True, False]},  # exclude NEUTRAL (stored as null)
            "timestamp": {"$gte": fetch_since.isoformat()},
        },
        {
            "_id": 0,
            "conviction": 1,
            "confidence": 1,
            "verified_24h.correct": 1,
            "timestamp": 1,
        },
    ).limit(10000)

    def _empty(buckets):
        # Include the raw cutoffs during aggregation; stripped in _finalise.
        return [
            {**b, "total": 0, "correct": 0, "win_rate": None}
            for b in buckets
        ]

    by_conviction = _empty(CONVICTION_BUCKETS)
    by_confidence = _empty(CONFIDENCE_BUCKETS)
    # Week 0 = oldest, week 3 = newest. Pre-seed so the sparkline always
    # has 4 points even when a week has zero data (we emit None, UI skips).
    weekly_conviction = [_empty(CONVICTION_BUCKETS) for _ in range(trend_weeks)]
    weekly_confidence = [_empty(CONFIDENCE_BUCKETS) for _ in range(trend_weeks)]
    total_verified = 0
    total_with_conviction = 0

    def _bucket_for(value, buckets):
        for b in buckets:
            if b["min"] <= value < b["max"]:
                return b
        return None

    def _week_index(ts_str: str):
        """Map an ISO timestamp → 0..3 week bucket, or None if out of range.

        Uses a naive `fromisoformat` + UTC-aware fallback. Older Python
        versions (pre-3.11) can't parse the trailing 'Z'; we strip it
        defensively so the endpoint stays portable.
        """
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return None
        delta_days = (now - ts).days
        if delta_days < 0 or delta_days >= trend_weeks * 7:
            return None
        # weeks ago: 0..trend_weeks-1 (0=this week). Flip so newest = last.
        weeks_ago = delta_days // 7
        return (trend_weeks - 1) - weeks_ago

    async for row in cursor:
        ts = row.get("timestamp", "")
        correct = bool((row.get("verified_24h") or {}).get("correct"))
        in_window = ts >= since.isoformat()
        wk = _week_index(ts)

        if in_window:
            total_verified += 1

        conv = (row.get("conviction") or {}).get("score")
        if isinstance(conv, (int, float)):
            if in_window:
                total_with_conviction += 1
                b = _bucket_for(conv, by_conviction)
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1
            if wk is not None:
                b = _bucket_for(conv, weekly_conviction[wk])
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1

        conf = row.get("confidence")
        if isinstance(conf, (int, float)):
            conf_norm = conf / 100.0 if conf > 1.0 else conf
            if in_window:
                b = _bucket_for(conf_norm, by_confidence)
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1
            if wk is not None:
                b = _bucket_for(conf_norm, weekly_confidence[wk])
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1

    def _finalise(buckets):
        out = []
        for b in buckets:
            win_rate = round(b["correct"] / b["total"], 4) if b["total"] else None
            out.append({
                "label": b["label"],
                "tier": b.get("tier"),
                "total": b["total"],
                "correct": b["correct"],
                "win_rate": win_rate,
                "range": [b["min"], b["max"]],
            })
        return out

    def _trend_series(weekly_buckets):
        """Pivot [week][bucket] → {bucket_label: [wr_week0, …, wr_week3]}.

        Returns None entries when a week had no data for that bucket so
        the UI can render gaps instead of misleading zero-points.
        """
        if not weekly_buckets:
            return {}
        labels = [b["label"] for b in weekly_buckets[0]]
        series = {lbl: [] for lbl in labels}
        for week in weekly_buckets:
            for b in week:
                wr = round(b["correct"] / b["total"], 4) if b["total"] else None
                series[b["label"]].append(wr)
        return series

    def _week_bounds():
        """Return the [start_iso, end_iso] pair for each of the 4 weeks."""
        out = []
        for i in range(trend_weeks):
            weeks_ago = (trend_weeks - 1) - i
            end = now - timedelta(days=weeks_ago * 7)
            start = end - timedelta(days=7)
            out.append([start.isoformat(), end.isoformat()])
        return out

    by_conv_out = _finalise(by_conviction)
    by_conf_out = _finalise(by_confidence)

    def _is_monotonic(buckets):
        rates = [b["win_rate"] for b in buckets if b["win_rate"] is not None]
        if len(rates) < 2:
            return None
        return all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))

    return {
        "window_days": days,
        "total_verified": total_verified,
        "total_with_conviction": total_with_conviction,
        "by_conviction": by_conv_out,
        "by_confidence": by_conf_out,
        "monotonic": {
            "conviction": _is_monotonic(by_conv_out),
            "confidence": _is_monotonic(by_conf_out),
        },
        "trend": {
            "weeks": trend_weeks,
            "bounds": _week_bounds(),
            "by_conviction": _trend_series(weekly_conviction),
            "by_confidence": _trend_series(weekly_confidence),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# ML TIER 3 PROGRESS + CONVICTION CLAMP CANARY (Admin)
# ============================================================

@router.get("/tier3-progress")
async def ml_tier3_progress(request: Request):
    """Progress toward the 30-day paper-trading gate that unlocks
    Tier 3 (live execution). Derived from `paper_trades.opened_at`
    so the count stays honest without manually editing env vars."""
    await _require_admin(request)
    from services.paper_trading_progress import tier3_progress
    return await tier3_progress(db)


@router.get("/conviction/clamp-canary")
async def conviction_clamp_canary(request: Request, days: int = 30):
    """Count prediction outcomes that hit the `score_prediction_outcome`
    boundary (±MIN_REWARD / MAX_PENALTY). Zero is the healthy state
    today; any non-zero count means `GRADE_WEIGHTS` drifted past the
    clamp or a confidence-scale bug is pushing scores beyond bounds."""
    await _require_admin(request)
    from services.conviction_clamp_canary import conviction_clamp_counter
    return await conviction_clamp_counter(db, days=days)


@router.get("/conviction/reliability")
async def conviction_reliability(request: Request, days: int = 30):
    """Classic reliability diagram — hit-rate bucketed by confidence
    decile (0, 10, 20, …, 100). Complements `/conviction/calibration`
    (which buckets by tier). Includes Expected Calibration Error
    (ECE) so the admin UI can flag "well_calibrated" at a glance."""
    await _require_admin(request)
    from services.calibration_reliability import reliability_snapshot
    days = max(1, min(int(days), 365))
    return await reliability_snapshot(db, days=days)


@router.get("/tier3-readiness")
async def tier3_readiness(request: Request, days: int = 30):
    """Composite Tier 3 unlock gate — 6 checks plus a 0-100 readiness
    score. Stricter than the simple 30-day + accuracy gate: adds
    high-confidence win-rate, calibration gap, strong-miss rate,
    last-7d stability, and the conviction clamp canary."""
    await _require_admin(request)
    from services.tier3_readiness import tier3_readiness_snapshot
    days = max(1, min(int(days), 365))
    return await tier3_readiness_snapshot(db, days=days)


# ============================================================
# CAPITAL ALLOCATION PREVIEW (fleet-wide)
# ============================================================

@router.get("/alpaca-health")
async def alpaca_health(request: Request):
    """Live diagnostic against the Alpaca account behind `ALPACA_API_KEY`.

    Returns:
      * account: status, equity, cash, trading_blocked
      * positions: split into longs/shorts + full detail (any short
        is surfaced explicitly — these are the "rogue shorts" the
        cover-order workflow was designed to close)
      * orders: last 50, sliced by terminal vs non-terminal state
        (pending/new/accepted/etc are flagged as "orphans")
      * verdict: high-level boolean summary (`covers_clean`,
        `no_orphan_orders`) so the UI can render a green/red strip.

    This is the programmatic equivalent of logging into the Alpaca
    dashboard — use it after cover-order workflows to confirm no
    shorts survived and no orders are stuck in a non-terminal state.
    """
    await _require_admin(request)

    import os
    from services.broker_service import AlpacaTradingService

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_SECRET_KEY")
    base_url = os.environ.get("ALPACA_BASE_URL", "")
    if not api_key or not api_secret:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    paper = "paper" in base_url.lower()
    svc = AlpacaTradingService(api_key=api_key, api_secret=api_secret, paper=paper)

    account = svc.get_account() or {}
    positions = svc.get_positions() or []
    orders = svc.get_orders(status="all", limit=50) or []

    longs = [p for p in positions if float(p.get("qty", 0)) > 0]
    shorts = [p for p in positions if float(p.get("qty", 0)) < 0]

    # Non-terminal statuses that indicate an orphaned order.
    NON_TERMINAL = {"new", "pending_new", "accepted", "pending_cancel",
                    "accepted_for_bidding", "held", "replaced"}
    orphans = [o for o in orders if o.get("status") in NON_TERMINAL]

    return {
        "mode": "paper" if paper else "live",
        "account": {
            "status": account.get("status"),
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "trading_blocked": account.get("trading_blocked"),
            "account_blocked": account.get("account_blocked"),
        },
        "positions": {
            "total": len(positions),
            "longs": len(longs),
            "shorts": len(shorts),
            "short_detail": [
                {"symbol": p.get("symbol"), "qty": p.get("qty"),
                 "unrealized_pl": p.get("unrealized_pl"),
                 "market_value": p.get("market_value")}
                for p in shorts
            ],
        },
        "orders": {
            "total": len(orders),
            "orphan_count": len(orphans),
            "orphan_detail": [
                {"symbol": o.get("symbol"), "side": o.get("side"),
                 "qty": o.get("qty"), "filled_qty": o.get("filled_qty"),
                 "status": o.get("status"),
                 "submitted_at": o.get("submitted_at")}
                for o in orphans
            ],
        },
        "verdict": {
            "covers_clean": len(shorts) == 0,
            "no_orphan_orders": len(orphans) == 0,
            "trading_enabled": not (account.get("trading_blocked")
                                    or account.get("account_blocked")),
        },
    }


@router.get("/allocation-preview")
async def allocation_preview(request: Request, total_capital: float = 10000.0):
    """Preview how a given USD pool would be split across the enabled
    trading-bot fleet, using :func:`ai_core.allocate_capital`.

    Each bot is scored on `(win_rate, pnl)` — winners get a larger
    slice, losing bots get a 0.1 floor so they can rehabilitate rather
    than being starved of capital.

    Data source: the `trading_bots` Mongo collection (enabled bots
    only). We read `stats.pnl` directly. `win_rate` is read from
    `stats.win_rate` if the bot has it, else we derive it from
    `stats.winning_trades / stats.trades` when both exist, else we
    hand `None` to :func:`compute_bot_score` which falls back to 0.5
    (neutral) — matches how a brand-new bot gets a fair initial share.

    Useful for deciding whether to toggle new bots on: the preview
    tells you exactly who would get what at the next capital
    rebalance, without actually moving any money.
    """
    await _require_admin(request)

    if total_capital <= 0:
        raise HTTPException(status_code=400, detail="total_capital must be positive")

    from ai_core.drawdown_allocator import allocate_capital, compute_bot_score

    enabled_bots = await db.trading_bots.find(
        {"enabled": True}, {"_id": 0, "name": 1, "type": 1, "stats": 1}
    ).to_list(length=100)

    if not enabled_bots:
        return {
            "total_capital": total_capital,
            "bot_count": 0,
            "allocations": {},
            "scores": {},
            "note": "no enabled bots",
        }

    # Name-key each bot, deriving win_rate when absent but computable.
    bots_for_allocator: dict[str, dict] = {}
    for b in enabled_bots:
        name = b.get("name") or f"{b.get('type', 'bot')}-{len(bots_for_allocator)}"
        stats = b.get("stats") or {}
        win_rate = stats.get("win_rate")
        if win_rate is None:
            trades = stats.get("trades") or 0
            wins = stats.get("winning_trades")
            if wins is not None and trades > 0:
                win_rate = wins / trades
        bots_for_allocator[name] = {
            "win_rate": win_rate,  # None → scorer defaults to 0.5
            "pnl": stats.get("pnl", 0),
        }

    allocations = allocate_capital(total_capital, bots_for_allocator)
    # Return the scores alongside so the UI can show "why" each bot got
    # its slice — matches how the ConvictionCalibration panel works.
    scores = {name: round(compute_bot_score(s), 4)
              for name, s in bots_for_allocator.items()}

    return {
        "total_capital": total_capital,
        "bot_count": len(enabled_bots),
        "allocations": allocations,
        "scores": scores,
        "bots": bots_for_allocator,
    }


@router.get("/gather-error-rate")
async def gather_error_rate(
    request: Request,
    hours: int = 24,
    context_prefix: str | None = None,
):
    """Aggregate recent structured `log_error` events by `context` tag.

    Every `services.structured_log.log_error` call (which includes every
    failure unwrapped by `unwrap_gather_result`) is pushed into an
    in-process rolling buffer — see `services.error_metrics`. This
    endpoint slices that buffer by window + optional prefix and groups
    the result so operators can see "which provider is flaking over
    the last N hours" at a glance.

    Query params:
      * `hours` — look-back window (default 24, cap 168).
      * `context_prefix` — optional. Restrict to contexts starting with
        this string (e.g. `market_data` covers both ticker + crypto;
        `war_room` scopes to the War Room fetch fan-out).

    Response shape:
      * `by_context` — ordered list of `{context, count, top_types, last_seen}`,
        sorted by count desc.
      * `total_errors` — sum across all contexts in the window.
      * `window_hours` — echoed back so the UI can label the tile.

    In-memory by design: restarts clear the buffer. That matches the
    tile's purpose (live operational view), not an audit log.
    """
    await _require_admin(request)

    if hours <= 0 or hours > 168:
        raise HTTPException(
            status_code=400,
            detail="hours must be between 1 and 168 (7 days)",
        )

    from services.error_metrics import snapshot_since
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = snapshot_since(cutoff)

    if context_prefix:
        events = [e for e in events if e["context"].startswith(context_prefix)]

    # Group by context; track total count, top 3 exception types seen,
    # and most-recent timestamp. Two passes is fine — bounded buffer.
    from collections import Counter
    grouped: dict[str, dict] = {}
    for e in events:
        ctx = e["context"] or "(none)"
        slot = grouped.setdefault(ctx, {
            "context": ctx,
            "count": 0,
            "type_counter": Counter(),
            "last_seen": e["ts"],
        })
        slot["count"] += 1
        slot["type_counter"][e["type"] or "(none)"] += 1
        if e["ts"] > slot["last_seen"]:
            slot["last_seen"] = e["ts"]

    by_context = []
    for slot in grouped.values():
        by_context.append({
            "context": slot["context"],
            "count": slot["count"],
            "top_types": [
                {"type": t, "count": n}
                for t, n in slot["type_counter"].most_common(3)
            ],
            "last_seen": slot["last_seen"].isoformat(),
        })
    by_context.sort(key=lambda r: r["count"], reverse=True)

    return {
        "window_hours": hours,
        "context_prefix": context_prefix,
        "total_errors": len(events),
        "distinct_contexts": len(by_context),
        "by_context": by_context,
    }


@router.get("/kill-switch")
async def kill_switch_status(request: Request):
    """Current state of the global fleet-wide kill switch.

    Returns `active` flag, trip reason + timestamp, cooldown
    remaining, trip count since process start, current rolling
    error rate, and the config thresholds (so the UI can render
    "15 / 30% error rate" style comparisons).

    In-process by design — matches the gather-error tile's
    philosophy. Restarts wipe both flag and error window, which is
    the conservative choice (fleet comes back *armed but ready*,
    not *latched-off from a past incident*).
    """
    await _require_admin(request)
    from ai_core.kill_switch import kill_switch
    return kill_switch.status()


@router.post("/kill-switch/reset")
async def kill_switch_reset(request: Request):
    """Force-clear the kill switch (owner only).

    Wipes the active flag **and** the error window — the
    alternative (flag-only) leaves the window full of stale
    failures that would immediately re-trip the next guarded
    execution. That's not a reset, that's a stutter.
    """
    await _require_owner(request)
    from ai_core.kill_switch import kill_switch
    before = kill_switch.status()
    kill_switch.reset()
    return {
        "cleared": True,
        "previous": {
            "active": before["active"],
            "last_reason": before["last_reason"],
            "trip_count": before["trip_count"],
        },
        "current": kill_switch.status(),
    }


# ============================================================
# BROKER OAUTH CONFIGURATION (Owner only)
# ============================================================

@router.get("/broker-oauth")
async def get_broker_oauth_config(request: Request):
    """Get OAuth configuration status for all supported brokers. Owner only."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    configs = {}
    cursor = db.broker_oauth_config.find({}, {"_id": 0, "client_secret_enc": 0})
    async for doc in cursor:
        configs[doc["broker_id"]] = {
            "broker_id": doc["broker_id"],
            "configured": True,
            "client_id_preview": doc.get("client_id_preview", ""),
            "updated_at": doc.get("updated_at", ""),
            "updated_by": doc.get("updated_by", ""),
        }

    # Include supported brokers even if not configured
    from routes.broker import OAUTH_CONFIGS
    for broker_id in OAUTH_CONFIGS:
        if broker_id not in configs:
            configs[broker_id] = {
                "broker_id": broker_id,
                "configured": False,
                "client_id_preview": "",
            }

    return {"brokers": configs}


@router.post("/broker-oauth/{broker_id}")
async def set_broker_oauth_config(broker_id: str, request: Request):
    """Set or update OAuth Client ID and Secret for a broker. Owner only.

    Body: {"client_id": "...", "client_secret": "..."}
    """
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    from routes.broker import OAUTH_CONFIGS, encrypt_value
    if broker_id not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Broker '{broker_id}' does not support OAuth")

    body = await request.json()
    client_id = body.get("client_id", "").strip()
    client_secret = body.get("client_secret", "").strip()

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Both client_id and client_secret are required")

    # Store encrypted secret, plaintext ID (needed for OAuth redirects)
    doc = {
        "broker_id": broker_id,
        "client_id": client_id,
        "client_id_preview": f"{client_id[:8]}...{client_id[-4:]}" if len(client_id) > 12 else client_id[:4] + "...",
        "client_secret_enc": encrypt_value(client_secret),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": user.get("email", "unknown"),
    }

    await db.broker_oauth_config.update_one(
        {"broker_id": broker_id},
        {"$set": doc},
        upsert=True,
    )

    logger.info(f"Broker OAuth config updated for {broker_id} by {user.get('email')}")
    return {
        "status": "configured",
        "broker_id": broker_id,
        "client_id_preview": doc["client_id_preview"],
        "message": f"OAuth credentials saved for {broker_id}",
    }


@router.delete("/broker-oauth/{broker_id}")
async def delete_broker_oauth_config(broker_id: str, request: Request):
    """Remove OAuth configuration for a broker. Owner only."""
    user = await _require_admin(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    result = await db.broker_oauth_config.delete_one({"broker_id": broker_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No OAuth config found for this broker")

    return {"status": "deleted", "broker_id": broker_id}


# ── Codebase Download ──

@router.get("/download/codebase-txt")
async def download_codebase_txt(request: Request):
    """Download the entire codebase as a single .txt file."""
    await _require_admin(request)
    import os
    from fastapi.responses import Response

    EXTENSIONS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.json', '.md', '.txt', '.yml', '.yaml', '.toml', '.cfg'}
    SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.emergent', 'chromadb', 'dist', 'build', '.next', 'venv', '.venv'}
    SKIP_FILES = {'.env', '.env.test', '.env.local', '.env.production'}
    MAX_FILE_SIZE = 200_000  # skip files > 200KB

    lines = []
    lines.append("=" * 80)
    lines.append("RISEDUAL AI — Complete Source Code Export")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 80)
    lines.append("")

    file_count = 0
    for root, dirs, files in os.walk("/app"):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in EXTENSIONS or fname in SKIP_FILES:
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, "/app")
            try:
                size = os.path.getsize(fpath)
                if size > MAX_FILE_SIZE:
                    lines.append(f"\n{'─' * 80}")
                    lines.append(f"FILE: {rel}  [SKIPPED — {size:,} bytes]")
                    continue
                with open(fpath, "r", errors="replace") as f:
                    content = f.read()
                lines.append(f"\n{'─' * 80}")
                lines.append(f"FILE: {rel}  ({len(content.splitlines())} lines)")
                lines.append("─" * 80)
                lines.append(content)
                file_count += 1
            except Exception:
                pass

    lines.insert(4, f"Files: {file_count}")
    body = "\n".join(lines)

    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=RISEDUAL_AI_Codebase.txt"},
    )



# ============================================================
# LEARNING ENGINE
# ============================================================

@router.get("/learning-engine/summary")
async def learning_engine_summary(request: Request):
    """Fleet-wide trade outcome roll-up: wins / losses / pending / win-rate
    / expectancy_r. Owner-only. Reads the canonical ai_core counters."""
    await _require_owner(request)
    from ai_core import LearningEngine
    return await LearningEngine(db).get_summary()


@router.get("/learning-engine/trades")
async def learning_engine_trades(
    request: Request, limit: int = 50, status: str = None,
):
    """Recent trade records from the ai_core learning_engine_trades
    collection. `status` filter accepts win|loss|pending. Owner-only."""
    await _require_owner(request)
    from ai_core import LearningEngine
    limit = max(1, min(int(limit), 500))
    return {
        "count": 0 if status else None,
        "items": await LearningEngine(db).recent_trades(limit=limit, status=status),
    }



# ============================================================
# ALERT AUDIT — dedup + delivery forensics
# ============================================================

@router.get("/alerts/audit")
async def alerts_audit(
    request: Request,
    alert_type: str | None = None,
    limit: int = 25,
):
    """Last N rows from `alerts_sent` with dedup + delivery metadata.

    Surfaces the reserve-first pipeline's forensic trail: `alert_id`,
    `run_id`, ticker set, persistence run, and any email delivery
    failures. Admin-only (not owner-only — lower-tier admins also
    investigate alert issues).
    """
    await _require_admin(request)
    if db is None:
        return {"items": [], "total": 0}

    limit = max(1, min(int(limit), 200))
    query: dict = {}
    if alert_type:
        query["alert_type"] = alert_type

    cursor = db["alerts_sent"].find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = []
    async for row in cursor:
        meta = row.get("metadata") or {}
        items.append({
            "alert_id": row.get("alert_id"),
            "alert_type": row.get("alert_type"),
            "created_at": row.get("created_at"),
            "date_bucket": row.get("date_bucket"),
            "run_id": meta.get("run_id"),
            "toxic_count": meta.get("toxic_count"),
            "affected_tickers": (meta.get("affected_tickers") or [])[:10],
            "persistence_run": meta.get("persistence_run"),
            "delivery_attempts": meta.get("delivery_attempts", 1),
            "email_recipients": meta.get("email_recipients") or [],
            "email_failed": bool(meta.get("email_failed", False)),
            "email_failed_recipients": meta.get("email_failed_recipients") or [],
            "email_replayed_at": meta.get("email_replayed_at"),
            "replayable": bool(meta.get("replay_payload")) and bool(meta.get("email_failed_recipients")),
        })

    total = await db["alerts_sent"].count_documents(query)
    return {"items": items, "total": total, "limit": limit}


@router.post("/alerts/replay")
async def alerts_replay(request: Request, alert_id: str):
    """Replay email delivery to the failed recipients of a past alert.

    Reads `metadata.replay_payload` from the reserved `alerts_sent`
    row — no re-running of the expensive nightly-cleanup scan, no new
    reserve. Only recipients in `email_failed_recipients` are retried;
    successful recipients are left alone so we never double-send.

    On each call, `delivery_attempts` is incremented and
    `email_replayed_at` is stamped. The failed-recipients list shrinks
    to only those that still failed this attempt — repeated replays
    converge on either success or a persistent-failure shortlist.

    Admin-gated.
    """
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    alert = await db["alerts_sent"].find_one({"alert_id": alert_id}, {"_id": 0})
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    meta = alert.get("metadata") or {}
    failed = list(meta.get("email_failed_recipients") or [])
    if not failed:
        return {"status": "no_failed_recipients", "alert_id": alert_id}

    payload = meta.get("replay_payload")
    if not payload:
        # Legacy rows reserved before replay_payload was added — refuse
        # rather than send a degraded email.
        raise HTTPException(
            status_code=409,
            detail="Alert pre-dates replay support (no replay_payload stored)",
        )

    from services.email_service import send_toxic_spikes_email

    still_failed: list[dict[str, str]] = []
    replayed_ok: list[str] = []
    for entry in failed:
        email = entry.get("email", "")
        if not email or email == "*":
            # "*" is the catch-all marker used when the whole send
            # block crashed — can't replay an unknown address.
            still_failed.append(entry)
            continue
        ok = await send_toxic_spikes_email(
            recipient_email=email,
            toxic_count=int(payload.get("toxic_count", 0)),
            obsolete_count=int(payload.get("obsolete_count", 0)),
            total_before=int(payload.get("total_before", 0)),
            total_after=int(payload.get("total_after", 0)),
            spike_details=payload.get("spike_details") or [],
            persistence_tag=str(payload.get("persistence_tag", "")),
        )
        if ok:
            replayed_ok.append(email)
        else:
            still_failed.append({
                "email": email,
                "error": "send_failed_or_no_provider",
            })

    # Merge: successful replays move into email_recipients, failures
    # stay (or are refreshed) in email_failed_recipients.
    existing_recipients = list(meta.get("email_recipients") or [])
    merged_recipients = list({*existing_recipients, *replayed_ok})
    await db["alerts_sent"].update_one(
        {"alert_id": alert_id},
        {
            "$set": {
                "metadata.email_recipients": merged_recipients,
                "metadata.email_failed_recipients": still_failed,
                "metadata.email_failed": len(still_failed) > 0,
                "metadata.email_replayed_at": datetime.now(timezone.utc).isoformat(),
            },
            "$inc": {"metadata.delivery_attempts": 1},
        },
    )

    new_attempts = int(meta.get("delivery_attempts", 1)) + 1
    # Narrate the replay into the agent activity feed so admins see
    # it from the dashboard, not just the Audit panel.
    try:
        from services.agent_activity_service import log_alert_replay
        await log_alert_replay(
            alert_id=alert_id,
            alert_type=alert.get("alert_type", "toxic_spike"),
            replayed=replayed_ok,
            still_failed=still_failed,
            delivery_attempts=new_attempts,
        )
    except Exception:
        pass

    return {
        "status": "replayed",
        "alert_id": alert_id,
        "replayed": replayed_ok,
        "still_failed": still_failed,
        "delivery_attempts": new_attempts,
    }



# ============================================================
# "WHY DID THIS ALERT FIRE?" — feature-level drilldown
# ============================================================


def _extract_drivers(snap: dict, failure_code: str | None = None) -> list[str]:
    """Turn a features_snapshots row into human-readable driver
    strings, steered by the alert's ``failure_code`` when present.

    Two layers:

    1. **Failure-code specific** (high-signal, explains what actually
       went wrong). The caller passes `failure_code` from the
       ChromaDB toxic row. Codes map to the canonical
       ``services/post_mortem_service.FAILURE_MODES`` vocabulary:
       ``TECH_FAKEOUT``, ``LIQUIDITY_GAP``, ``REGIME_SHIFT`` (trend
       exhaustion / overextension), ``MACRO_SHOCK`` (sector/macro
       mismatch), and ``UNKNOWN``.

    2. **Fallback heuristics** (medium-signal) fill the remaining
       slots when the failure-code layer didn't produce enough.

    Each driver carries a weight so the UI implicitly ranks "strongest
    cause" first — failure-code-specific hits weigh more than generic
    extreme-reading hits.

    Returns the top 3 labels (strings), sorted by weight desc.
    Safe on empty/null feature values.
    """
    if not snap:
        return []

    drivers: list[dict] = []

    def add(label: str, weight: float, metric: str) -> None:
        """`metric` is a canonical key (rsi, volume, sector, ...) so
        we can dedup across the failure-code + fallback layers when
        both phrasings describe the same underlying signal."""
        drivers.append({"label": label, "weight": weight, "metric": metric})

    rsi = snap.get("rsi_14")
    vol = snap.get("volume_ratio")
    macd = snap.get("macd")
    macd_sig = snap.get("macd_signal")
    sector = snap.get("sector_momentum")
    sentiment = snap.get("sentiment_score")

    # ── 1. Failure-code specific overrides (HIGH SIGNAL) ──
    if failure_code == "TECH_FAKEOUT":
        if snap.get("pattern_bull_flag"):
            add("bull flag broke down", 0.95, "pattern")
        if isinstance(macd, (int, float)) and macd < 0:
            add("bearish momentum reversal", 0.9, "macd")

    elif failure_code == "LIQUIDITY_GAP":
        if isinstance(vol, (int, float)) and vol < 0.8:
            add(f"low liquidity ({vol:.2f}x volume)", 0.95, "volume")
        add("slippage / spread expansion", 0.85, "liquidity")

    elif failure_code == "REGIME_SHIFT":
        # Overextension / trend exhaustion lives under REGIME_SHIFT
        # in our FAILURE_MODES vocabulary.
        if isinstance(rsi, (int, float)) and rsi > 70:
            add(f"overbought RSI ({int(rsi)})", 0.95, "rsi")
        add("trend exhaustion", 0.85, "trend")

    elif failure_code == "MACRO_SHOCK":
        if isinstance(sector, (int, float)) and sector < 0:
            add(f"negative sector momentum ({sector * 100:+.1f}%)", 0.95, "sector")
        add("macro regime misalignment", 0.85, "macro")

    # ── 2. Fallback heuristics (MEDIUM SIGNAL) ──
    # Only fill slots that the failure-code layer didn't already
    # claim — we dedup below by `metric`, so anything tagged with an
    # already-seen metric is silently dropped.
    if len(drivers) < 3:
        if isinstance(rsi, (int, float)):
            if rsi > 70:
                add(f"overbought RSI ({int(rsi)})", 0.6, "rsi")
            elif rsi < 30:
                add(f"oversold RSI ({int(rsi)})", 0.6, "rsi")

        if isinstance(vol, (int, float)):
            if vol < 0.8:
                add(f"low volume ({vol:.2f}x)", 0.55, "volume")
            elif vol > 1.5:
                add(f"volume spike ({vol:.2f}x)", 0.55, "volume")

        if isinstance(macd, (int, float)) and isinstance(macd_sig, (int, float)):
            if macd < macd_sig and macd < 0:
                add("MACD bearish crossover", 0.6, "macd")

        if isinstance(sector, (int, float)) and sector < -0.02:
            add(f"negative sector ({sector * 100:+.1f}%)", 0.55, "sector")

        if isinstance(sentiment, (int, float)) and sentiment < -0.3:
            add(f"negative sentiment ({sentiment:+.2f})", 0.5, "sentiment")

        if snap.get("pattern_rsi_divergence"):
            add("RSI divergence", 0.55, "pattern_divergence")
        if snap.get("pattern_head_and_shoulders"):
            add("head & shoulders pattern", 0.55, "pattern_hs")
        if snap.get("pattern_bearish_engulfing"):
            add("bearish engulfing", 0.55, "pattern_engulf")

    # Rank by weight desc, then dedup by canonical metric so we never
    # show two phrasings of the same underlying signal. Failure-code
    # variants always win because their weights are higher.
    seen_metrics: set[str] = set()
    unique: list[dict] = []
    for d in sorted(drivers, key=lambda d: d["weight"], reverse=True):
        if d["metric"] in seen_metrics:
            continue
        seen_metrics.add(d["metric"])
        unique.append(d)

    return [d["label"] for d in unique[:3]]


@router.get("/alerts/why/{alert_id}")
async def alert_why(alert_id: str, request: Request):
    """Feature-level drilldown: for each affected ticker on the
    alert, pull the most-recent ``features_snapshots`` row and run
    heuristic driver extraction against real feature columns.

    Best-effort join: ``features_snapshots`` rarely stores
    ``prediction_id`` in this deployment (only ~25 / 276k rows), so
    we can't perfectly match the ChromaDB toxic row to its feature
    snapshot. Instead we grab the most recent snapshot per ticker
    as a "what was the model seeing around that time?" proxy.
    When no snapshot exists for a ticker we just omit it from the
    result — the client already has the ChromaDB-level confidence
    + failure_code via the ``alert_reserved`` event metadata and
    can render a degraded row.

    Admin-only.
    """
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    alert = await db["alerts_sent"].find_one({"alert_id": alert_id}, {"_id": 0})
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    meta = alert.get("metadata") or {}
    # spike_details lives on replay_payload for current-generation
    # rows; fall back to affected_tickers for anything older.
    spikes = ((meta.get("replay_payload") or {}).get("spike_details")
              or [])
    if not spikes:
        spikes = [
            {"symbol": t, "confidence": None, "failure_code": "UNKNOWN"}
            for t in (meta.get("affected_tickers") or [])
        ]

    enriched = []
    for spike in spikes[:10]:
        ticker = spike.get("symbol")
        if not ticker or ticker == "?":
            continue
        snap = await db["features_snapshots"].find_one(
            {"ticker": ticker},
            {"_id": 0, "rsi_14": 1, "volume_ratio": 1, "macd": 1,
             "macd_signal": 1, "sector_momentum": 1, "sentiment_score": 1,
             "regime_label": 1, "timestamp": 1,
             "pattern_rsi_divergence": 1, "pattern_head_and_shoulders": 1,
             "pattern_bearish_engulfing": 1, "pattern_double_bottom": 1,
             "pattern_bull_flag": 1},
            sort=[("timestamp", -1)],
        )
        failure_code = spike.get("failure_code", "UNKNOWN")
        drivers = _extract_drivers(snap, failure_code) if snap else []
        enriched.append({
            "symbol": ticker,
            "confidence": spike.get("confidence"),
            "failure_code": failure_code,
            "date": spike.get("date"),
            "regime": (snap or {}).get("regime_label"),
            "snapshot_at": (snap or {}).get("timestamp"),
            "drivers": drivers,
        })

    return {
        "alert_id": alert_id,
        "toxic_count": meta.get("toxic_count", 0),
        "alert_type": alert.get("alert_type", "toxic_spike"),
        "items": enriched,
    }
