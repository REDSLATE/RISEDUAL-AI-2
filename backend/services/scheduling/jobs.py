"""APScheduler job registrations.

Strangler-split out of ``server._start_schedulers`` (2026-05-08).

Behaviour-preservation contract
-------------------------------
This module's ``register_all`` is a verbatim move of every
``scheduler.add_job(...)`` call that previously lived in-place
inside ``server._start_schedulers``. **Every job ID, every cron
expression, every interval, every replace_existing flag, every
``next_run_time`` kwarg is identical to before the split.**

Inline async closures (``_run_ticker_abandonment_snapshot``,
``_run_nightly_integrity_audit``, etc.) close over the ``db``
parameter exactly as they used to close over the enclosing
``_start_schedulers`` scope. Module-level callbacks (e.g.
``_check_smart_orders``, ``_run_grid_bots``, ``_run_etl_job``)
are looked up on the live ``server`` module passed in as
``server_mod`` so we don't introduce a circular import at module
load time.
"""
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def register_all(scheduler, db, server_mod):
    """Register every APScheduler job onto ``scheduler``.

    Args:
        scheduler: a started-or-not AsyncIOScheduler instance.
        db: the live Motor database handle (closures depend on it).
        server_mod: the ``server`` module — used for module-level
            callback lookups (``server_mod._check_smart_orders``,
            ``server_mod._run_grid_bots``, etc.). Passed in instead
            of imported here to avoid a circular import.

    Side effects:
        Mutates ``scheduler`` by adding jobs. Does NOT call
        ``scheduler.start()`` — the caller (server._start_schedulers)
        is responsible for that, plus self-test wiring + error pump.
    """
    s = server_mod  # short alias for the verbatim move below

    from services.digest_service import send_daily_digest
    scheduler.add_job(send_daily_digest, 'cron', hour=6, minute=0, args=[db], id='daily_digest')
    scheduler.add_job(s._pregen_watchlist_intel, 'cron', hour=5, minute=30, args=[db], id='watchlist_pregen')
    scheduler.add_job(s._run_memory_cleanup, 'cron', hour=2, minute=0, id='memory_cleanup')
    scheduler.add_job(s._run_waitlist_auto_invite, 'cron', hour=9, minute=0, id='waitlist_auto_invite')
    scheduler.add_job(s._check_smart_orders, 'interval', seconds=30, id='smart_order_monitor')
    scheduler.add_job(s._run_grid_bots, 'interval', seconds=30, id='grid_bot_monitor')
    scheduler.add_job(s._run_signal_bot_dispatcher, 'interval', minutes=5, id='signal_bot_dispatcher')
    scheduler.add_job(s._run_headlines_pipeline, 'interval', minutes=15, id='headlines_pipeline')
    scheduler.add_job(s._run_prediction_prewarm, 'interval', minutes=10, id='prediction_prewarm')
    scheduler.add_job(s._run_prediction_labeler, 'interval', hours=1, id='prediction_labeler')
    scheduler.add_job(s._run_fred_snapshot, 'cron', hour=7, minute=0, id='fred_daily_snapshot')
    scheduler.add_job(s._run_13f_scan, 'cron', hour=8, minute=0, id='sec_13f_daily_scan')
    scheduler.add_job(s._run_referral_hit_rewards, 'cron', hour=9, minute=0, id='referral_hit_rewards_daily')
    scheduler.add_job(s._run_referral_monthly_rewards, 'cron', day=1, hour=9, minute=30, id='referral_monthly_rewards')
    scheduler.add_job(s._run_help_search_digest, 'cron', day_of_week='mon', hour=7, minute=0, id='help_search_weekly_digest')
    scheduler.add_job(s._run_usaspending_warmup, 'cron', hour=3, minute=30, id='usaspending_warmup')
    scheduler.add_job(s._run_nightly_ml_retrain, 'cron', hour=2, minute=30, id='nightly_ml_retrain')
    scheduler.add_job(s._run_self_test_monitor, 'interval', minutes=15, id='self_test_monitor')
    scheduler.add_job(s._run_conviction_drift_check, 'cron', hour=8, minute=0, id='conviction_drift_check')

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
    async def _run_ticker_abandonment_snapshot():
        try:
            from services.ticker_abandonment_history import snapshot_today
            await snapshot_today(db)
        except Exception:
            logging.exception(
                "ticker_abandonment_snapshot failed (non-critical)",
            )
    scheduler.add_job(
        _run_ticker_abandonment_snapshot,
        'cron', hour=21, minute=5,
        id='ticker_abandonment_snapshot',
    )

    # Nightly data-integrity tripwire — runs at 03:15 UTC,
    # AFTER memory_cleanup (02:00) and ml_retrain (02:30) so the
    # latest grading / retag is reflected. Writes its summary to
    # the `data_integrity_audits` Mongo collection; admin UI
    # surfaces it via /api/admin/data-integrity/summary. Never
    # mutates production data — tripwire only.
    async def _run_nightly_integrity_audit():
        try:
            from services.data_integrity_auditor import run_nightly_integrity_audit
            await run_nightly_integrity_audit(db, window_hours=24)
        except Exception:
            logging.exception("nightly_integrity_audit failed (non-critical)")
    scheduler.add_job(
        _run_nightly_integrity_audit,
        'cron', hour=3, minute=15,
        id='nightly_integrity_audit',
    )

    # Data-integrity alert evaluator — runs every 15 minutes
    # checking every enabled rule against the same metrics the
    # admin dashboard reads from. Throttle + event log live on
    # the rule documents themselves, so this job is safe to
    # re-run and stateless in memory.
    async def _run_integrity_alert_evaluator():
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
    scheduler.add_job(
        _run_integrity_alert_evaluator,
        'interval', minutes=15,
        id='integrity_alert_evaluator',
    )

    # Integrity-mitigation TTL sweep — runs every 5 minutes so
    # the admin dashboard, position sizer, and strong-signal
    # suppressor see an expired mitigation within ≤5min of its
    # TTL instead of waiting for the next alert evaluation.
    # Also primes the sync-side cache used by pure-math sizing
    # paths that can't await. No-ops when nothing is active.
    async def _run_integrity_mitigation_sweep():
        try:
            from services.integrity_mitigation_service import (
                expire_integrity_mitigations,
                refresh_sync_cache,
            )
            await expire_integrity_mitigations(db)
            await refresh_sync_cache(db)
        except Exception:
            logging.exception("integrity_mitigation_sweep failed (non-critical)")
    scheduler.add_job(
        _run_integrity_mitigation_sweep,
        'interval', minutes=5,
        id='integrity_mitigation_sweep',
    )

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
    async def _run_notification_lifecycle_sweep():
        try:
            from services.notification_lifecycle import supersede_stale_alerts
            summary = await supersede_stale_alerts(
                db, trigger="scheduled_cron",
            )
            totals = summary.get("totals") or {}
            if totals.get("superseded", 0) > 0:
                logging.info(
                    "[notif_lifecycle] superseded=%d kept_active=%d checked=%d",
                    totals.get("superseded", 0),
                    totals.get("kept_active", 0),
                    totals.get("checked", 0),
                )
        except Exception:
            logging.exception(
                "notification_lifecycle_sweep failed (non-critical)"
            )
    scheduler.add_job(
        _run_notification_lifecycle_sweep,
        'cron', hour='4,16', minute=0,
        id='notification_lifecycle_sweep',
    )

    # NEWS_SHOCK feeders — drives both Benzinga (news volume) and
    # Alpha Vantage (sentiment) telemetry population on a 15-min
    # market-hours cadence over a rotating slice of Tier A. The
    # market-hours gate is inside the tick, so this job is safe to
    # run every 15 min around the clock — it no-ops outside RTH
    # without consuming any provider quota.
    async def _run_news_feeders_tick():
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
    scheduler.add_job(
        _run_news_feeders_tick,
        'interval', minutes=15,
        id='news_feeders_tick',
    )

    # Kraken xStock equity shadow comparator — Phase 0 (market data
    # only, no orders). Hits Kraken's public Ticker + AssetPairs
    # endpoints and shadow-compares against our primary Alpaca/AV
    # quote for the top-N ML watchlist + S&P500 universe. Writes
    # one row per (symbol × tick) into ``kraken_equity_shadow_compare``.
    # GATED: the inner ``run_kraken_shadow_compare_once`` short-circuits
    # unless ``KRAKEN_SHADOW_ENABLED`` is set, so this job is safe
    # to register unconditionally — cold-start pods stay silent.
    async def _run_kraken_shadow_compare():
        try:
            from services.kraken_equity_shadow_service import (
                ensure_indexes,
                run_kraken_shadow_compare_once,
            )
            # Cheap idempotent index pass — safe every tick.
            await ensure_indexes(db)
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
    scheduler.add_job(
        _run_kraken_shadow_compare,
        'interval', minutes=5,
        id='kraken_shadow_compare',
    )

    # Sovereign AI Resolution Loop — back-patches
    # sovereign_decisions.outcomes.{60m|4h|eod} from closed paper_trades
    # via the sovereign_decision_id link. Every 15 min, batch cap 50 per
    # (horizon, asset) slice so a backlog can't starve the rest of the
    # fleet. Never raises — internal exceptions become structured logs.
    async def _run_sovereign_resolution_tick():
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
    scheduler.add_job(
        _run_sovereign_resolution_tick,
        'interval', minutes=15,
        id='sovereign_resolution_tick',
    )

    # Fear & Greed history refresh — daily at 04:30 UTC (after the
    # alternative.me daily snapshot rolls over). Pulls the last 30
    # days and upserts them so any backfills/corrections from the
    # source are reflected. Lazy seeding on first dashboard hit
    # handles cold-start; this job keeps the store fresh thereafter.
    async def _run_fear_greed_refresh():
        try:
            from services.fear_greed_service import refresh_fear_greed_history
            n = await refresh_fear_greed_history()
            logging.info(f"[fear_greed] daily refresh wrote {n} rows")
        except Exception:
            logging.exception("fear_greed_refresh failed (non-critical)")
    scheduler.add_job(
        _run_fear_greed_refresh,
        'cron', hour=4, minute=30,
        id='fear_greed_refresh',
    )
    scheduler.add_job(s._run_tier3_readiness_digest, 'cron', hour=8, minute=15, id='tier3_readiness_digest')
    scheduler.add_job(s._run_ml_health_digest, 'cron', hour=8, minute=0, id='ml_health_digest')
    scheduler.add_job(s._run_paper_trade_closer, 'interval', minutes=60, id='paper_trade_closer')
    # ── Tier-3 paper bot closer (Schema-B fill-pair closer) ──
    # Pairs unmatched BUYs with exit SELLs. Same exit cascade as
    # the crypto closer: SL → trail → max_hold (hard TP off).
    scheduler.add_job(s._run_tier3_paper_closer, 'interval', minutes=15,
                      id='tier3_paper_closer', replace_existing=True)
    # ── Crypto bot (24/7 lane, isolated from equity ml_paper_trader) ──
    scheduler.add_job(s._run_crypto_paper_bot, 'interval', minutes=15,
                      id='crypto_paper_bot', replace_existing=True)
    # ── Crypto closer (own service, 15-min tick, 12h hold window) ──
    scheduler.add_job(s._run_crypto_paper_closer, 'interval', minutes=15,
                      id='crypto_paper_trade_closer', replace_existing=True)
    # ── Crypto adaptation detector (closed-loop learning, 6-hourly) ──
    scheduler.add_job(s._run_crypto_adaptation_detector, 'interval', hours=6,
                      id='crypto_adaptation_detector', replace_existing=True)
    # ── Day-trade scanner (scan → rank → gate → queue top-1) ──
    # Strict discipline: never execute while scanning. ALWAYS scan
    # all symbols first, THEN rank, THEN decide. Two parallel lanes
    # (equity + crypto) at 5-min cadence. Scanner writes only to
    # ``day_trade_targets`` + ``day_trade_scan_log`` — no live
    # trade mutation. Gated by ``DAY_TRADE_SCANNER_ENABLED``.
    scheduler.add_job(
        s._run_day_trade_scanner_equity, 'interval', minutes=5,
        id='day_trade_scanner_equity', replace_existing=True,
    )
    scheduler.add_job(
        s._run_day_trade_scanner_crypto, 'interval', minutes=5,
        id='day_trade_scanner_crypto', replace_existing=True,
    )
    # ── Day-trade EOD exit monitor (closes max_hold_until breaches) ──
    scheduler.add_job(
        s._run_day_trade_exit_monitor, 'interval', minutes=5,
        id='day_trade_exit_monitor', replace_existing=True,
    )
    # ── Cross-asset stress monitor — checks Spread Watch every
    # minute. Fires stress_events row + (optionally) auto-flattens
    # when ≥ STRESS_SYMBOL_THRESHOLD symbols are stressed. Per-event
    # cooldown lives on the row so process restarts respect it. ──
    scheduler.add_job(
        s._run_stress_event_monitor, 'interval', minutes=1,
        id='stress_event_monitor', replace_existing=True,
    )
    # ── Tier-3 slippage advisor — weekly segmentation pass.
    # Mondays at 13:15 UTC (just before US RTH) so the proposals
    # land in time for the operator to flip env knobs before the
    # session opens. Advisory only — never applies env changes. ──
    scheduler.add_job(
        s._run_tier3_slippage_advisor, 'cron',
        day_of_week='mon', hour=13, minute=15,
        id='tier3_slippage_advisor', replace_existing=True,
    )
    # ── Research Shadow scorer (Tier-3 safe; writes only to
    # research_shadow_decisions; deferred counterfactual scoring) ──
    scheduler.add_job(s._run_research_shadow_scorer, 'interval', seconds=60,
                      id='research_shadow_scorer', replace_existing=True)
    # ── Autonomous trading agents (all narrate into agent_activity) ──
    # Trading agents — staggered so they don't hammer yfinance
    # simultaneously. Mean-rev runs most often; earnings only
    # needs once daily pre-market.
    scheduler.add_job(s._run_agent_mean_reversion, 'interval', minutes=15,
                      id='agent_mean_reversion')
    scheduler.add_job(s._run_agent_options_paper, 'interval', minutes=30,
                      id='agent_options_paper')
    scheduler.add_job(s._run_agent_earnings_watchdog, 'cron',
                      hour=8, minute=30, id='agent_earnings_watchdog')
    # Observer agents — daily rollups
    scheduler.add_job(s._run_agent_regime_drift, 'cron',
                      hour=8, minute=45, id='agent_regime_drift')
    scheduler.add_job(s._run_agent_performance_monitor, 'cron',
                      hour=8, minute=50, id='agent_performance_monitor')
    # ── Patent Watch (USPTO daily fetch) ──
    scheduler.add_job(s._run_patent_watch_refresh, 'cron',
                      hour=4, minute=15, id='patent_watch_refresh',
                      replace_existing=True)
    # ── Ops alerter (wedge detector — every 15 minutes) ──
    # No-op unless OPS_ALERT_WEBHOOK_URL is set, so safe to
    # always schedule.
    scheduler.add_job(s._run_ops_alerter_tick, 'interval',
                      minutes=15, id='ops_alerter_tick',
                      replace_existing=True)
    # ── ML heartbeat wedge alerter (every 5 min) ──
    # Notification-only. Posts to OPS_ALERT_WEBHOOK_URL when a
    # lane stays frozen >30 min OR feature-health-low repeats
    # >50× in 1h. NEVER promotes, NEVER calls a broker. Safe to
    # schedule even without the webhook env (logs once and
    # returns).
    scheduler.add_job(s._run_wedge_alerter_tick, 'interval',
                      minutes=5, id='wedge_alerter_tick',
                      replace_existing=True)
    # ── AI Core nightly sweep (02:45 UTC) ──
    # Runs after memory cleanup (02:00) and ML retrain (02:30) so
    # any newly-graded predictions / closed paper trades are
    # already in place. Dedup-safe — re-runs on the same day are
    # idempotent thanks to the unique alert id.
    scheduler.add_job(s._run_ai_core_nightly, 'cron',
                      hour=2, minute=45, id='ai_core_nightly',
                      replace_existing=True)
    # ── Position reconciler (Step 10 OUTCOME_VERIFIED for
    # external broker fills — equity + options, every 30 min) ──
    # No-op when no rows have proof_chain_entity_id pending; the
    # initial query is index-friendly and bounded at 200 rows.
    scheduler.add_job(s._run_position_reconciler, 'interval',
                      minutes=30, id='position_reconciler',
                      replace_existing=True)
    # ── Mongo→Chroma drift alert watcher (every 5 min) ──
    # Same compute path as GET /api/admin/memory/drift. Emits
    # to ``ai_core_alerts`` collection only on threshold
    # crossings / jumps > 5pts / recovery — dedup-bucketed by
    # UTC day, so this is *not* a spam source.
    scheduler.add_job(s._run_drift_alert_watcher, 'interval',
                      minutes=5, id='drift_alert_watcher',
                      replace_existing=True)
    # ── ETL framework jobs (one scheduler entry per registered
    # subclass of BaseETLJob; pulled from the in-process registry).
    # Each job owns its own unique composite + TTL index; rows
    # expire at retention_days via Mongo server-side TTL. The
    # scheduler entry per job uses the job's declared ``cadence``
    # dict as cron kwargs, so e.g. ``{"day_of_week": "mon",
    # "hour": 4}`` runs Mondays at 4 UTC. Disabled jobs are
    # skipped at scheduling time.
    try:
        from services.etl_registry import all_jobs as _all_etl_jobs
        for _etl_job in _all_etl_jobs():
            if not _etl_job.enabled:
                continue
            scheduler.add_job(
                s._run_etl_job,
                'cron',
                args=[_etl_job.source_name],
                id=f'etl_{_etl_job.source_name}',
                replace_existing=True,
                **_etl_job.cadence,
            )
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"ETL scheduler setup failed: {_e}")

    # ── Top-universe tiered pre-warm (user-requested Phase 1) ──
    # Three jobs, all non-critical — a failure here never blocks
    # anything downstream because they only *seed* caches that
    # services already check-through-and-fall-back.
    #   - rebuild:       Sunday 00:00 UTC (~500 OVERVIEW calls, weekly)
    #   - warm post-close: 21:05 UTC daily (Tier A full, Tier B light)
    #   - warm pre-open:   13:00 UTC daily (Tier A quote + technicals)
    async def _run_universe_rebuild():
        try:
            from services.top_universe_service import rebuild_universe
            await rebuild_universe(db)
        except Exception:
            logging.exception("top_universe_rebuild failed (non-critical)")

    async def _run_universe_warm_post_close():
        try:
            from services.top_universe_service import warm_universe
            await warm_universe(db, run_type="post_close")
        except Exception:
            logging.exception("top_universe_warm_post_close failed (non-critical)")

    async def _run_universe_warm_pre_open():
        try:
            from services.top_universe_service import warm_universe
            await warm_universe(db, run_type="pre_open")
        except Exception:
            logging.exception("top_universe_warm_pre_open failed (non-critical)")

    scheduler.add_job(
        _run_universe_rebuild,
        'cron', day_of_week='sun', hour=0, minute=0,
        id='top_universe_rebuild', replace_existing=True,
    )
    scheduler.add_job(
        _run_universe_warm_post_close,
        'cron', hour=21, minute=5,
        id='top_universe_warm_post_close', replace_existing=True,
    )
    scheduler.add_job(
        _run_universe_warm_pre_open,
        'cron', hour=13, minute=0,
        id='top_universe_warm_pre_open', replace_existing=True,
    )

    # Options universe warm — fires every 5 min. The service itself
    # is market-hours-gated (13:30–21:00 UTC Mon–Fri); off-hours calls
    # write a "skipped" stats row and exit fast, so the 5-min cadence
    # keeps the snapshot fresh during session without burning quota
    # on static overnight data.
    async def _run_options_universe_warm():
        try:
            from services.options_universe_service import warm_options_universe
            await warm_options_universe(db)
        except Exception:
            logging.exception("options_universe_warm failed (non-critical)")

    scheduler.add_job(
        _run_options_universe_warm,
        'interval', minutes=5,
        id='options_universe_warm', replace_existing=True,
    )

    # Dedicated scheduler heartbeat — writes every 60s to a tiny
    # ``scheduler_heartbeat`` document. Replaces the old "infer
    # heartbeat from arbitrary other writes" proxy which would
    # show false-stale on quiet pods (e.g. when no paper trades
    # opened in the last day, the proxy reported the scheduler as
    # dead even though it was firing every minute).
    async def _write_scheduler_heartbeat():
        try:
            # Module-level ``from datetime import datetime, timezone``
            # already covers this scope. No inner re-import (see
            # 2026-05-06 incident — function-local datetime imports
            # in the parent scope took the scheduler down for 3
            # days). Defensive consistency: do not re-introduce.
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

    scheduler.add_job(
        _write_scheduler_heartbeat,
        'interval', seconds=60,
        id='scheduler_heartbeat', replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # write one immediately
    )
