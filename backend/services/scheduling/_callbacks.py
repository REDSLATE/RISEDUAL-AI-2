"""Inline scheduler callbacks — extracted from ``services.scheduling.jobs``.

Every callback here is a verbatim move of an inline ``async def`` that
previously lived inside ``register_all``. Each one closes over ``db`` via
an explicit parameter — wired in from the scheduler via ``args=[db]`` —
so behaviour is identical to the closure form.

Block comments above each function preserve the original production-
incident rationale (cadence choices, idempotency notes, gate semantics).
"""
import logging
import os
from datetime import datetime, timezone


# Daily ticker-abandonment snapshot — runs at 21:05 UTC,
# right after the regular US session close (matches the
# existing top-universe-warm-post-close pattern). Belt-and-
# suspenders alongside the on-read upsert in the admin
# endpoint: guarantees the Δ-since-yesterday column has
# data even if nobody opens the admin page on a given day.
# Picked 21:05 UTC over 01:00 UTC to keep the snapshot's
# date stamp on the SAME UTC day as the trading session —
# otherwise the Δ comparison breaks because the session-
# ending snapshot would land on tomorrow's date.
# Idempotent (safe to run repeatedly same-day). Never raises.
async def run_ticker_abandonment_snapshot(db):
    try:
        from services.ticker_abandonment_history import snapshot_today
        await snapshot_today(db)
    except Exception:
        logging.exception("ticker_abandonment_snapshot failed (non-critical)")


# Nightly data-integrity tripwire — runs at 03:15 UTC,
# AFTER memory_cleanup (02:00) and ml_retrain (02:30) so the
# latest grading / retag is reflected. Writes its summary to
# the `data_integrity_audits` Mongo collection; admin UI
# surfaces it via /api/admin/data-integrity/summary. Never
# mutates production data — tripwire only.
async def run_nightly_integrity_audit(db):
    try:
        from services.data_integrity_auditor import run_nightly_integrity_audit
        await run_nightly_integrity_audit(db, window_hours=24)
    except Exception:
        logging.exception("nightly_integrity_audit failed (non-critical)")


# Data-integrity alert evaluator — runs every 15 minutes
# checking every enabled rule against the same metrics the
# admin dashboard reads from. Throttle + event log live on
# the rule documents themselves, so this job is safe to
# re-run and stateless in memory.
async def run_integrity_alert_evaluator(db):
    try:
        from services.data_integrity_alerts import evaluate_rules_once
        summary = await evaluate_rules_once(db)
        if summary.get("fired", 0) > 0:
            logging.info(
                "[integrity_alerts] fired %d rule(s); throttled=%d errors=%d",
                summary["fired"], summary.get("throttled", 0),
                summary.get("errors", 0),
            )
    except Exception:
        logging.exception("integrity_alert_evaluator failed (non-critical)")


# Integrity-mitigation TTL sweep — runs every 5 minutes so
# the admin dashboard, position sizer, and strong-signal
# suppressor see an expired mitigation within ≤5min of its
# TTL instead of waiting for the next alert evaluation.
# Also primes the sync-side cache used by pure-math sizing
# paths that can't await. No-ops when nothing is active.
async def run_integrity_mitigation_sweep(db):
    try:
        from services.integrity_mitigation_service import (
            expire_integrity_mitigations,
            refresh_sync_cache,
        )
        await expire_integrity_mitigations(db)
        await refresh_sync_cache(db)
    except Exception:
        logging.exception("integrity_mitigation_sweep failed (non-critical)")


# Notification lifecycle sweep — runs the registered
# superseders (toxic_spike, verdict_change, ...) twice daily
# so stale alerts disappear from the drawer without waiting
# for an admin button-click or a regrade-backfill run. Cheap
# operation: re-checks source rows, supersedes only when the
# source no longer supports the alert. Idempotent — re-runs
# are safe. Each run writes a receipt to
# ``notification_lifecycle_runs`` with trigger=``scheduled_cron``.
# Cadence: 04:00 UTC (post-overnight backfills) and 16:00 UTC
# (pre-US-close) so an alert that becomes stale during the
# session is cleared within ~12 hours.
async def run_notification_lifecycle_sweep(db):
    try:
        from services.notification_lifecycle import supersede_stale_alerts
        summary = await supersede_stale_alerts(db, trigger="scheduled_cron")
        totals = summary.get("totals") or {}
        if totals.get("superseded", 0) > 0:
            logging.info(
                "[notif_lifecycle] superseded=%d kept_active=%d checked=%d",
                totals.get("superseded", 0),
                totals.get("kept_active", 0),
                totals.get("checked", 0),
            )
    except Exception:
        logging.exception("notification_lifecycle_sweep failed (non-critical)")


# NEWS_SHOCK feeders — drives both Benzinga (news volume) and
# Alpha Vantage (sentiment) telemetry population on a 15-min
# market-hours cadence over a rotating slice of Tier A. The
# market-hours gate is inside the tick, so this job is safe to
# run every 15 min around the clock — it no-ops outside RTH
# without consuming any provider quota.
async def run_news_feeders_tick(db):
    try:
        from services.news_feeders_scheduler import run_news_feeders_tick
        summary = await run_news_feeders_tick(db)
        if summary.get("status") == "ran":
            logging.info(
                "[news_feeders] fed=%d+%d skipped=%d+%d offset→%d",
                summary.get("benzinga", {}).get("fed", 0),
                summary.get("av", {}).get("fed", 0),
                summary.get("benzinga", {}).get("skipped", 0),
                summary.get("av", {}).get("skipped", 0),
                summary.get("new_offset", -1),
            )
    except Exception:
        logging.exception("news_feeders_tick failed (non-critical)")


# Kraken xStock equity shadow comparator — Phase 0 (market data
# only, no orders). Hits Kraken's public Ticker + AssetPairs
# endpoints and shadow-compares against our primary Alpaca/AV
# quote for the top-N ML watchlist + S&P500 universe. Writes
# one row per (symbol × tick) into ``kraken_equity_shadow_compare``.
# GATED: the inner ``run_kraken_shadow_compare_once`` short-circuits
# unless ``KRAKEN_SHADOW_ENABLED`` is set, so this job is safe
# to register unconditionally — cold-start pods stay silent.
async def run_kraken_shadow_compare(db):
    try:
        from services.kraken_equity_shadow_service import (
            ensure_indexes,
            run_kraken_shadow_compare_once,
        )
        await ensure_indexes(db)  # Cheap idempotent index pass — safe every tick.
        summary = await run_kraken_shadow_compare_once(db)
        if summary.get("ok") and summary.get("rows_written", 0) > 0:
            logging.info(
                "[kraken_shadow] tick rows=%d alerts=%d session=%s",
                summary.get("rows_written", 0),
                summary.get("alerts_fired", 0),
                summary.get("session"),
            )
    except Exception:
        logging.exception("kraken_shadow_compare failed (non-critical)")


# Sovereign AI Resolution Loop — back-patches
# sovereign_decisions.outcomes.{60m|4h|eod} from closed paper_trades
# via the sovereign_decision_id link. Every 15 min, batch cap 50 per
# (horizon, asset) slice so a backlog can't starve the rest of the
# fleet. Never raises — internal exceptions become structured logs.
async def run_sovereign_resolution_tick(db):
    try:
        from services.sovereign_resolution_loop import run_resolution_tick
        summary = await run_resolution_tick(db)
        if summary.get("total_resolved", 0):
            logging.info(
                "[sovereign_resolve] resolved=%d per_horizon=%s",
                summary.get("total_resolved", 0),
                [(t["horizon"], t["asset_type"], t["resolved"])
                    for t in summary.get("per_horizon", [])],
            )
    except Exception:
        logging.exception("sovereign_resolution_tick failed (non-critical)")


# Fear & Greed history refresh — daily at 04:30 UTC (after the
# alternative.me daily snapshot rolls over). Pulls the last 30
# days and upserts them so any backfills/corrections from the
# source are reflected. Lazy seeding on first dashboard hit
# handles cold-start; this job keeps the store fresh thereafter.
async def run_fear_greed_refresh(db):
    try:
        from services.fear_greed_service import refresh_fear_greed_history
        n = await refresh_fear_greed_history()
        logging.info(f"[fear_greed] daily refresh wrote {n} rows")
    except Exception:
        logging.exception("fear_greed_refresh failed (non-critical)")


# Top-universe tiered pre-warm (user-requested Phase 1) ──
# Three jobs, all non-critical — a failure here never blocks
# anything downstream because they only *seed* caches that
# services already check-through-and-fall-back.
#   - rebuild:       Sunday 00:00 UTC (~500 OVERVIEW calls, weekly)
#   - warm post-close: 21:05 UTC daily (Tier A full, Tier B light)
#   - warm pre-open:   13:00 UTC daily (Tier A quote + technicals)
async def run_universe_rebuild(db):
    try:
        from services.top_universe_service import rebuild_universe
        await rebuild_universe(db)
    except Exception:
        logging.exception("top_universe_rebuild failed (non-critical)")


async def run_universe_warm_post_close(db):
    try:
        from services.top_universe_service import warm_universe
        await warm_universe(db, run_type="post_close")
    except Exception:
        logging.exception("top_universe_warm_post_close failed (non-critical)")


async def run_universe_warm_pre_open(db):
    try:
        from services.top_universe_service import warm_universe
        await warm_universe(db, run_type="pre_open")
    except Exception:
        logging.exception("top_universe_warm_pre_open failed (non-critical)")


# Options universe warm — fires every 5 min. The service itself
# is market-hours-gated (13:30–21:00 UTC Mon–Fri); off-hours calls
# write a "skipped" stats row and exit fast, so the 5-min cadence
# keeps the snapshot fresh during session without burning quota
# on static overnight data.
async def run_options_universe_warm(db):
    try:
        from services.options_universe_service import warm_options_universe
        await warm_options_universe(db)
    except Exception:
        logging.exception("options_universe_warm failed (non-critical)")


# Dedicated scheduler heartbeat — writes every 60s to a tiny
# ``scheduler_heartbeat`` document. Replaces the old "infer
# heartbeat from arbitrary other writes" proxy which would
# show false-stale on quiet pods (e.g. when no paper trades
# opened in the last day, the proxy reported the scheduler as
# dead even though it was firing every minute).
#
# Module-level ``from datetime import datetime, timezone`` already
# covers this scope. No inner re-import (see 2026-05-06 incident —
# function-local datetime imports in the parent scope took the
# scheduler down for 3 days). Defensive consistency: do not
# re-introduce.
async def write_scheduler_heartbeat(db):
    try:
        await db.scheduler_heartbeat.update_one(
            {"_id": "main"},
            {
                "$set": {
                    "last_beat_at": datetime.now(timezone.utc),
                    "host_pid": os.getpid(),
                },
                "$inc": {"beat_count": 1},
            },
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        logging.exception("scheduler_heartbeat write failed")
