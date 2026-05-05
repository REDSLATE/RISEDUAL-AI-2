"""Admin-only routes: cache monitoring, system diagnostics, broker OAuth config."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timedelta, timezone
from typing import Any
from pydantic import BaseModel
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

@router.get("/symbol-failure/{symbol}")
async def get_symbol_failure_status(symbol: str, request: Request, asset_type: str = "equity"):
    """Show recent miss count + the penalty that would be applied to the
    next signal for this symbol. Owner-only."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from dataclasses import asdict
    from services.symbol_failure_memory import get_failure_penalty
    penalty = await get_failure_penalty(
        db, symbol=symbol.upper(), asset_type=asset_type,
    )
    return {"symbol": symbol.upper(), "asset_type": asset_type, "penalty": asdict(penalty)}


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


@router.get("/ml-retrain-cost-trend")
async def ml_retrain_cost_trend(request: Request, limit: int = 30):
    """Per-run wall/CPU cost + throughput for the last N successful retrains.

    Complements `/ml-training-history` by surfacing the timing telemetry
    added to `ml_training_log` (fit_wall_seconds, fit_cpu_seconds,
    fit_cpu_threads_equiv). Returns chart-ready chronological rows, scalar
    aggregates (mean + p95), and a thread-binding health verdict derived
    from the 3 most recent runs — so a regression in the `n_jobs` cap
    surfaces as a red badge instead of requiring a Mongo query.

    Owner-only. `limit` is clamped to 500 server-side.
    """
    await _require_owner(request)
    from services.ml_retrain_service import get_retrain_cost_trend
    return await get_retrain_cost_trend(db, limit=limit)


# ============================================================
# TOP UNIVERSE (tiered stock pre-warm)
# ============================================================

@router.get("/top-universe/status")
async def top_universe_status(request: Request):
    """Current tiered-universe snapshot + last warm/rebuild telemetry.

    Backs the universe admin panel. Shape:
        - active_total, by_tier, tier_sizes
        - last_rebuild, last_warm (full stats row each)
        - recent_coverage (succeeded/attempted over the last 10 warms)
        - sample (first 20 rows, rank-ordered)
    """
    await _require_owner(request)
    from services.top_universe_service import get_status
    return await get_status(db)


@router.get("/top-universe/history")
async def top_universe_history(request: Request, limit: int = 30):
    """Chronological warm + rebuild history from `universe_warm_stats`.

    Used by the admin panel to render the trend of coverage + wall-time
    per run. Limit clamped to 200 server-side.
    """
    await _require_owner(request)
    from services.top_universe_service import get_recent_warm_stats
    rows = await get_recent_warm_stats(db, limit=limit)
    return {"runs": rows, "count": len(rows)}


@router.post("/top-universe/warm")
async def top_universe_warm(request: Request, run_type: str = "post_close"):
    """Fire a universe warm asynchronously. Returns immediately with
    `{"status":"started"}`; poll `/top-universe/status` for completion
    (the warm writes its stats row when done).

    Returns HTTP 202. Runs as a detached asyncio task so long-running
    warms (1–4 min on 200 symbols) don't hit the ingress 60s timeout.
    Owner-only.
    """
    await _require_owner(request)
    if run_type not in ("post_close", "pre_open"):
        raise HTTPException(status_code=400, detail="run_type must be 'post_close' or 'pre_open'")
    import asyncio as _asyncio
    from services.top_universe_service import warm_universe

    async def _bg():
        try:
            await warm_universe(db, run_type=run_type)
        except Exception:
            logger.exception("top-universe warm (%s) failed", run_type)

    _asyncio.create_task(_bg())
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "started", "run_type": run_type,
                 "poll": "/api/admin/top-universe/status"},
    )


@router.post("/top-universe/rebuild")
async def top_universe_rebuild(request: Request):
    """Fire a universe rebuild asynchronously. Returns HTTP 202; the
    actual ranking run (~500 AV OVERVIEW calls, 3–4 minutes) continues
    in the background and writes its stats row on completion.

    Normally scheduled weekly on Sunday 00:00 UTC; this endpoint is for
    forced refreshes after seed edits or a new-ticker event. Owner-only.
    """
    await _require_owner(request)
    import asyncio as _asyncio
    from services.top_universe_service import rebuild_universe

    async def _bg():
        try:
            await rebuild_universe(db)
        except Exception:
            logger.exception("top-universe rebuild failed")

    _asyncio.create_task(_bg())
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "started", "poll": "/api/admin/top-universe/status"},
    )


# ============================================================
# OPTIONS UNIVERSE (liquidity-filtered top-N per underlying)
# ============================================================

@router.get("/options-universe/status")
async def options_universe_status(request: Request):
    """Current options-universe snapshot + last warm telemetry.

    Reads the single ``option_universe`` doc (_id="current") and the
    latest ``universe_warm_stats`` row for run_type="options_warm".
    Returns shape:
        - snapshot_updated_at
        - underlyings_configured, top_n_per_symbol
        - symbols_with_data, symbols_with_hot_flow, contracts_by_symbol
        - data: list of {symbol, contracts: [top-N], aggregate: {PCR, ...}}
        - last_warm: stats row (includes wall_seconds, skipped-if-closed)
        - is_market_open: bool
    """
    await _require_owner(request)
    from services.options_universe_service import get_options_status
    return await get_options_status(db)


@router.post("/options-universe/warm")
async def options_universe_warm(request: Request, force: bool = True):
    """Fire the options warm asynchronously. Returns HTTP 202.

    By default (``force=true``) bypasses the market-hours gate — this
    endpoint is for manual admin triggers. The scheduled job runs with
    ``force=False`` so it no-ops outside US regular session. Owner-only.
    """
    await _require_owner(request)
    import asyncio as _asyncio
    from services.options_universe_service import warm_options_universe

    async def _bg():
        try:
            await warm_options_universe(db, force=force)
        except Exception:
            logger.exception("options-universe warm failed")

    _asyncio.create_task(_bg())
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "started", "force": force,
                 "poll": "/api/admin/options-universe/status"},
    )


@router.get("/options-universe/p90-alerts")
async def options_universe_p90_alerts(request: Request, limit: int = 50):
    """Recent p90 spread-widening alerts from ``option_universe_p90_alerts``.

    The watcher fires one alert per symbol when p90 rises ≥ 50% while
    the chain-wide avg stays under ±20% drift within a ≈15-min window.
    Dedupe is 30 min per symbol. Owner-only.
    """
    await _require_owner(request)
    from services.options_p90_watcher import get_recent_alerts
    rows = await get_recent_alerts(db, limit=limit)
    return {"alerts": rows, "count": len(rows)}


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

    def _ece(buckets):
        """Expected Calibration Error — weighted-mean gap between
        each bucket's midpoint confidence and its actual accuracy.

        Adapted from the RISEDUAL CLI prototype's auditor.py.
        Standard formula: ECE = Σ (n_b / N) × |conf_b − acc_b|.

        We use the bucket midpoint as the avg confidence in the
        bucket — slightly imprecise vs. tracking per-prediction
        confidences but standard for binned visualisations and
        directionally identical for monotonicity-style alarms.
        Returns None when fewer than 2 buckets have data.
        """
        observed = [b for b in buckets if b["total"] > 0 and b["win_rate"] is not None]
        if len(observed) < 2:
            return None
        n_total = sum(b["total"] for b in observed)
        if n_total == 0:
            return None
        gap = 0.0
        for b in observed:
            lo, hi = b["range"]
            midpoint = (lo + hi) / 2.0
            gap += (b["total"] / n_total) * abs(midpoint - b["win_rate"])
        return round(gap, 4)

    ece_conv = _ece(by_conv_out)
    ece_conf = _ece(by_conf_out)

    def _calibration_notes():
        """Auto-generated single-sentence operator guidance.
        Adapted from the AuditReport.notes pattern in the RISEDUAL
        CLI prototype."""
        out = []
        if total_verified < 30:
            out.append(
                f"Only {total_verified} verified predictions in the window — "
                "calibration numbers are noisy below ~30 samples."
            )
        # ECE thresholds borrowed from the prototype: >0.10 = poor,
        # >0.05 = borderline. Below that = healthy.
        if ece_conv is not None and ece_conv > 0.10:
            out.append(
                f"Conviction ECE = {ece_conv:.3f} (poor); model is overconfident "
                "or underconfident relative to actual win rate."
            )
        elif ece_conv is not None and ece_conv > 0.05:
            out.append(f"Conviction ECE = {ece_conv:.3f} — borderline calibration.")
        if ece_conf is not None and ece_conf > 0.10:
            out.append(
                f"Confidence ECE = {ece_conf:.3f} (poor); raw confidence scores "
                "diverge meaningfully from realised win rate."
            )
        # Monotonicity flags — these come BEFORE ECE wording in the
        # UI so the operator sees the structural failure first.
        mono_conv = _is_monotonic(by_conv_out)
        mono_conf = _is_monotonic(by_conf_out)
        if mono_conv is False:
            out.append(
                "Conviction win-rate is non-monotonic (Moderate beats Strong, "
                "or similar) — CONVICTION_WEIGHTS need retraining."
            )
        if mono_conf is False:
            out.append("Confidence win-rate is non-monotonic across buckets.")
        if not out:
            out.append("Calibration is healthy across all buckets.")
        return out

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
        # Expected Calibration Error — single-number health metric
        # (lower = better; ≤0.05 healthy, ≤0.10 borderline, >0.10 poor).
        "ece": {
            "conviction": ece_conv,
            "confidence": ece_conf,
        },
        "trend": {
            "weeks": trend_weeks,
            "bounds": _week_bounds(),
            "by_conviction": _trend_series(weekly_conviction),
            "by_confidence": _trend_series(weekly_confidence),
        },
        # Auto-generated operator guidance based on monotonicity +
        # ECE thresholds + sample-size guard.
        "notes": _calibration_notes(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }



# ============================================================
# RISK QUALITY KPIs — distinct failure-pattern count + calibration-
# gap rolling chart. Both are surfaced on the AdminPanel Conviction
# tab. They turn the cleaned-up predictions collection into actual
# operator-facing risk metrics: how many genuinely-distinct
# high-conf misses are we seeing per week (post-dedup), and is the
# avg-confidence/empirical-accuracy gap closing or widening?
# ============================================================


@router.get("/conviction/quality-kpis")
async def conviction_quality_kpis(request: Request, weeks: int = 8):
    """Two operator-facing risk-quality metrics over the last N weeks.

    Returns
    -------
    {
      "weeks": int,
      "unique_failure_patterns": [
         {"week_start": "2026-04-08", "count": int, "patterns": [...]},
         ...  # oldest -> newest
      ],
      "calibration_gap": [
         {"week_start": "2026-04-08", "n": int, "avg_confidence": float,
          "accuracy": float, "gap": float},
         ...
      ],
      "summary": {
         "trend": "improving" | "stable" | "degrading" | "insufficient_data",
         "current_gap": float | None,   # latest non-empty week
         "current_unique_failures": int | None,
      },
    }

    A pattern is `(symbol, failure_code)`. We count DISTINCT patterns
    per week — the post-cleanup version of the recurring "Toxic
    Spikes" bug where 15× duplicates of NVDA-100% inflated the alert
    count. The fix put dedup on write; this widget shows the result.

    A degrading trend (latest |gap| > earliest |gap| by ≥0.05) is
    the early-warning signal that the ML stack needs retraining.
    """
    await _require_owner(request)
    from datetime import timedelta
    weeks = max(1, min(int(weeks), 26))
    now = datetime.now(timezone.utc)
    # Align to ISO week starts (Mondays UTC). We bucket by the
    # "Monday-of" date string so all rows in the same calendar week
    # collapse into one entry — UI sparkline gets a uniform x-axis.
    def monday_of(dt):
        return (dt - timedelta(days=dt.weekday())).date()
    earliest_monday = monday_of(now - timedelta(weeks=weeks - 1))
    fetch_since = datetime.combine(
        earliest_monday, datetime.min.time(), tzinfo=timezone.utc,
    )

    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$in": [True, False]},
            "timestamp": {"$gte": fetch_since.isoformat()},
        },
        {
            "_id": 0,
            "symbol": 1,
            "confidence": 1,
            "verified_24h.correct": 1,
            "verified_24h.failure_code": 1,
            "timestamp": 1,
        },
    ).limit(20000)

    # Prepare per-week buckets keyed by Monday date.
    week_keys = [
        (earliest_monday + timedelta(weeks=i)).isoformat()
        for i in range(weeks)
    ]
    failure_buckets: dict[str, set] = {k: set() for k in week_keys}
    cal_buckets: dict[str, dict] = {
        k: {"n": 0, "sum_conf": 0.0, "n_correct": 0} for k in week_keys
    }

    async for row in cursor:
        ts = row.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        wk = monday_of(dt).isoformat()
        if wk not in failure_buckets:
            continue
        # Confidence is stored mixed 0-1 OR 0-100. Normalise to 0-1 so
        # the gap math is in a single scale — same predicate used by
        # `prediction_tracker.normalize_confidence`.
        conf = float(row.get("confidence") or 0.0)
        if conf > 1.0:
            conf = conf / 100.0
        verified = (row.get("verified_24h") or {})
        correct = verified.get("correct")
        # Calibration gap aggregates EVERY verified row regardless of
        # win/loss — that's the whole point of calibration (compare
        # predicted prob to empirical hit rate).
        cb = cal_buckets[wk]
        cb["n"] += 1
        cb["sum_conf"] += conf
        if correct is True:
            cb["n_correct"] += 1
        # Distinct-failure-pattern set only counts misses (failure
        # patterns by definition can't be wins).
        if correct is False:
            sym = (row.get("symbol") or "?").upper()
            fcode = verified.get("failure_code") or "UNKNOWN"
            failure_buckets[wk].add((sym, fcode))

    unique_failure_patterns = []
    for wk in week_keys:
        patterns = sorted(failure_buckets[wk])
        unique_failure_patterns.append({
            "week_start": wk,
            "count": len(patterns),
            # Truncate sample list — operator only needs a glance at
            # what's recurring, not the full set.
            "patterns": [{"symbol": s, "failure_code": f} for s, f in patterns[:8]],
        })

    calibration_gap = []
    for wk in week_keys:
        b = cal_buckets[wk]
        if b["n"] == 0:
            calibration_gap.append({
                "week_start": wk, "n": 0,
                "avg_confidence": None, "accuracy": None, "gap": None,
            })
            continue
        avg_conf = b["sum_conf"] / b["n"]
        accuracy = b["n_correct"] / b["n"]
        calibration_gap.append({
            "week_start": wk,
            "n": b["n"],
            "avg_confidence": round(avg_conf, 4),
            "accuracy": round(accuracy, 4),
            "gap": round(avg_conf - accuracy, 4),
        })

    # Trend over the populated weeks. We compare the AVG of the first
    # 1/3 of populated weeks to the AVG of the last 1/3 — robust to
    # single-week outliers. < 0.05 absolute change = stable.
    populated = [c for c in calibration_gap if c["gap"] is not None]
    summary_trend = "insufficient_data"
    if len(populated) >= 3:
        third = max(1, len(populated) // 3)
        head_avg = sum(abs(c["gap"]) for c in populated[:third]) / third
        tail_avg = sum(abs(c["gap"]) for c in populated[-third:]) / third
        delta = tail_avg - head_avg
        if delta <= -0.05:
            summary_trend = "improving"
        elif delta >= 0.05:
            summary_trend = "degrading"
        else:
            summary_trend = "stable"

    return {
        "weeks": weeks,
        "unique_failure_patterns": unique_failure_patterns,
        "calibration_gap": calibration_gap,
        "summary": {
            "trend": summary_trend,
            "current_gap": populated[-1]["gap"] if populated else None,
            "current_unique_failures": (
                unique_failure_patterns[-1]["count"]
                if unique_failure_patterns else None
            ),
        },
        "generated_at": now.isoformat(),
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
# STRATEGY LEADERBOARD — which of the agents is actually winning?
# ============================================================

@router.get("/strategies/leaderboard")
async def strategies_leaderboard(request: Request, days: int = 30):
    """Roll up ``learning_engine_trades`` by ``strategy_id`` so we
    can see at a glance which autonomous agent (near_52w_high,
    mean_reversion, earnings_watchdog, options_paper, rsi_overbought,
    …) is actually generating PnL.

    Returns per-strategy:
    - ``trades``: total count in window
    - ``wins`` / ``losses`` / ``pending``: status breakdown
    - ``win_rate``: wins / (wins + losses) — null when no resolved
    - ``avg_r``: mean R-multiple on resolved trades only
    - ``total_pnl``: sum on resolved trades
    - ``last_trade_at``: most recent entry

    Rows with ``strategy_id=None`` are grouped under ``"(untagged)"``
    so the admin sees the tail the dashboard code forgot to label.
    Results sorted by total_pnl desc (money talks); pending-only
    strategies drop to the bottom since their PnL sums to zero.

    Owner-only — reveals agent performance.
    """
    await _require_owner(request)
    if db is None:
        return {"items": [], "window_days": days, "resolved_total": 0, "pending_total": 0}

    days = max(1, min(int(days), 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline: list[dict] = [
        {"$match": {"logged_at": {"$gte": since.isoformat()}}},
        {"$addFields": {
            # Two generations of trade-logging code coexist in this
            # collection: older rows use `strategy_id`, the newer
            # agents (mean_reversion, earnings_watchdog, …) use
            # `strategy`. Coalesce so a single tile surfaces both.
            "_strategy": {
                "$ifNull": [
                    "$strategy",
                    {"$ifNull": ["$strategy_id", "(untagged)"]},
                ],
            },
        }},
        {"$group": {
            "_id": "$_strategy",
            "trades": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$status", "win"]}, 1, 0]}},
            "losses": {"$sum": {"$cond": [{"$eq": ["$status", "loss"]}, 1, 0]}},
            "pending": {"$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]}},
            # Only resolved rows contribute to R and PnL averages —
            # pending trades have r_multiple=0 / pnl=0 by default
            # and would dilute the metric if included.
            "resolved_r_sum": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", ["win", "loss"]]},
                        {"$ifNull": ["$r_multiple", 0]},
                        0,
                    ],
                },
            },
            "resolved_count": {
                "$sum": {
                    "$cond": [{"$in": ["$status", ["win", "loss"]]}, 1, 0],
                },
            },
            "total_pnl": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", ["win", "loss"]]},
                        {"$ifNull": ["$pnl", 0]},
                        0,
                    ],
                },
            },
            "last_trade_at": {"$max": "$logged_at"},
        }},
        {"$sort": {"total_pnl": -1, "trades": -1}},
    ]

    items: list[dict] = []
    resolved_total = 0
    pending_total = 0
    try:
        async for row in db["learning_engine_trades"].aggregate(pipeline):
            resolved = int(row.get("resolved_count", 0))
            wins = int(row.get("wins", 0))
            losses = int(row.get("losses", 0))
            pending = int(row.get("pending", 0))
            r_sum = float(row.get("resolved_r_sum", 0.0) or 0.0)
            avg_r = (r_sum / resolved) if resolved > 0 else None
            win_rate = (wins / (wins + losses)) if (wins + losses) > 0 else None

            items.append({
                "strategy": row.get("_id") or "(untagged)",
                "trades": int(row.get("trades", 0)),
                "wins": wins,
                "losses": losses,
                "pending": pending,
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "avg_r": round(avg_r, 3) if avg_r is not None else None,
                "total_pnl": round(float(row.get("total_pnl", 0.0) or 0.0), 2),
                "last_trade_at": row.get("last_trade_at"),
            })
            resolved_total += resolved
            pending_total += pending
    except Exception as e:
        logger.warning(f"[strategies-leaderboard] aggregate failed: {e}")

    return {
        "items": items,
        "window_days": days,
        "resolved_total": resolved_total,
        "pending_total": pending_total,
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

    # Systemic-failure escalation: after 3+ attempts a recipient
    # is still failing, that's not a transient hiccup — a human
    # needs to intervene (SMTP outage, typo, DNS, …). Fire a
    # high-severity event once per threshold crossing so the feed
    # lights up red.
    if still_failed and new_attempts >= 3:
        try:
            from services.agent_activity_service import log_alert_systemic_failure
            await log_alert_systemic_failure(
                alert_id=alert_id,
                alert_type=alert.get("alert_type", "toxic_spike"),
                delivery_attempts=new_attempts,
                still_failed=still_failed,
            )
        except Exception:
            pass

    return {
        "status": "replayed",
        "alert_id": alert_id,
        "replayed": replayed_ok,
        "still_failed": still_failed,
        "delivery_attempts": new_attempts,
        "systemic_failure": bool(still_failed and new_attempts >= 3),
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
        """`metric` is a canonical dotted key (e.g. ``volume.liquidity``,
        ``macd.crossover``, ``pattern.bull_flag``) so we can dedup
        across the failure-code + fallback layers when both
        phrasings describe the same underlying signal. The namespace
        prefix (``volume.``, ``pattern.``, ...) groups related
        signals without collapsing distinct ones (e.g.
        ``volume.liquidity`` and ``volume.spike`` coexist — they
        mean opposite things)."""
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
            add("bull flag broke down", 0.95, "pattern.bull_flag")
        if isinstance(macd, (int, float)) and macd < 0:
            add("bearish momentum reversal", 0.9, "macd.crossover")

    elif failure_code == "LIQUIDITY_GAP":
        if isinstance(vol, (int, float)) and vol < 0.8:
            add(f"low liquidity ({vol:.2f}x volume)", 0.95, "volume.liquidity")
        add("slippage / spread expansion", 0.85, "liquidity.slippage")

    elif failure_code == "REGIME_SHIFT":
        # Overextension / trend exhaustion lives under REGIME_SHIFT
        # in our FAILURE_MODES vocabulary.
        if isinstance(rsi, (int, float)) and rsi > 70:
            add(f"overbought RSI ({int(rsi)})", 0.95, "rsi.overbought")
        add("trend exhaustion", 0.85, "trend.exhaustion")

    elif failure_code == "MACRO_SHOCK":
        if isinstance(sector, (int, float)) and sector < 0:
            add(f"negative sector momentum ({sector * 100:+.1f}%)", 0.95, "sector.momentum")
        add("macro regime misalignment", 0.85, "macro.regime")

    # ── 2. Fallback heuristics (MEDIUM SIGNAL) ──
    # Only fill slots that the failure-code layer didn't already
    # claim — we dedup below by `metric`, so anything tagged with an
    # already-seen metric is silently dropped.
    if len(drivers) < 3:
        if isinstance(rsi, (int, float)):
            if rsi > 70:
                add(f"overbought RSI ({int(rsi)})", 0.6, "rsi.overbought")
            elif rsi < 30:
                add(f"oversold RSI ({int(rsi)})", 0.6, "rsi.oversold")

        if isinstance(vol, (int, float)):
            if vol < 0.8:
                add(f"low volume ({vol:.2f}x)", 0.55, "volume.liquidity")
            elif vol > 1.5:
                add(f"volume spike ({vol:.2f}x)", 0.55, "volume.spike")

        if isinstance(macd, (int, float)) and isinstance(macd_sig, (int, float)):
            if macd < macd_sig and macd < 0:
                add("MACD bearish crossover", 0.6, "macd.crossover")

        if isinstance(sector, (int, float)) and sector < -0.02:
            add(f"negative sector ({sector * 100:+.1f}%)", 0.55, "sector.momentum")

        if isinstance(sentiment, (int, float)) and sentiment < -0.3:
            add(f"negative sentiment ({sentiment:+.2f})", 0.5, "sentiment.negative")

        if snap.get("pattern_rsi_divergence"):
            add("RSI divergence", 0.55, "pattern.rsi_divergence")
        if snap.get("pattern_head_and_shoulders"):
            add("head & shoulders pattern", 0.55, "pattern.head_and_shoulders")
        if snap.get("pattern_bearish_engulfing"):
            add("bearish engulfing", 0.55, "pattern.bearish_engulfing")

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
    # Try to load the latest trained model once so we can compute
    # SHAP-style contributions for every ticker in one pass. Falls
    # back to the per-snapshot heuristic if the model isn't loadable
    # (fresh deployment, load error, etc.) — the caller still sees
    # the drivers, just without SHAP magnitudes.
    shap_model = None
    try:
        from services.ml_retrain_service import get_latest_model_info
        info = get_latest_model_info()
        if info and info.get("path"):
            from risedual_core.ml.signal_model import SignalModel
            shap_model = SignalModel.load(info["path"])
    except Exception as e:
        logger.debug(f"[why-drilldown] SHAP model load skipped: {e}")

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
        # SHAP enrichment — computed AFTER heuristic so a SHAP
        # failure never loses us the baseline drivers.
        shap_top: list[dict] = []
        if shap_model is not None and snap:
            try:
                # Need a richer snapshot with all FEATURE_COLUMNS —
                # re-fetch with no projection so the DataFrame
                # reconstructor has everything it needs.
                full_snap = await db["features_snapshots"].find_one(
                    {"ticker": ticker}, {"_id": 0},
                    sort=[("timestamp", -1)],
                )
                if full_snap:
                    import pandas as _pd
                    df = _pd.DataFrame([full_snap])
                    tops = shap_model.shap_top_features(df, top_n=3)
                    if tops and tops[0]:
                        shap_top = [
                            {"feature": name, "contribution": round(val, 4)}
                            for (name, val) in tops[0]
                        ]
            except Exception as e:
                logger.debug(f"[why-drilldown] SHAP for {ticker}: {e}")
        enriched.append({
            "symbol": ticker,
            "confidence": spike.get("confidence"),
            "failure_code": failure_code,
            "date": spike.get("date"),
            "regime": (snap or {}).get("regime_label"),
            "snapshot_at": (snap or {}).get("timestamp"),
            "drivers": drivers,
            "shap_top": shap_top,
        })

    return {
        "alert_id": alert_id,
        "toxic_count": meta.get("toxic_count", 0),
        "alert_type": alert.get("alert_type", "toxic_spike"),
        "items": enriched,
    }


# ============================================================
# ML ADAPTATIONS — prescriptive training-weight adjustments
# ============================================================

@router.get("/adaptations")
async def list_adaptations(request: Request):
    """Active model adaptations — bounded row-weight adjustments
    applied at retrain time based on recent toxic-alert patterns.
    Owner-gated (touches ML behaviour).

    Also carries the most recent ``adaptation_impact`` block from
    the ml_training_log — callers surface ΔR / Δwin-rate so admins
    can tell at a glance whether the adaptation set is actually
    moving the expected outcome or just shuffling weights."""
    await _require_owner(request)
    if db is None:
        return {"items": [], "enabled": False, "total": 0,
                "last_impact": None, "recent_auto_reverts": []}
    from services.model_adaptation import (
        adaptation_enabled,
        list_active_adaptations,
    )
    items = await list_active_adaptations(db)

    # Most recent retrain row carrying an adaptation_impact block.
    # Older rows (pre-upgrade) don't have it — we silently skip.
    last_impact = None
    try:
        run = await db["ml_training_log"].find_one(
            {"adaptation_impact": {"$exists": True}},
            {"_id": 0, "adaptation_impact": 1, "adaptations_applied": 1,
             "started_at": 1, "finished_at": 1, "model_version": 1},
            sort=[("started_at", -1)],
        )
        if run:
            ts = run.get("finished_at") or run.get("started_at")
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            per_ad = []
            for row in run.get("adaptations_applied") or []:
                if "delta_mean_r" in row or "delta_win_rate" in row:
                    per_ad.append({
                        "adaptation_id": row.get("adaptation_id"),
                        "metric": row.get("metric"),
                        "direction": row.get("direction"),
                        "rows_matched": row.get("rows_matched"),
                        "delta_mean_r": row.get("delta_mean_r"),
                        "delta_win_rate": row.get("delta_win_rate"),
                    })
            last_impact = {
                "at": ts,
                "model_version": run.get("model_version"),
                "global": run["adaptation_impact"],
                "per_adaptation": per_ad,
            }
    except Exception:
        last_impact = None

    # Recent auto-revert/auto-soften — surfaces the safety-rail
    # activity in the same panel so admins see gradient
    # de-escalation (soften) AND final flips (revert) side-by-side.
    # Last 10 is plenty; older entries live in the
    # `adaptation_audit` collection for long-range queries.
    recent_auto_reverts: list[dict] = []
    try:
        cursor = (
            db["adaptation_audit"]
            .find(
                {"action": {"$in": [
                    "auto_revert", "auto_soften",
                    "shadow_revert", "shadow_soften",
                ]}},
                {"_id": 0},
            )
            .sort("at", -1)
            .limit(10)
        )
        async for row in cursor:
            recent_auto_reverts.append(row)
    except Exception:
        recent_auto_reverts = []

    return {
        "items": items,
        "enabled": adaptation_enabled(),
        "total": len(items),
        "last_impact": last_impact,
        "recent_auto_reverts": recent_auto_reverts,
    }


@router.post("/adaptations/{adaptation_id}/revert")
async def revert_adaptation_endpoint(adaptation_id: str, request: Request):
    """Flip one adaptation to inactive — the next retrain will
    ignore it. Audit trail preserved (not deleted)."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.model_adaptation import revert_adaptation
    ok = await revert_adaptation(db, adaptation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Adaptation not found or already inactive")
    return {"status": "reverted", "adaptation_id": adaptation_id}


@router.post("/adaptations/disable_all")
async def disable_all_adaptations_endpoint(request: Request):
    """Kill switch — deactivates every active adaptation at once.
    Use when the retrain-level adaptation loop is misbehaving and
    you want model weights back to pristine severity+regime-only
    on the next retrain."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.model_adaptation import disable_all_adaptations
    n = await disable_all_adaptations(db)
    return {"status": "disabled_all", "deactivated": n}


@router.get("/adaptations/calibration")
async def adaptations_calibration(request: Request, window_days: int = 30):
    """Distribution analysis of shadow-mode safety-rail observations.

    Reads the last ``window_days`` of ``adaptation_audit`` rows
    tagged ``shadow_soften`` / ``shadow_revert`` and computes
    per-metric percentile distributions of ``decision_score``,
    ``decision_ratio`` and ``delta_r_trend``. Pairs that with the
    CURRENT live thresholds (pulled from
    ``get_auto_revert_config``) so operators can see at a glance:

      * what the scanner has been seeing in shadow mode,
      * which threshold each observation cleared,
      * a recommended tuned threshold (the p25 of observed scores,
        so flipping live would act on the strongest 75% of
        observations — the rest land in noise territory).

    The recommendation is advisory, not auto-applied — set
    ``ML_AUTO_REVERT_EFFECT_SIZE`` (and siblings) in the env and
    restart supervisor to adopt it.

    Owner-gated. Runs read-only; safe to call repeatedly.
    """
    await _require_owner(request)
    if db is None:
        return {
            "window_days": window_days,
            "observations": 0,
            "current_config": {},
            "distribution": {},
            "recommendation": None,
            "note": "Database unavailable",
        }
    from services.model_adaptation import get_auto_revert_config

    cfg = get_auto_revert_config()
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, window_days))).isoformat()

    cursor = db["adaptation_audit"].find(
        {
            "action": {"$in": ["shadow_soften", "shadow_revert",
                                "auto_soften", "auto_revert"]},
            "at": {"$gte": since},
        },
        {"_id": 0},
    ).sort("at", -1).limit(1000)

    scores: list[float] = []
    ratios: list[float] = []
    trends: list[float] = []
    shadow_count = 0
    live_count = 0
    by_action: dict[str, int] = {}
    by_metric: dict[str, dict[str, Any]] = {}
    async for row in cursor:
        ds = row.get("decision_score")
        dr_ratio = row.get("decision_ratio")
        dtrend = row.get("delta_r_trend")
        try:
            if ds is not None:
                scores.append(float(ds))
            if dr_ratio is not None:
                ratios.append(float(dr_ratio))
            if dtrend is not None:
                trends.append(float(dtrend))
        except (TypeError, ValueError):
            continue
        if row.get("shadow"):
            shadow_count += 1
        else:
            live_count += 1
        action = row.get("action") or "unknown"
        by_action[action] = by_action.get(action, 0) + 1
        metric = row.get("metric") or "unknown"
        bucket = by_metric.setdefault(metric, {"n": 0, "scores": []})
        bucket["n"] += 1
        if ds is not None:
            try:
                bucket["scores"].append(float(ds))
            except (TypeError, ValueError):
                pass

    def _percentiles(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"n": 0, "min": None, "p25": None, "p50": None,
                    "p75": None, "p90": None, "max": None, "mean": None}
        ordered = sorted(values)
        n = len(ordered)

        def pick(p: float) -> float:
            idx = max(0, min(n - 1, int(round((n - 1) * p))))
            return round(ordered[idx], 6)

        return {
            "n": n,
            "min": round(ordered[0], 6),
            "p25": pick(0.25),
            "p50": pick(0.50),
            "p75": pick(0.75),
            "p90": pick(0.90),
            "max": round(ordered[-1], 6),
            "mean": round(sum(ordered) / n, 6),
        }

    score_dist = _percentiles(scores)
    ratio_dist = _percentiles(ratios)
    trend_dist = _percentiles(trends)

    # ── Per-metric roll-up ──
    # Strip the raw score list from the response so we don't ship
    # 1000 floats back, but compute a p50 per metric — the single
    # number that tells admins "this rule is consistently over
    # the threshold" vs "this rule is borderline".
    per_metric: list[dict[str, Any]] = []
    for metric, bucket in by_metric.items():
        raw = bucket["scores"]
        if raw:
            raw_sorted = sorted(raw)
            median = raw_sorted[len(raw_sorted) // 2]
        else:
            median = None
        per_metric.append({
            "metric": metric,
            "observations": bucket["n"],
            "median_score": round(median, 6) if median is not None else None,
        })
    per_metric.sort(key=lambda r: r["observations"], reverse=True)

    # ── Recommendation ──
    # Use the 25th percentile of observed decision_scores as the
    # suggested new threshold. Rationale: 75% of the shadow rail's
    # would-be actions would still fire (the high-signal ones),
    # the bottom quartile (borderline/noisy) would get filtered.
    # We cap below at 0.0001 so a degenerate shadow dataset can't
    # recommend a threshold of zero.
    MIN_OBS_FOR_RECOMMENDATION = 20
    recommendation: dict[str, Any] | None = None
    if score_dist["n"] and score_dist["n"] >= MIN_OBS_FOR_RECOMMENDATION:
        suggested = max(float(score_dist["p25"] or 0.0), 0.0001)
        delta = suggested - cfg["effect_size"]
        pct_change = (delta / cfg["effect_size"] * 100) if cfg["effect_size"] > 0 else None
        direction = "tighten" if suggested > cfg["effect_size"] else "loosen"
        recommendation = {
            "suggested_effect_size": round(suggested, 6),
            "current_effect_size": cfg["effect_size"],
            "direction": direction,
            "delta": round(delta, 6),
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
            "rationale": (
                "Tuned to the 25th percentile of observed shadow "
                "decision_scores — would act on the strongest 75% "
                "of signals and filter the noisy tail."
            ),
            "apply_via": "Set ML_AUTO_REVERT_EFFECT_SIZE in the env, then restart supervisor.",
        }
    elif score_dist["n"]:
        recommendation = {
            "suggested_effect_size": None,
            "current_effect_size": cfg["effect_size"],
            "note": (
                f"Need ≥{MIN_OBS_FOR_RECOMMENDATION} observations "
                f"before recommending; currently have {score_dist['n']}."
            ),
        }

    return {
        "window_days": window_days,
        "observations": score_dist["n"],
        "shadow_observations": shadow_count,
        "live_observations": live_count,
        "by_action": by_action,
        "current_config": cfg,
        "distribution": {
            "decision_score": score_dist,
            "decision_ratio": ratio_dist,
            "delta_r_trend": trend_dist,
        },
        "per_metric": per_metric,
        "recommendation": recommendation,
    }



# ── Metric → plain-English narrator. Keeps the "why" payload
#    self-contained so the UI doesn't need a second hop to make
#    sense of it. Lift is rendered as N.NNx so non-ML admins can
#    read it at a glance ("1.52× more often than baseline").
def _explain_adaptation_metric(metric: str, lift: float | None,
                               direction: str | None) -> str:
    suffix = ""
    if direction and direction not in ("ANY", None):
        side = "bullish" if direction == "LONG" else "bearish"
        suffix = f" on the {side} side"
    lift_s = f"{lift:.2f}×" if lift is not None else "elevated"
    if metric.startswith("volume.liquidity"):
        return f"Low-liquidity setups failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("volume.spike"):
        return f"Panic-volume setups failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("rsi.overbought"):
        return f"Overbought (RSI > 70) rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("rsi.oversold"):
        return f"Oversold (RSI < 30) rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("macd.crossover"):
        return f"Bearish-MACD rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("sector.momentum"):
        return f"Negative-sector-momentum rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("sentiment.negative"):
        return f"Negative-sentiment rows failed {lift_s} more often than baseline{suffix}."
    if metric.startswith("pattern."):
        pretty = metric.replace("pattern.", "").replace("_", " ")
        return f"The {pretty} pattern failed {lift_s} more often than baseline{suffix}."
    return f"This metric showed an elevated failure rate ({lift_s} vs baseline){suffix}."


@router.get("/adaptations/why/{adaptation_id}")
async def explain_adaptation(adaptation_id: str, request: Request):
    """Explain a specific adaptation — what failed, how much more
    often than baseline, and what the retrain will do about it.

    This is the second half of the "closed-loop explainability"
    story: ``/alerts/why/{alert_id}`` explains a failure, this
    endpoint explains the adaptation that was derived from one or
    more of those failures. Admin-gated."""
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc = await db["model_adaptations"].find_one(
        {"adaptation_id": adaptation_id}, {"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Adaptation not found")

    metric = doc.get("metric", "")
    direction = doc.get("direction") or "ANY"
    factor = float(doc.get("adjustment_factor") or 1.0)
    lift = doc.get("contrast")
    try:
        lift_f: float | None = float(lift) if lift is not None else None
    except (TypeError, ValueError):
        lift_f = None
    bucket_rate = doc.get("bucket_rate")
    global_rate = doc.get("global_rate")
    severity = doc.get("severity")
    evidence = int(doc.get("evidence_count") or 0)
    active = bool(doc.get("active", False))

    pct_down = max(0, round((1.0 - factor) * 100))
    projected_effect = (
        f"Reduces influence of matching setups by ~{pct_down}% in the next retrain"
        if pct_down > 0 else
        "No weight reduction (factor at or above 1.0)"
    )

    explanation = _explain_adaptation_metric(metric, lift_f, direction)

    expires_at = doc.get("expires_at")
    if hasattr(expires_at, "isoformat"):
        expires_at = expires_at.isoformat()

    return {
        "adaptation_id": adaptation_id,
        "metric": metric,
        "direction": direction,
        "factor": round(factor, 3),
        "weight_reduction_pct": pct_down,
        "lift": round(lift_f, 3) if lift_f is not None else None,
        "bucket_rate": round(float(bucket_rate), 4) if bucket_rate is not None else None,
        "global_rate": round(float(global_rate), 4) if global_rate is not None else None,
        "severity": round(float(severity), 4) if severity is not None else None,
        "evidence_count": evidence,
        "active": active,
        "expires_at": expires_at,
        "description": doc.get("description"),
        "explanation": explanation,
        "projected_effect": projected_effect,
    }



# ============================================================
# ML HEALTH DIGEST — admin-only daily brief
# ============================================================

@router.post("/ml-health-digest/trigger")
async def trigger_ml_health_digest(request: Request):
    """Owner/admin: manually fire the daily ML-health digest now
    (same code path as the 08:00 UTC scheduler). Useful for
    testing the email template without waiting for cron.

    Note: the scheduled path is idempotent per UTC date — if the
    digest has already run today it will be a no-op. This manual
    trigger wraps the same function, so a second call on the same
    day returns ``{"sent": False, "reason": "already_sent_today"}``
    rather than double-sending."""
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.ml_health_digest_service import run_ml_health_digest
    return await run_ml_health_digest(db)


@router.get("/ml-health-digest/preview")
async def preview_ml_health_digest(request: Request):
    """Render the ML-health digest WITHOUT sending. Returns raw
    HTML + the collected data payload so the admin UI can mount an
    inline preview tile."""
    await _require_admin(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.ml_health_digest_service import (
        collect_ml_health_data, _format_body_html, _format_subject,
    )
    from services.email_service import _base_html
    data = await collect_ml_health_data(db, window_hours=24)
    subject = _format_subject(data)
    counts = data.get("counts") or {}
    preheader = (
        f"soften={counts.get('auto_soften',0)+counts.get('shadow_soften',0)} · "
        f"revert={counts.get('auto_revert',0)+counts.get('shadow_revert',0)} · "
        f"active={data.get('active_count',0)}"
    )
    html = _base_html(_format_body_html(data), preheader=preheader)
    return {"subject": subject, "html": html, "data": data}



# ═══════════════════════════════════════════════════════════════════
# DATA INTEGRITY DASHBOARD
# ═══════════════════════════════════════════════════════════════════
#
# Operator-visible surface for the 2026-05-01 direction-token
# cleanup and its ongoing tripwires. Every audit / repair / supersede
# event we care about is aggregated into a single response so the
# admin UI can render "data integrity timeline" at a glance.


@router.get("/data-integrity/summary")
async def data_integrity_summary(request: Request):
    """Return the current data-integrity health snapshot.

    Fields:

      * ``unknown_direction_tokens`` — metric counts (24h / 7d) and
        per-context breakdown. Target: 0 in every window.
      * ``backfills`` — prediction grade backfills + LE trade repairs
        performed by the cleanup scripts; counts all-time + 7d.
      * ``toxic_lessons`` — created in the last 7d vs. superseded
        (i.e. flipped away from toxic_lesson by the corrections).
      * ``latest_audit`` — the most recent
        ``data_integrity_audits`` row (nightly invariant run).
      * ``brute_force_events`` — lockout triggers in the last 24h
        / 7d, with the top offending (client_ip, email) pairs.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    # 1) unknown-direction metric
    udt_24h = await db.data_integrity_metrics.count_documents({
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since_24h},
    })
    udt_7d = await db.data_integrity_metrics.count_documents({
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since_7d},
    })
    udt_contexts = [
        {"context": r["_id"], "count": r["n"]}
        async for r in db.data_integrity_metrics.aggregate([
            {"$match": {"metric": "unknown_direction_token",
                        "fired_at": {"$gte": since_7d}}},
            {"$group": {"_id": "$context", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ])
    ]

    # 2) backfills
    grade_backfills_all = await db.prediction_grade_backfill_log.count_documents({})
    grade_backfills_7d = await db.prediction_grade_backfill_log.count_documents({
        "applied_at": {"$gte": since_7d},
    })
    le_repairs_all = await db.learning_engine_trade_repair_log.count_documents({})
    le_repairs_7d = await db.learning_engine_trade_repair_log.count_documents({
        "applied_at": {"$gte": since_7d},
    })

    # 3) toxic-lesson count in Chroma — best-effort, never fail the dashboard.
    toxic_created_7d = None
    toxic_superseded = None
    try:
        import services.market_memory_service as mms
        coll = getattr(mms, "_collection", None)
        if coll is not None:
            import asyncio as _aio
            active = await _aio.to_thread(
                coll.get, where={"outcome": "toxic_lesson"}, include=["metadatas"]
            )
            metas = active.get("metadatas") or []
            toxic_created_7d = sum(
                1 for m in metas
                if m and str((m.get("created_at") or m.get("logged_at") or "")) >= since_7d
            )
            superseded = await _aio.to_thread(
                coll.get, where={"lesson_status": "superseded"}, include=["metadatas"]
            )
            toxic_superseded = len(superseded.get("ids") or [])
    except Exception as e:
        logger.warning("data_integrity: chroma probe failed: %s", e)

    # 4) latest audit summary
    latest_audit = await db.data_integrity_audits.find_one(
        {}, sort=[("run_id", -1)],
    )
    if latest_audit:
        latest_audit.pop("_id", None)

    # 5) brute-force events
    bf_24h = await db.brute_force_events.count_documents({
        "fired_at": {"$gte": since_24h},
    })
    bf_7d = await db.brute_force_events.count_documents({
        "fired_at": {"$gte": since_7d},
    })
    bf_top = [
        {"identifier": r["_id"], "count": r["n"]}
        async for r in db.brute_force_events.aggregate([
            {"$match": {"fired_at": {"$gte": since_7d}}},
            {"$group": {
                "_id": {"$concat": ["$client_ip", " → ", "$triggered_by_user_email"]},
                "n": {"$sum": 1},
            }},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ])
    ]

    # 6) integrity mitigation state — the self-defense layer surfaced
    # so the dashboard banner can render "degraded trading active"
    # without needing a separate endpoint.
    mitigation_state = {"active": False, "active_count": 0}
    try:
        from services.integrity_mitigation_service import (
            summarize_integrity_mitigation_state,
        )
        mitigation_state = await summarize_integrity_mitigation_state(db)
    except Exception as e:
        logger.warning("data_integrity: mitigation probe failed: %s", e)

    return {
        "as_of": now.isoformat(),
        "unknown_direction_tokens": {
            "last_24h": udt_24h,
            "last_7d": udt_7d,
            "top_contexts_7d": udt_contexts,
        },
        "backfills": {
            "grade_all_time": grade_backfills_all,
            "grade_last_7d": grade_backfills_7d,
            "le_trade_all_time": le_repairs_all,
            "le_trade_last_7d": le_repairs_7d,
        },
        "toxic_lessons": {
            "created_last_7d": toxic_created_7d,
            "superseded_total": toxic_superseded,
        },
        "brute_force": {
            "lockouts_last_24h": bf_24h,
            "lockouts_last_7d": bf_7d,
            "top_offenders_7d": bf_top,
        },
        "latest_nightly_audit": latest_audit,
        "mitigation": mitigation_state,
    }


@router.post("/data-integrity/run-audit")
async def run_integrity_audit_now(request: Request):
    """Manually trigger the nightly invariant audit.

    Useful for verifying a cleanup just landed. The scheduled job
    runs once a day at 03:15 UTC.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    from services.data_integrity_auditor import run_nightly_integrity_audit
    summary = await run_nightly_integrity_audit(db)
    summary.pop("_id", None)
    return summary


# ═══════════════════════════════════════════════════════════════════
# DATA INTEGRITY ALERT RULES
# ═══════════════════════════════════════════════════════════════════
#
# Operator-defined threshold alerts on top of the dashboard metrics.
# Each rule is independently editable from the admin UI; breaches
# dispatch to email + Slack in parallel subject to per-rule throttle.
# The rule documents are the same shape the scheduler evaluator
# reads; see services/data_integrity_alerts.py for the contract.


class AlertRuleUpsert(BaseModel):
    rule_id: str
    metric: str  # Validated against METRIC_COUNTERS below.
    window_hours: int = 24
    threshold: int = 1
    comparator: str = "gte"  # gte | gt | eq
    channels: list[str] = []  # "email:<addr>" | "slack"
    enabled: bool = True
    throttle_hours: int = 12
    notes: str = ""
    # Self-defense spec (optional). When present and the rule fires,
    # `activate_integrity_mitigation` is called with this dict — e.g.
    #   {"action": "DEGRADE_TRADING",
    #    "params": {"position_multiplier": 0.5,
    #               "disable_strong_signals": true}}
    mitigation: dict | None = None
    mitigation_ttl_minutes: int = 60


@router.get("/data-integrity/alert-rules")
async def list_alert_rules(request: Request):
    """Return every alert rule. Sorted most-recently-updated first."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    cursor = db.data_integrity_alert_rules.find({}).sort("updated_at", -1)
    rules = []
    async for r in cursor:
        r.pop("_id", None)
        rules.append(r)
    return {"rules": rules}


@router.post("/data-integrity/alert-rules")
async def upsert_alert_rule(payload: AlertRuleUpsert, request: Request):
    """Create or update an alert rule, keyed by ``rule_id``."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    from services.data_integrity_alerts import METRIC_COUNTERS
    if payload.metric not in METRIC_COUNTERS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "unsupported_metric",
                "supported": sorted(METRIC_COUNTERS.keys()),
            },
        )
    if payload.comparator not in {"gte", "gt", "eq"}:
        raise HTTPException(status_code=400, detail="comparator must be gte|gt|eq")
    if payload.window_hours <= 0 or payload.threshold < 0 or payload.throttle_hours < 0:
        raise HTTPException(status_code=400, detail="window/threshold/throttle must be non-negative")
    # Channel format sanity — each entry must be "email:<addr>" or
    # exactly "slack". Unknown channels would silently fail at dispatch.
    for c in payload.channels:
        if c == "slack":
            continue
        if c.startswith("email:") and "@" in c.split(":", 1)[1]:
            continue
        raise HTTPException(
            status_code=400,
            detail=f"unsupported channel {c!r}; use 'email:<addr>' or 'slack'",
        )

    # Mitigation sanity — if the operator attached a mitigation spec,
    # the `action` must be in the whitelist. Catching it here instead
    # of silently no-opping at evaluator time prevents rules that
    # "look like" they'll degrade trading but never actually fire.
    if payload.mitigation:
        from services.integrity_mitigation_service import SUPPORTED_ACTIONS
        mit_action = (payload.mitigation.get("action") or "").strip()
        if mit_action not in SUPPORTED_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "unsupported_mitigation_action",
                    "supported": sorted(SUPPORTED_ACTIONS),
                    "received": mit_action,
                },
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        **payload.dict(),
        "updated_at": now_iso,
    }
    existing = await db.data_integrity_alert_rules.find_one({"rule_id": payload.rule_id})
    if not existing:
        doc["created_at"] = now_iso
    await db.data_integrity_alert_rules.update_one(
        {"rule_id": payload.rule_id},
        {"$set": doc},
        upsert=True,
    )
    saved = await db.data_integrity_alert_rules.find_one({"rule_id": payload.rule_id})
    saved.pop("_id", None)
    return saved


@router.delete("/data-integrity/alert-rules/{rule_id}")
async def delete_alert_rule(rule_id: str, request: Request):
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    res = await db.data_integrity_alert_rules.delete_one({"rule_id": rule_id})
    return {"deleted": res.deleted_count > 0, "rule_id": rule_id}


@router.post("/data-integrity/alert-rules/evaluate-now")
async def evaluate_alert_rules_now(request: Request):
    """Manually run the rule evaluator once. Returns the summary."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    from services.data_integrity_alerts import evaluate_rules_once
    return await evaluate_rules_once(db)


@router.get("/data-integrity/alert-events")
async def list_alert_events(request: Request, limit: int = 50):
    """Recent alert breaches with dispatch results."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")
    limit = max(1, min(int(limit or 50), 500))
    cursor = (
        db.data_integrity_alert_events
        .find({})
        .sort("fired_at", -1)
        .limit(limit)
    )
    events = []
    async for e in cursor:
        e.pop("_id", None)
        events.append(e)
    return {"events": events, "limit": limit}


@router.get("/data-integrity/timeseries")
async def data_integrity_timeseries(request: Request, days: int = 14):
    """Daily bucket counts for the data-integrity sparklines.

    Returns three parallel arrays (unknown-direction tokens, grade
    backfills, brute-force lockouts) bucketed by UTC calendar day.
    14 days is the default — plenty of width to catch a slow drift
    toward the bug class without overwhelming the admin card.

    Shape:
        {
          "days": 14,
          "buckets": ["2026-04-17", "2026-04-18", ..., "2026-04-30"],
          "unknown_direction_tokens": [0, 0, ..., 0],
          "grade_backfills":          [0, ..., 24, 0, ...],
          "brute_force_lockouts":     [0, ..., 0],
        }

    A rising curve on ``unknown_direction_tokens`` is the earliest
    tripwire for a new engine emitting a non-enum verdict; the
    nightly invariant audit will eventually catch it, but the
    sparkline surfaces the trend hours earlier.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    days = max(1, min(int(days or 14), 90))
    today = datetime.now(timezone.utc).date()
    buckets = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    bucket_isos = [d.isoformat() for d in buckets]
    since = (datetime.combine(buckets[0], datetime.min.time(), tzinfo=timezone.utc)).isoformat()

    async def _daily_counts(coll, time_field: str, extra: dict | None = None) -> dict[str, int]:
        q: dict[str, Any] = {time_field: {"$gte": since}}
        if extra:
            q.update(extra)
        cursor = coll.find(q, {"_id": 0, time_field: 1})
        out: dict[str, int] = {d: 0 for d in bucket_isos}
        async for r in cursor:
            ts = str(r.get(time_field, ""))
            if len(ts) >= 10:
                day = ts[:10]
                if day in out:
                    out[day] += 1
        return out

    udt = await _daily_counts(
        db.data_integrity_metrics, "fired_at",
        {"metric": "unknown_direction_token"},
    )
    backfills = await _daily_counts(
        db.prediction_grade_backfill_log, "applied_at",
    )
    bf = await _daily_counts(db.brute_force_events, "fired_at")

    return {
        "days": days,
        "buckets": bucket_isos,
        "unknown_direction_tokens": [udt[d] for d in bucket_isos],
        "grade_backfills": [backfills[d] for d in bucket_isos],
        "brute_force_lockouts": [bf[d] for d in bucket_isos],
    }



# ── Tier 3 Universe Bootstrap ─────────────────────────────────────


# Universe expanded 2026-05-02. The original 5-bot setup
# (SPY, QQQ, AAPL, MSFT, NVDA) saturated its `max_trades_per_day`
# cap every day, throttling the ML pipeline's learning rate. The
# 15 new tickers below are picked across sectors so ensemble
# disagreement (the adversarial layer's primary suppressor)
# happens less often — broader coverage = more independent signals
# = more ML training samples per day.
_TIER3_NEW_TICKERS: tuple[str, ...] = (
    # High-vol mega-cap tech (different beta from existing AAPL/MSFT/NVDA)
    "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "NFLX",
    # Momentum / high-vol non-mag-7
    "PLTR", "COIN", "SMCI",
    # Financials (rate-sensitive; different macro driver)
    "JPM", "BAC",
    # Energy (oil-price driven)
    "XOM",
    # Defensives (consumer staples + healthcare)
    "WMT", "UNH",
)


_TIER3_DAILY_CAP: int = 10  # was 5 — bumped to clear the saturation


@router.post("/bots/seed-tier3-universe")
async def seed_tier3_universe(request: Request):
    """One-shot: add the 15 new Tier-3 Accumulator signal bots and
    bump per-bot daily caps to 10.

    Idempotent — re-running is safe:
      * Bots that already exist (matched by name) are skipped, never
        duplicated.
      * Daily-cap bump runs on every Tier-3 Accumulator bot (existing
        and newly created) so the cap stays consistent.

    Returns a summary of what changed so the operator can audit.
    """
    user = await _require_owner(request)
    owner_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    base_config = {
        "min_confidence": 70,
        "strategies": [],
        "side": "both",
        "qty": 1,
        "use_smart_order": True,
        "auto_sl_pct": 3,
        "auto_tp_pct": 6,
        "max_trades_per_day": _TIER3_DAILY_CAP,
        "trades_today": 0,
    }
    base_stats = {"trades": 0, "pnl": 0, "signals_received": 0, "signals_executed": 0}

    created: list[str] = []
    skipped: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    from uuid import uuid4
    for ticker in _TIER3_NEW_TICKERS:
        name = f"Tier3 Accumulator · {ticker}"
        existing = await db.trading_bots.find_one({"name": name}, {"_id": 1})
        if existing:
            skipped.append(ticker)
            continue
        await db.trading_bots.insert_one({
            "_id": str(uuid4()).replace("-", ""),
            "user_id": owner_id,
            "type": "signal",
            "name": name,
            "enabled": True,
            "mode": "paper",
            "config": {**base_config, "symbols": [ticker]},
            "stats": dict(base_stats),
            "created_at": now,
            "updated_at": now,
            "last_run": None,
        })
        created.append(ticker)

    # Bump the daily cap on every Tier-3 Accumulator bot. Resets
    # ``trades_today`` to 0 so the cap takes effect immediately
    # without waiting for the per-bot midnight rollover.
    cap_update = await db.trading_bots.update_many(
        {"name": {"$regex": "^Tier3 Accumulator · "}},
        {
            "$set": {
                "config.max_trades_per_day": _TIER3_DAILY_CAP,
                "config.trades_today": 0,
                "updated_at": now,
            }
        },
    )

    total_signal_bots = await db.trading_bots.count_documents(
        {"type": "signal", "enabled": True}
    )

    return {
        "created": created,
        "skipped_already_existed": skipped,
        "daily_cap_now": _TIER3_DAILY_CAP,
        "bots_with_cap_bumped": cap_update.modified_count,
        "total_enabled_signal_bots": total_signal_bots,
        "as_of": now,
    }


# ── Commander Shadow — observability + promotion gate ────────────


@router.get("/commander-shadow/promotion-status")
async def commander_shadow_promotion_status(request: Request):
    """Return the equity Commander Shadow promotion-gate state.

    Powers the admin dashboard card the user requested:

        Commander Equity Shadow
        Rows: X / 50
        Win rate: YY%
        Brake eligible: yes/no

    Phase 1 (logging only) is the default state after redeploy.
    Phase 2 (pre-Tier-3 size brake) unlocks automatically when the
    thresholds in ``services.equity_shadow_promotion`` clear.
    """
    await _require_owner(request)
    from services.equity_shadow_promotion import (
        compute_equity_shadow_promotion_status,
    )
    return await compute_equity_shadow_promotion_status(db)


@router.get("/commander-shadow/stream-stats")
async def commander_shadow_stream_stats(request: Request):
    """Return per-market JSONL file health: size, line_count, mtime.

    Lets the admin dashboard show "we have N crypto / M equity /
    K options decisions logged this session" without hitting Mongo.
    The file stream is a developer-ergonomics mirror — the Mongo
    ``research_shadow_decisions`` collection is authoritative.
    """
    await _require_owner(request)
    from services.commander_decision_stream import list_stream_stats
    return {"streams": list_stream_stats()}


@router.get("/commander-shadow/recent/{asset_type}")
async def commander_shadow_recent(asset_type: str, request: Request, limit: int = 50):
    """Tail the last ``limit`` rows of a per-market Commander
    decision stream. Useful for quick UI spot-checking without
    shell access.

    ``asset_type`` must be one of ``stock``, ``crypto``, ``options``.
    """
    await _require_owner(request)
    from services.commander_decision_stream import read_recent_decisions
    rows = read_recent_decisions(asset_type, limit=max(1, min(500, int(limit))))
    return {"asset_type": asset_type, "count": len(rows), "rows": rows}



@router.get("/commander-shadow/brake-activity")
async def commander_shadow_brake_activity(request: Request, hours: int = 24):
    """Summarize Phase 2 brake activity over the last ``hours`` hours.

    Reads the ``paper_trades`` collection for rows that carry a
    ``commander_phase2_brake`` sub-doc (written inline at entry time
    by ``ml_paper_trader`` when the brake evaluator ran). Returns:

        {
            "window_hours": int,
            "since": ISO UTC,
            "trades_evaluated": int,
            "trades_braked": int,
            "trades_disagreement_logged": int,  # includes non-braked Phase 1
            "brake_rate_pct": float,
            "sample_braked_trades": [...],      # most recent 10
        }

    Complements ``/commander-shadow/promotion-status`` — the status
    endpoint shows *whether* the gate has opened, this one shows
    *how often* the brake actually fires once it does.
    """
    await _require_owner(request)
    from datetime import datetime, timezone, timedelta

    hours = max(1, min(168, int(hours)))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    base_query = {
        "opened_at": {"$gte": since},
        "commander_phase2_brake": {"$exists": True},
    }

    trades_evaluated = await db.paper_trades.count_documents(base_query)
    trades_braked = await db.paper_trades.count_documents({
        **base_query,
        "commander_phase2_brake.brake_applied": True,
    })
    trades_disagreement = await db.paper_trades.count_documents({
        **base_query,
        "commander_phase2_brake.disagreement": True,
    })

    brake_rate_pct = (
        round(trades_braked / trades_evaluated * 100, 2)
        if trades_evaluated > 0 else 0.0
    )

    cursor = db.paper_trades.find(
        {**base_query, "commander_phase2_brake.brake_applied": True},
        {
            "_id": 0,
            "ticker": 1,
            "direction": 1,
            "opened_at": 1,
            "position_size_usd": 1,
            "commander_phase2_brake": 1,
        },
    ).sort("opened_at", -1).limit(10)
    sample = []
    async for row in cursor:
        if isinstance(row.get("opened_at"), datetime):
            row["opened_at"] = row["opened_at"].replace(
                tzinfo=row["opened_at"].tzinfo or timezone.utc
            ).isoformat()
        sample.append(row)

    return {
        "window_hours": hours,
        "since": since.isoformat(),
        "trades_evaluated": trades_evaluated,
        "trades_braked": trades_braked,
        "trades_disagreement_logged": trades_disagreement,
        "brake_rate_pct": brake_rate_pct,
        "sample_braked_trades": sample,
    }



# ── Benzinga News API — slot health & smoke test ────────────────


@router.get("/benzinga/status")
async def benzinga_status(request: Request):
    """Return Benzinga integration health: key presence, daily quota
    usage, configured ceiling + interval.

    Does NOT make an outbound Benzinga call — pure DB + env read. A
    separate `/benzinga/smoke` endpoint triggers a real fetch.
    """
    await _require_owner(request)
    import os
    from services.benzinga_news_service import (
        _daily_call_count, _daily_ceiling,
        _min_interval_seconds, _today_key,
    )

    key = (os.environ.get("BENZINGA_API_KEY") or "").strip()
    key_configured = bool(key)
    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)

    return {
        "key_configured": key_configured,
        "key_fingerprint": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else None,
        "daily_ceiling": ceiling,
        "daily_calls_used": used,
        "daily_remaining": max(0, ceiling - used) if ceiling > 0 else None,
        "min_interval_seconds": _min_interval_seconds(),
        "today_utc": _today_key(),
    }


@router.post("/benzinga/smoke")
async def benzinga_smoke_test(request: Request, symbol: str = "AAPL"):
    """Trigger a single Benzinga News fetch to verify the slot is wired
    end-to-end. Consumes 1 call from the daily ceiling.

    Returns the summarized article count + channels for the symbol
    plus the raw meta block from the client (auth status, rate-limit
    headers, error_code if any). The most recent 5 sample titles are
    included for an at-a-glance operator read.
    """
    await _require_owner(request)
    from services.benzinga_news_service import fetch_news, summarize_articles

    result = await fetch_news(
        db=db,
        tickers=[symbol.upper()],
        minutes=240,
        page_size=25,
        display_output="headline",
    )
    summary = summarize_articles(result.get("articles") or [])

    return {
        "symbol": symbol.upper(),
        "summary": summary,
        "meta": result.get("meta", {}),
    }


@router.post("/benzinga/news-telemetry/{symbol}")
async def benzinga_news_telemetry_feed(request: Request, symbol: str):
    """Pull Benzinga news for a symbol and record the article count
    into the ``equity_telemetry`` rolling buffer.

    This is the NEWS_SHOCK feeder endpoint — each call contributes one
    sample to the baseline that Patent M's classifier reads. Consumes
    1 call from the daily ceiling.

    Returns the recorded sample + meta block from the upstream fetch.
    Safe to call from a scheduler; caller is responsible for cadence
    and ticker selection.
    """
    await _require_owner(request)
    from services.news_shock_feeder import fetch_and_record_news_telemetry

    return await fetch_and_record_news_telemetry(db, symbol)


@router.post("/benzinga/news-telemetry-batch")
async def benzinga_news_telemetry_batch(
    request: Request,
    symbols: str,
):
    """Feed a comma-separated batch of symbols (e.g. ``?symbols=AAPL,NVDA,MSFT``).
    Serialized by the 2s-spacing internal rate limiter. Short-circuits
    when the daily ceiling trips.

    Limit: 20 symbols per call to keep request wall-time bounded
    (~40s worst case at a 2s interval)."""
    await _require_owner(request)
    from services.news_shock_feeder import batch_feed_symbols

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"error": "no_symbols_provided", "fed": 0, "skipped": 0}
    if len(syms) > 20:
        return {
            "error": "too_many_symbols",
            "max_per_call": 20,
            "requested": len(syms),
        }
    return await batch_feed_symbols(db, syms)


# ── Alpha Vantage sentiment feeder (NEWS_SHOCK sentiment leg) ──


@router.get("/av-news/status")
async def av_news_status(request: Request):
    """Return AV sentiment feeder health: key presence, daily quota
    usage, configured ceiling + interval."""
    await _require_owner(request)
    from services.av_sentiment_feeder import (
        _api_key, _daily_call_count, _daily_ceiling,
        _min_interval_seconds, _today_key,
    )

    key = _api_key()
    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)
    return {
        "key_configured": bool(key),
        "key_fingerprint": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else None,
        "daily_ceiling": ceiling,
        "daily_calls_used": used,
        "daily_remaining": max(0, ceiling - used) if ceiling > 0 else None,
        "min_interval_seconds": _min_interval_seconds(),
        "today_utc": _today_key(),
    }


@router.post("/av-news/sentiment-telemetry/{symbol}")
async def av_news_sentiment_feed(request: Request, symbol: str):
    """Pull AV NEWS_SENTIMENT for a symbol, compute sentiment magnitude,
    record into ``equity_telemetry`` rolling buffer. Consumes 1 call
    from the AV daily ceiling."""
    await _require_owner(request)
    from services.av_sentiment_feeder import fetch_and_record_sentiment

    return await fetch_and_record_sentiment(db, symbol)


@router.post("/av-news/sentiment-telemetry-batch")
async def av_news_sentiment_batch(request: Request, symbols: str):
    """Batch sentiment feeder. 20-symbol cap; short-circuits on AV
    rate-limit or daily-ceiling hit."""
    await _require_owner(request)
    from services.av_sentiment_feeder import batch_feed_sentiment

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"error": "no_symbols_provided", "fed": 0, "skipped": 0}
    if len(syms) > 20:
        return {
            "error": "too_many_symbols",
            "max_per_call": 20,
            "requested": len(syms),
        }
    return await batch_feed_sentiment(db, syms)


@router.post("/news-feeders/tick")
async def news_feeders_manual_tick(request: Request):
    """Manually trigger one 15-min news-feeders tick. Useful for
    smoke-testing the scheduler without waiting for the next cron
    firing. Respects the market-hours gate; returns the skipped
    summary outside RTH."""
    await _require_owner(request)
    from services.news_feeders_scheduler import run_news_feeders_tick

    return await run_news_feeders_tick(db)


# ── Phase C — NEWS_SHOCK / catalyst readiness ──


@router.get("/news-shock/status")
async def news_shock_status(request: Request):
    """Return the ``catalyst_snapshots`` state — per-symbol shock
    state, z-score readiness, event risk, and the most recent headline.

    Read-only, cheap (single collection scan capped at 300 rows).
    Used by the admin dashboard + operator tooling to watch the
    Mon-AM baseline accumulation after a fresh deploy."""
    await _require_owner(request)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    cursor = db.catalyst_snapshots.find({}, {"_id": 0}).limit(300)
    rows = await cursor.to_list(length=300)

    ready_count = 0
    elevated_count = 0
    high_count = 0
    symbols = []
    for row in rows:
        shock = row.get("news_shock", {}) or {}
        ready = bool(shock.get("zscore_ready"))
        if ready:
            ready_count += 1
        if shock.get("shock_state") == "elevated":
            elevated_count += 1
        if shock.get("shock_state") == "high":
            high_count += 1
        updated = row.get("updated_at")
        if isinstance(updated, datetime):
            updated = updated.isoformat()
        symbols.append({
            "symbol": row.get("symbol"),
            "zscore_ready": ready,
            "baseline_samples": shock.get("baseline_samples", 0),
            "news_zscore": shock.get("news_zscore"),
            "shock_state": shock.get("shock_state"),
            "sentiment_label": shock.get("sentiment_label"),
            "event_risk": row.get("event_risk"),
            "latest_headline": shock.get("latest_headline"),
            "updated_at": updated,
        })

    return {
        "ready_symbols": ready_count,
        "tracked_symbols": len(rows),
        "elevated_count": elevated_count,
        "high_count": high_count,
        "symbols": symbols,
        "updated_at": now.isoformat(),
    }


@router.post("/news-shock/ensure-indexes")
async def news_shock_ensure_indexes(request: Request):
    """One-shot helper to create the three Mongo indexes the NEWS_SHOCK
    layer depends on. Idempotent; safe to re-run. Kept as an endpoint
    (rather than eager startup hook) so the operator can time the
    first creation with an empty database."""
    await _require_owner(request)
    created = []
    try:
        await db.catalyst_events.create_index(
            [("symbol", 1), ("event_time", -1)],
        )
        created.append("catalyst_events.symbol_event_time")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_events_error:{exc}")
    try:
        await db.catalyst_events.create_index("event_id", unique=True)
        created.append("catalyst_events.event_id_unique")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_events_event_id_error:{exc}")
    try:
        await db.news_telemetry.create_index(
            [("symbol", 1), ("created_at", -1)],
        )
        created.append("news_telemetry.symbol_created_at")
    except Exception as exc:  # noqa: BLE001
        created.append(f"news_telemetry_error:{exc}")
    try:
        await db.catalyst_snapshots.create_index("symbol", unique=True)
        created.append("catalyst_snapshots.symbol_unique")
    except Exception as exc:  # noqa: BLE001
        created.append(f"catalyst_snapshots_error:{exc}")
    return {"created": created}


@router.get("/news-shock/burn-in")
async def news_shock_burn_in(request: Request):
    """One-shot Monday burn-in health check.

    Aggregates the four independent health signals the operator
    should watch on first-market-open after a fresh deploy:

    1. ``scheduler_state.news_feeders_rotation`` — is the tick
       running? Last ``updated_at`` + current offset.
    2. ``catalyst_events`` — is the feeder persisting articles?
       Row count + most recent event_time.
    3. ``news_telemetry`` — is the shock-compute step recording
       baselines? Row count + most recent created_at.
    4. ``catalyst_snapshots`` — is the projection landing?
       Total tracked + ``zscore_ready`` count + top-3 most
       recently updated.
    5. ``decision_proof_chain`` — are ``SMART_MONEY_VERIFIED``
       blocks appearing on real equity decisions?
    6. ``equity_telemetry_baselines`` — is ``_warm_one`` feeding
       atr/volume/dollar_volume? Samples per symbol histogram.

    Cheap single read per collection — safe to poll every 30 s
    during the burn-in window.
    """
    await _require_owner(request)
    from datetime import datetime, timezone

    def _iso(dt):
        if isinstance(dt, datetime):
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc).isoformat()
        return dt

    # 1. Scheduler state
    rot_doc = await db.scheduler_state.find_one(
        {"_id": "news_feeders_rotation"}, {"_id": 0},
    )

    # 2. Catalyst events
    catalyst_events_total = await db.catalyst_events.count_documents({})
    latest_catalyst_event = await db.catalyst_events.find_one(
        {}, {"_id": 0, "event_time": 1, "symbol": 1, "source": 1, "headline": 1},
        sort=[("event_time", -1)],
    ) or {}

    # 3. News telemetry rows
    news_tel_total = await db.news_telemetry.count_documents({})
    latest_news_tel = await db.news_telemetry.find_one(
        {}, {"_id": 0, "created_at": 1, "symbol": 1, "news_volume": 1},
        sort=[("created_at", -1)],
    ) or {}

    # 4. Catalyst snapshots
    snapshots_total = await db.catalyst_snapshots.count_documents({})
    ready_total = await db.catalyst_snapshots.count_documents(
        {"news_shock.zscore_ready": True},
    )
    top_cursor = db.catalyst_snapshots.find(
        {}, {"_id": 0, "symbol": 1, "event_risk": 1, "updated_at": 1, "news_shock.shock_state": 1},
    ).sort("updated_at", -1).limit(3)
    top_snapshots = await top_cursor.to_list(length=3)
    for s in top_snapshots:
        s["updated_at"] = _iso(s.get("updated_at"))

    # 5. Smart Money proof-chain blocks (last 24 h)
    from datetime import timedelta
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    smart_money_blocks_24h = await db.decision_proof_chain.count_documents({
        "event_type": "SMART_MONEY_VERIFIED",
        "created_at": {"$gte": since_24h},
    })

    # 6. Equity telemetry baselines (atr/volume/dollar_volume)
    baseline_total = await db.equity_telemetry_baselines.count_documents({})
    baseline_cursor = db.equity_telemetry_baselines.find(
        {}, {"_id": 0, "symbol": 1, "samples": 1},
    ).limit(5)
    baseline_samples = []
    async for row in baseline_cursor:
        samples = row.get("samples", []) or []
        last = samples[-1] if samples else {}
        baseline_samples.append({
            "symbol": row.get("symbol"),
            "sample_count": len(samples),
            "has_dollar_volume": "dollar_volume" in last,
            "has_news_count": "news_count" in last,
            "has_news_sentiment": "news_sentiment_abs" in last,
            "latest_at": _iso(last.get("at")) if isinstance(last.get("at"), datetime) else last.get("at"),
        })

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scheduler": {
            "last_offset": (rot_doc or {}).get("offset"),
            "last_updated_at": _iso((rot_doc or {}).get("updated_at")),
        },
        "catalyst_events": {
            "total": catalyst_events_total,
            "latest_event_time": _iso(latest_catalyst_event.get("event_time")),
            "latest_symbol": latest_catalyst_event.get("symbol"),
            "latest_source": latest_catalyst_event.get("source"),
            "latest_headline": latest_catalyst_event.get("headline"),
        },
        "news_telemetry": {
            "total_rows": news_tel_total,
            "latest_created_at": _iso(latest_news_tel.get("created_at")),
            "latest_symbol": latest_news_tel.get("symbol"),
            "latest_news_volume": latest_news_tel.get("news_volume"),
        },
        "catalyst_snapshots": {
            "total": snapshots_total,
            "zscore_ready": ready_total,
            "most_recent": top_snapshots,
        },
        "smart_money_blocks_24h": smart_money_blocks_24h,
        "equity_telemetry": {
            "total_symbols_tracked": baseline_total,
            "sample": baseline_samples,
        },
    }


@router.get("/kraken-shadow/today")
async def kraken_shadow_today(request: Request):
    """Today's Kraken xStock shadow-compare summary for the burn-in card.

    Returns the same shape as ``summarize_today`` — rows count, max
    bps, p95 bps, divergent count vs threshold, alerts fired, last
    run, session counts, and a 5-row sample. Cheap single aggregation
    pass; safe to poll every 60 s alongside the rest of the burn-in.
    """
    await _require_owner(request)
    from services.kraken_equity_shadow_service import summarize_today
    return await summarize_today(db)


@router.get("/adversarial-cores/24h")
async def adversarial_cores_24h(request: Request):
    """At-a-glance read of the Bull/Bear/Commander core activity over
    the last 24 hours — decision counts, win rates (once closed rows
    exist), avg edge_gap and confidences, plus enabled + phase.

    Feeds the compact "Adversarial Cores" chip on the admin Terminal
    tab. Designed for the same cold-start-is-normal flow as the
    Kraken xStock shadow chip: zero rows returns a valid empty
    envelope rather than erroring.
    """
    await _require_owner(request)
    from services.adversarial_monitor import summarize_24h
    return await summarize_24h(db)


@router.get("/adversarial-cores/promotion")
async def adversarial_cores_promotion(request: Request):
    """Lifetime readiness for ``shadow → risk_only → veto → full``.

    Inform-only — never flips a phase env on its own. Returns the
    Commander-correct rate vs the next-transition floor, the
    Bull/Bear lifetime win-rate spread, and a copy-pastable
    ``promote_env_line`` for the operator to paste into
    ``backend/.env`` when ``ready_to_promote=true``.
    """
    await _require_owner(request)
    from services.adversarial_promotion_gate import compute_promotion_status
    return await compute_promotion_status(db)


@router.get("/calibration/status")
async def calibration_status(request: Request):
    """Active confidence-calibration model + last-fit telemetry.

    Inform-only — never refits on its own. Use the one-off script
    ``backend/scripts/fit_calibration_from_history.py`` to refit;
    the result is picked up automatically on the next request via
    ``services.calibration_service.get_active_calibration``.

    Returned shape::

        {
          "active": bool,
          "version": str | None,
          "fitted_at": ISO datetime | None,
          "n_rows": int,
          "ece_before_pp": float | None,
          "ece_after_pp": float | None,
          "max_calibrated_confidence": float,
          "calibration_applies_to": ["tier3_readiness_only"],
          "knots": [{"x": float, "y": float}, ...]
        }
    """
    await _require_owner(request)
    from services.calibration_service import (
        MAX_CALIBRATED_CONFIDENCE, get_active_calibration,
    )
    model = await get_active_calibration(db)
    if not model:
        return {
            "active": False,
            "version": None,
            "fitted_at": None,
            "n_rows": 0,
            "ece_before_pp": None,
            "ece_after_pp": None,
            "max_calibrated_confidence": MAX_CALIBRATED_CONFIDENCE,
            "calibration_applies_to": ["tier3_readiness_only"],
            "knots": [],
        }
    fitted_at = model.get("fitted_at")
    return {
        "active": bool(model.get("active")),
        "version": model.get("version"),
        "fitted_at": fitted_at.isoformat() if fitted_at else None,
        "n_rows": int(model.get("n_rows") or 0),
        "ece_before_pp": model.get("ece_before_pp"),
        "ece_after_pp": model.get("ece_after_pp"),
        "max_calibrated_confidence": float(
            model.get("max_calibrated_confidence") or MAX_CALIBRATED_CONFIDENCE,
        ),
        "calibration_applies_to": list(
            model.get("calibration_applies_to", ["tier3_readiness_only"]),
        ),
        "knots": list(model.get("knots") or []),
    }


@router.get("/ticker-abandonment/{symbol}")
async def ticker_abandonment_status(
    request: Request, symbol: str, lane: str = "equity",
):
    """Inspect the ticker-abandonment gate's current decision for a
    symbol. Inform-only — the gate runs on every paper-trade tick
    and this endpoint just shows what the gate would say right now.

    ``lane`` query param: ``"equity"`` (default) or ``"crypto"``.
    Returns the resolved inputs the gate read + the
    ``TickerExitDecision`` action / reason / cooldown_minutes.
    """
    await _require_owner(request)
    from services.ticker_abandonment import decide_ticker_exit
    from services.ticker_abandonment_stats import (
        compute_crypto_inputs, compute_equity_inputs,
    )
    sym = symbol.upper()
    if lane.lower() == "crypto":
        inputs = await compute_crypto_inputs(db, sym)
    else:
        inputs = await compute_equity_inputs(db, sym)
    decision = decide_ticker_exit(
        symbol=sym,
        recent_signals=inputs.recent_signals,
        recent_rejections=inputs.recent_rejections,
        recent_losses=inputs.recent_losses,
        recent_wins=inputs.recent_wins,
        avg_confidence=inputs.avg_confidence,
        avg_rr=inputs.avg_rr,
        last_profitable_at=inputs.last_profitable_at,
    )
    last_p = inputs.last_profitable_at
    return {
        "symbol": sym,
        "lane": lane.lower(),
        "window_days": inputs.window_days,
        "inputs": {
            "recent_signals": inputs.recent_signals,
            "recent_rejections": inputs.recent_rejections,
            "recent_wins": inputs.recent_wins,
            "recent_losses": inputs.recent_losses,
            "avg_confidence": round(inputs.avg_confidence, 4),
            "avg_rr": round(inputs.avg_rr, 4),
            "last_profitable_at": last_p.isoformat() if last_p else None,
        },
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "cooldown_minutes": decision.cooldown_minutes,
        },
    }



@router.get("/ticker-abandonment")
async def ticker_abandonment_overview(request: Request):
    """Bulk view of the ticker-abandonment gate's current decision
    across every ticker that has at least one row in the last
    ``TICKER_ABANDONMENT_WINDOW_DAYS`` for either lane.

    Discovery: scans ``paper_trades`` (equity) and
    ``crypto_paper_trades`` (crypto) for distinct symbols opened
    within the rolling window, plus distinct symbols from
    ``agent_activity`` ``paper_trade_*`` events (catches symbols
    that ONLY get rejections, never fills — which is exactly the
    high-rejection-rate cooldown bucket).

    Returns rows sorted: ABANDON first, then COOLDOWN by descending
    ``cooldown_minutes``, then KEEP. Single-glance scan for the
    operator. Inform-only.
    """
    await _require_owner(request)
    from datetime import datetime, timedelta, timezone

    from services.ticker_abandonment import decide_ticker_exit
    from services.ticker_abandonment_stats import (
        RECENT_WINDOW_DAYS, compute_crypto_inputs, compute_equity_inputs,
    )

    since = datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
    equity_symbols: set[str] = set()
    crypto_symbols: set[str] = set()

    try:
        for sym in await db["paper_trades"].distinct(
            "ticker", {"opened_at": {"$gte": since}},
        ):
            if sym:
                equity_symbols.add(str(sym).upper())
    except Exception:
        pass

    try:
        for sym in await db["crypto_paper_trades"].distinct(
            "symbol", {"opened_at": {"$gte": since}},
        ):
            if sym:
                crypto_symbols.add(str(sym).upper())
    except Exception:
        pass

    # Discover symbols that ONLY had rejections — high-rejection-rate
    # cooldown candidates won't show in paper_trades at all.
    try:
        for sym in await db["agent_activity"].distinct(
            "symbol",
            {
                "type": {"$in": ["paper_trade_open", "paper_trade_skip"]},
                "created_at": {"$gte": since},
            },
        ):
            if not sym:
                continue
            s = str(sym).upper()
            if s in {
                "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE",
                "AVAX", "LINK", "DOT", "MATIC", "BNB",
            }:
                crypto_symbols.add(s)
            else:
                equity_symbols.add(s)
    except Exception:
        pass

    rows: list[dict] = []
    for sym in sorted(equity_symbols):
        inputs = await compute_equity_inputs(db, sym)
        decision = decide_ticker_exit(
            symbol=sym,
            recent_signals=inputs.recent_signals,
            recent_rejections=inputs.recent_rejections,
            recent_losses=inputs.recent_losses,
            recent_wins=inputs.recent_wins,
            avg_confidence=inputs.avg_confidence,
            avg_rr=inputs.avg_rr,
            last_profitable_at=inputs.last_profitable_at,
        )
        rows.append(
            _serialize_abandonment_row("equity", sym, inputs, decision),
        )

    for sym in sorted(crypto_symbols):
        inputs = await compute_crypto_inputs(db, sym)
        decision = decide_ticker_exit(
            symbol=sym,
            recent_signals=inputs.recent_signals,
            recent_rejections=inputs.recent_rejections,
            recent_losses=inputs.recent_losses,
            recent_wins=inputs.recent_wins,
            avg_confidence=inputs.avg_confidence,
            avg_rr=inputs.avg_rr,
            last_profitable_at=inputs.last_profitable_at,
        )
        rows.append(
            _serialize_abandonment_row("crypto", sym, inputs, decision),
        )

    # ── Δ-since-yesterday + same-day snapshot persistence ─────────
    # For each row, look up the most recent prior snapshot's action
    # (strictly < today's UTC date) and stamp the delta envelope.
    # Then upsert today's snapshot so tomorrow's call has yesterday
    # to compare against. Idempotent — re-running same day is a
    # no-op-write of the same fields.
    from services.ticker_abandonment_history import (
        compute_action_delta, fetch_prior_action, write_snapshot,
    )
    for r in rows:
        try:
            prior = await fetch_prior_action(
                db, lane=r["lane"], symbol=r["symbol"],
            )
        except Exception:
            prior = None
        r["delta"] = compute_action_delta(r["decision"]["action"], prior)
        try:
            await write_snapshot(
                db,
                lane=r["lane"],
                symbol=r["symbol"],
                action=r["decision"]["action"],
                reason=r["decision"]["reason"],
                cooldown_minutes=r["decision"].get("cooldown_minutes") or 0,
                inputs_view=r["inputs"],
            )
        except Exception:
            pass  # snapshot write must never break the read endpoint

    # Sort: ABANDON first, COOLDOWN by descending cooldown_minutes,
    # then KEEP alphabetical.
    _action_rank = {"ABANDON": 0, "COOLDOWN": 1, "KEEP": 2}
    rows.sort(key=lambda r: (
        _action_rank.get(r["decision"]["action"], 99),
        -int(r["decision"].get("cooldown_minutes") or 0),
        r["symbol"],
    ))

    return {
        "window_days": RECENT_WINDOW_DAYS,
        "rows": rows,
        "totals": {
            "abandon": sum(
                1 for r in rows if r["decision"]["action"] == "ABANDON"
            ),
            "cooldown": sum(
                1 for r in rows if r["decision"]["action"] == "COOLDOWN"
            ),
            "keep": sum(
                1 for r in rows if r["decision"]["action"] == "KEEP"
            ),
        },
    }


def _serialize_abandonment_row(lane: str, symbol: str, inputs, decision) -> dict:
    last_p = inputs.last_profitable_at
    return {
        "lane": lane,
        "symbol": symbol,
        "inputs": {
            "recent_signals": inputs.recent_signals,
            "recent_rejections": inputs.recent_rejections,
            "recent_wins": inputs.recent_wins,
            "recent_losses": inputs.recent_losses,
            "avg_confidence": round(inputs.avg_confidence, 4),
            "avg_rr": round(inputs.avg_rr, 4),
            "last_profitable_at": last_p.isoformat() if last_p else None,
        },
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "cooldown_minutes": decision.cooldown_minutes,
        },
    }



@router.get("/news-shock/ingestion-sparkline")
async def news_shock_ingestion_sparkline(request: Request, hours: int = 24):
    """Hourly Benzinga + Alpha Vantage article ingestion counts over a
    rolling N-hour window for the burn-in sparkline.

    Returns
    -------
    {
        "hours": int,
        "buckets": ["2026-05-03T01:00:00Z", ...],   # ascending UTC hour starts
        "benzinga": [int, int, ...],                # per-bucket counts
        "alpha_vantage": [int, int, ...],
        "totals": {"benzinga": int, "alpha_vantage": int},
        "current_hour": {"benzinga": int, "alpha_vantage": int},
    }

    Aggregates ``catalyst_events`` by hour bucket. Caps at 168 hours
    (one week) to keep the aggregation cheap and the sparkline
    legible.
    """
    await _require_owner(request)
    from datetime import timedelta

    hours = max(1, min(int(hours if hours is not None else 24), 168))
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=hours - 1)

    # One aggregation pass — group by (source, hour-bucket) and emit
    # the per-source per-hour count. We fan it out into two parallel
    # arrays in Python because the bucket list is short.
    pipeline = [
        {"$match": {"event_time": {"$gte": start}}},
        {"$group": {
            "_id": {
                "source": "$source",
                "bucket": {
                    "$dateTrunc": {
                        "date": "$event_time",
                        "unit": "hour",
                        "timezone": "UTC",
                    },
                },
            },
            "count": {"$sum": 1},
        }},
    ]
    rows = await db.catalyst_events.aggregate(pipeline).to_list(length=None)

    bucket_starts: list[datetime] = []
    for i in range(hours):
        bucket_starts.append(start + timedelta(hours=i))

    benzinga = [0] * hours
    av = [0] * hours
    for r in rows:
        src = (r.get("_id", {}) or {}).get("source")
        bucket = (r.get("_id", {}) or {}).get("bucket")
        if not isinstance(bucket, datetime):
            continue
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        # Find bucket index by hour delta.
        delta_h = int((bucket - start).total_seconds() // 3600)
        if delta_h < 0 or delta_h >= hours:
            continue
        if src == "benzinga":
            benzinga[delta_h] = int(r.get("count", 0))
        elif src == "alpha_vantage":
            av[delta_h] = int(r.get("count", 0))

    return {
        "hours": hours,
        "buckets": [b.isoformat() for b in bucket_starts],
        "benzinga": benzinga,
        "alpha_vantage": av,
        "totals": {
            "benzinga": sum(benzinga),
            "alpha_vantage": sum(av),
        },
        "current_hour": {
            "benzinga": benzinga[-1] if benzinga else 0,
            "alpha_vantage": av[-1] if av else 0,
        },
    }


# ============================================================
# DAY-TRADE SCANNER (scan → rank → gate → queue top-1)
# ============================================================


def _scan_result_to_dict(result) -> dict:
    """Serialise a ``ScanResult`` to a JSON-safe dict. ``_id`` is
    never part of the dataclass so no Mongo leak risk."""
    from dataclasses import asdict as _asdict
    chosen = _asdict(result.chosen) if result.chosen is not None else None
    return {
        "scan_id": result.scan_id,
        "asset_class": result.asset_class,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "total_scanned": result.total_scanned,
        "blocked_count": result.blocked_count,
        "chosen": chosen,
        "candidates": [_asdict(c) for c in result.candidates],
    }


@router.post("/day-trade/scan/{asset_class}")
async def day_trade_manual_scan(asset_class: str, request: Request):
    """Manually trigger a scan → rank → gate → queue pass for
    ``equity`` or ``crypto``. Returns the ranked candidate list +
    the chosen winner (if any). Same code path as the scheduled
    scanner — every manual run is fully audit-logged."""
    await _require_owner(request)
    lane = (asset_class or "").strip().lower()
    if lane not in ("equity", "crypto"):
        raise HTTPException(
            status_code=400, detail="asset_class must be 'equity' or 'crypto'",
        )
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.day_trade_scanner import run_scan
    result = await run_scan(db, lane)  # type: ignore[arg-type]
    return _scan_result_to_dict(result)


@router.get("/day-trade/scan/recent")
async def day_trade_recent_scans(request: Request, limit: int = 20):
    """Recent scan-log rows (both lanes, newest first). Used by
    the admin dashboard to show scanner activity. No mutation."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 100))
    cursor = db.day_trade_scan_log.find(
        {}, {"_id": 0},
    ).sort("finished_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        for k in ("started_at", "finished_at"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"rows": rows, "count": len(rows)}


@router.get("/day-trade/targets")
async def day_trade_targets(request: Request, status: str = "pending", limit: int = 50):
    """List day-trade targets (pending by default). Operator view
    of the scanner's outgoing queue."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    limit = max(1, min(int(limit), 200))
    query: dict = {}
    if status and status.lower() != "all":
        query["status"] = status.lower()
    cursor = db.day_trade_targets.find(
        query, {"_id": 0},
    ).sort("queued_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        for k in ("queued_at", "max_hold_until"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"rows": rows, "count": len(rows), "status_filter": status}


@router.post("/day-trade/exit-monitor/tick")
async def day_trade_exit_tick(request: Request):
    """Manually trigger the day-trade EOD exit monitor. Useful for
    smoke-testing the 21:00 UTC close path without waiting."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.day_trade_exit_monitor import expire_due_day_trades
    counts = await expire_due_day_trades(db)
    return {"status": "ok", **counts}


# ============================================================
# KRAKEN CRYPTO QUOTES — primary live provider probe
# ============================================================


@router.get("/kraken-crypto-quotes/probe")
async def kraken_crypto_quotes_probe(
    request: Request, symbols: str = "BTC,ETH,SOL",
):
    """Live probe of Kraken as the primary crypto quote source.

    Accepts a comma-separated list of canonical symbols and returns
    whatever Kraken answered, bypassing the local TTL cache so every
    call is a fresh exchange hop. Useful for verifying latency /
    spread / geo-block state from the pod."""
    await _require_owner(request)
    from services.kraken_crypto_quotes import (
        fetch_kraken_quotes_batch, _to_kraken_pair,
    )
    req = [s.strip() for s in symbols.split(",") if s.strip()]
    out = await fetch_kraken_quotes_batch(req)
    # Report which symbols Kraken couldn't resolve so the operator
    # can adjust the universe.
    unresolved = [s for s in req if _to_kraken_pair(s.upper()) is None]
    missing = [s for s in req if s.upper() not in out and s not in unresolved]
    return {
        "requested": req,
        "resolved_count": len(out),
        "quotes": out,
        "unresolved_symbols": unresolved,
        "missing_from_kraken": missing,
    }


# ============================================================
# ALPACA EQUITY QUOTES — primary US equity live provider probe
# ============================================================


@router.get("/alpaca-equity-quotes/probe")
async def alpaca_equity_quotes_probe(
    request: Request, symbols: str = "AAPL,MSFT,SPY",
):
    """Live probe of Alpaca as the primary US equity quote source.

    Bypasses the local TTL cache. After-hours / closed-market state
    will surface as ``ask=null spread_bps=null`` with ``price`` set
    to last trade — that's expected, not an error."""
    await _require_owner(request)
    from services.alpaca_equity_quotes import fetch_alpaca_equity_quotes_batch
    req = [s.strip() for s in symbols.split(",") if s.strip()]
    out = await fetch_alpaca_equity_quotes_batch(req)
    missing = [s for s in req if s.upper() not in out]
    return {
        "requested": req,
        "resolved_count": len(out),
        "quotes": out,
        "missing_from_alpaca": missing,
    }


# ============================================================
# LIVE SPREAD WATCH — cross-asset liquidity-stress monitor
# ============================================================


def _market_session_label() -> str:
    """Coarse US market session classifier — RTH / pre / post /
    closed. Pure UTC math, no holiday calendar."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sat / Sun
        return "closed"
    # RTH: 13:30–20:00 UTC (covers EST 9:30–16:00 with no DST guard
    # — the chip just informs the operator; precise calendar logic
    # lives in services.kraken_equity_shadow_service).
    minutes = now.hour * 60 + now.minute
    if 13 * 60 + 30 <= minutes < 20 * 60:
        return "rth"
    if 8 * 60 <= minutes < 13 * 60 + 30:
        return "pre"
    if 20 * 60 <= minutes < 24 * 60:
        return "post"
    return "closed"


@router.get("/spread-watch")
async def spread_watch(
    request: Request,
    crypto_symbols: str = "BTC,ETH,SOL,DOGE,XRP",
    equity_symbols: str = "SPY,QQQ,AAPL,MSFT,NVDA",
    stress_threshold_bps: float = 25.0,
):
    """Cross-asset live spread snapshot.

    Pulls Kraken (crypto) + Alpaca (equity) in parallel, normalises
    the response shape, and flags any symbol whose ``spread_bps``
    exceeds ``stress_threshold_bps`` as a liquidity-stress signal.

    For equities, post-close one-sided quotes (``spread_bps=null``)
    are explicitly NOT counted as stress — that's the normal AH
    state. The frontend can use ``market_session`` to gate the
    "WIDE" pill so wide AH spreads don't trigger false alarms.
    """
    await _require_owner(request)
    import asyncio

    from services.kraken_crypto_quotes import fetch_kraken_quotes_batch
    from services.alpaca_equity_quotes import fetch_alpaca_equity_quotes_batch

    crypto_list = [s.strip() for s in crypto_symbols.split(",") if s.strip()]
    equity_list = [s.strip() for s in equity_symbols.split(",") if s.strip()]

    crypto_task = fetch_kraken_quotes_batch(crypto_list) if crypto_list else None
    equity_task = fetch_alpaca_equity_quotes_batch(equity_list) if equity_list else None

    crypto_out: dict = {}
    equity_out: dict = {}
    if crypto_task is not None and equity_task is not None:
        crypto_out, equity_out = await asyncio.gather(crypto_task, equity_task)
    elif crypto_task is not None:
        crypto_out = await crypto_task
    elif equity_task is not None:
        equity_out = await equity_task

    session = _market_session_label()

    def _row(lane: str, symbol: str, q: dict) -> dict:
        spread = q.get("spread_bps")
        stressed = (
            spread is not None
            and lane == "crypto" or (lane == "equity" and session == "rth")
        ) and (spread is not None and spread > stress_threshold_bps)
        return {
            "lane": lane,
            "symbol": symbol,
            "price": q.get("price"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "last": q.get("last"),
            "spread_bps": spread,
            "source": q.get("source"),
            "stressed": bool(stressed),
        }

    rows: list[dict] = []
    for sym in crypto_list:
        q = crypto_out.get(sym.upper())
        if q is not None:
            rows.append(_row("crypto", sym.upper(), q))
    for sym in equity_list:
        q = equity_out.get(sym.upper())
        if q is not None:
            rows.append(_row("equity", sym.upper(), q))

    stressed_count = sum(1 for r in rows if r["stressed"])
    return {
        "rows": rows,
        "row_count": len(rows),
        "stressed_count": stressed_count,
        "market_session": session,
        "stress_threshold_bps": stress_threshold_bps,
        "missing_crypto": [
            s for s in crypto_list if s.upper() not in crypto_out
        ],
        "missing_equity": [
            s for s in equity_list if s.upper() not in equity_out
        ],
    }


# ============================================================
# AI PROMOTION HISTORY (audit trail)
# ============================================================


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


# ============================================================
# POST-TRADE AUTOPSY
# ============================================================


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


# ============================================================
# KRAKEN WEBSOCKET STREAM — push-based crypto quotes
# ============================================================


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
    import time as _t
    ages: dict[str, float | None] = {}
    for sym in list(base.get("tracked_symbols", [])):
        q = await get_streamed_quote(sym)
        if q is None:
            ages[sym] = None
            continue
        ts = q.get("ts")
        ages[sym] = round(_t.time() - float(ts), 2) if ts else None
    base["snapshot_ages_sec"] = ages
    return base



# ============================================================
# SLIPPAGE ATTRIBUTION — realised spread cost vs strategy P&L
# ============================================================


@router.get("/slippage-attribution")
async def slippage_attribution_summary(
    request: Request, lane: str = "all", lookback_days: int = 30,
):
    """Aggregate realised slippage cost across recently closed
    trades. Returns per-lane and per-method roll-ups so the
    operator can see what the spread is taking from the strategy.

    * ``lane`` ∈ ``equity`` / ``crypto`` / ``all``
    * ``lookback_days`` window applies to ``closed_at``
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from datetime import datetime, timezone, timedelta
    from services.post_trade_autopsy import build_post_trade_autopsy

    lane_norm = (lane or "all").strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))

    colls: list[tuple[str, str]] = []
    if lane_norm in ("equity", "all"):
        colls.append(("paper_trades", "equity"))
    if lane_norm in ("crypto", "all"):
        colls.append(("crypto_paper_trades", "crypto"))

    aggregates: dict[str, dict] = {}
    by_method: dict[str, dict] = {}
    grand_pnl = 0.0
    grand_drag = 0.0
    grand_count = 0
    rows_with_slippage = 0

    for coll, label in colls:
        cursor = db[coll].find({
            "status": "closed",
            "closed_at": {"$gte": cutoff},
        }, {"_id": 0})
        async for r in cursor:
            grand_count += 1
            pnl = float(r.get("pnl_usd", r.get("pnl", 0.0)) or 0.0)
            grand_pnl += pnl
            slippage = (r.get("autopsy") or {}).get("slippage")
            if slippage is None:
                # Compute on-the-fly for rows that predate the
                # autopsy stamp.
                slippage = build_post_trade_autopsy(r).get("slippage")
            if slippage is None:
                continue
            rows_with_slippage += 1
            cost = float(slippage.get("total_dollar_cost") or 0.0)
            grand_drag += cost
            method = slippage.get("fill_method") or "unknown"
            method_row = by_method.setdefault(
                method,
                {"count": 0, "dollar_cost": 0.0, "bps_sum": 0.0},
            )
            method_row["count"] += 1
            method_row["dollar_cost"] += cost
            method_row["bps_sum"] += float(slippage.get("total_bps") or 0.0)
            lane_row = aggregates.setdefault(
                label,
                {"count": 0, "dollar_cost": 0.0, "bps_sum": 0.0, "pnl": 0.0},
            )
            lane_row["count"] += 1
            lane_row["dollar_cost"] += cost
            lane_row["bps_sum"] += float(slippage.get("total_bps") or 0.0)
            lane_row["pnl"] += pnl

    def _finalise(rows: dict) -> dict:
        out: dict = {}
        for k, v in rows.items():
            n = max(v["count"], 1)
            out[k] = {
                "count": v["count"],
                "dollar_cost": round(v["dollar_cost"], 2),
                "avg_bps": round(v["bps_sum"] / n, 2),
            }
            if "pnl" in v:
                out[k]["pnl"] = round(v["pnl"], 2)
                # Drag % = drag / |pnl| × 100 (capped, useful only
                # when there's some pnl to compare against).
                pnl_abs = abs(v["pnl"]) or 1.0
                out[k]["drag_pct_of_abs_pnl"] = round(
                    (v["dollar_cost"] / pnl_abs) * 100.0, 2,
                )
        return out

    return {
        "lane_filter": lane_norm,
        "lookback_days": lookback_days,
        "totals": {
            "trades": grand_count,
            "trades_with_slippage_stamp": rows_with_slippage,
            "total_pnl_usd": round(grand_pnl, 2),
            "total_slippage_drag_usd": round(grand_drag, 2),
            "drag_pct_of_abs_pnl": round(
                (grand_drag / (abs(grand_pnl) or 1.0)) * 100.0, 2,
            ),
            "avg_bps_per_trade": (
                round(
                    sum(v["bps_sum"] for v in by_method.values())
                    / max(rows_with_slippage, 1),
                    2,
                )
            ),
        },
        "by_lane": _finalise(aggregates),
        "by_method": _finalise(by_method),
    }


# ============================================================
# CROSS-ASSET STRESS EVENTS — auto-flatten + audit
# ============================================================


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
    result = await run_stress_check(db)
    return result



# ============================================================
# TIER-3 SLIPPAGE ADVISOR — env-tweak proposals
# ============================================================


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



# ============================================================
# NOTIFICATION LIFECYCLE — manual cleanup trigger
# ============================================================


@router.post("/notifications/lifecycle/cleanup")
async def notifications_lifecycle_cleanup(
    request: Request, types: str | None = None,
):
    """Manually run the notification-lifecycle cleanup. Optional
    ``types`` param is a comma-separated list (e.g.
    ``toxic_spike,verdict_change``) to scope the run; omitting it
    runs every registered superseder.

    Same code path the regrade backfill calls automatically and
    the operator's one-shot script invokes from CLI."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.notification_lifecycle import supersede_stale_alerts
    type_list: list[str] | None = None
    if types:
        type_list = [t.strip() for t in types.split(",") if t.strip()]
    return await supersede_stale_alerts(db, types=type_list)
