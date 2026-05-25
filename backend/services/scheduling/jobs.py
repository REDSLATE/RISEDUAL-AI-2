"""APScheduler job registrations.

Strangler-split out of ``server._start_schedulers`` (2026-05-08).

Behaviour-preservation contract
-------------------------------
``register_all`` is a verbatim move of every ``scheduler.add_job(...)``
call that previously lived in-place inside ``server._start_schedulers``.
**Every job ID, every cron expression, every interval, every
replace_existing flag, every ``next_run_time`` kwarg is identical
to before the split.**

Module-level callbacks (``_check_smart_orders``, ``_run_grid_bots``,
``_run_etl_job``, ...) are reached via the ``server_mod`` parameter
to avoid a circular import. Inline closures that previously closed
over the enclosing function's ``db`` now live in
``services/scheduling/_callbacks.py`` as top-level ``async def``
functions and are wired with ``args=[db]`` — same runtime behaviour.
"""
import logging
from datetime import datetime, timezone

from . import _callbacks as cb

logger = logging.getLogger(__name__)


def register_all(scheduler, db, server_mod):
    """Register every APScheduler job onto ``scheduler``.

    Args:
        scheduler: a started-or-not AsyncIOScheduler instance.
        db: the live Motor database handle.
        server_mod: the ``server`` module — used for module-level
            callback lookups (``server_mod._check_smart_orders``,
            ``server_mod._run_grid_bots``, etc.). Passed in instead
            of imported here to avoid a circular import.

    Side effects:
        Mutates ``scheduler`` by adding jobs. Does NOT call
        ``scheduler.start()`` — the caller (server._start_schedulers)
        is responsible for that, plus self-test wiring + error pump.
    """
    s = server_mod  # short alias

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

    # ── Chevelle calibration refit (Option A: IsotonicRegression) ──
    # Daily-cadence post-hoc isotonic mapping fit on
    # firewall-trainable rows. Never blocks; failure → next-day retry.
    from routes.governance_chevelle_calibration import (
        run_calibration_refit_job,
    )
    scheduler.add_job(
        run_calibration_refit_job,
        'cron', hour=4, minute=15, args=[db],
        id='chevelle_calibration_refit_daily',
    )
    scheduler.add_job(s._run_help_search_digest, 'cron', day_of_week='mon', hour=7, minute=0, id='help_search_weekly_digest')
    scheduler.add_job(s._run_usaspending_warmup, 'cron', hour=3, minute=30, id='usaspending_warmup')
    scheduler.add_job(s._run_nightly_ml_retrain, 'cron', hour=2, minute=30, id='nightly_ml_retrain')
    scheduler.add_job(s._run_self_test_monitor, 'interval', minutes=15, id='self_test_monitor')
    scheduler.add_job(s._run_conviction_drift_check, 'cron', hour=8, minute=0, id='conviction_drift_check')

    # Inline-callback jobs (rationale lives in _callbacks.py).
    scheduler.add_job(cb.run_ticker_abandonment_snapshot, 'cron', hour=21, minute=5, args=[db], id='ticker_abandonment_snapshot')
    scheduler.add_job(cb.run_nightly_integrity_audit, 'cron', hour=3, minute=15, args=[db], id='nightly_integrity_audit')
    scheduler.add_job(cb.run_integrity_alert_evaluator, 'interval', minutes=15, args=[db], id='integrity_alert_evaluator')
    scheduler.add_job(cb.run_integrity_mitigation_sweep, 'interval', minutes=5, args=[db], id='integrity_mitigation_sweep')
    scheduler.add_job(cb.run_notification_lifecycle_sweep, 'cron', hour='4,16', minute=0, args=[db], id='notification_lifecycle_sweep')
    scheduler.add_job(cb.run_news_feeders_tick, 'interval', minutes=15, args=[db], id='news_feeders_tick')
    scheduler.add_job(cb.run_kraken_shadow_compare, 'interval', minutes=5, args=[db], id='kraken_shadow_compare')
    scheduler.add_job(cb.run_sovereign_resolution_tick, 'interval', minutes=15, args=[db], id='sovereign_resolution_tick')
    scheduler.add_job(cb.run_fear_greed_refresh, 'cron', hour=4, minute=30, args=[db], id='fear_greed_refresh')

    scheduler.add_job(s._run_tier3_readiness_digest, 'cron', hour=8, minute=15, id='tier3_readiness_digest')
    scheduler.add_job(s._run_ml_health_digest, 'cron', hour=8, minute=0, id='ml_health_digest')
    scheduler.add_job(s._run_paper_trade_closer, 'interval', minutes=60, id='paper_trade_closer')
    scheduler.add_job(s._run_tier3_paper_closer, 'interval', minutes=15, id='tier3_paper_closer', replace_existing=True)
    scheduler.add_job(s._run_crypto_paper_bot, 'interval', minutes=15, id='crypto_paper_bot', replace_existing=True)
    scheduler.add_job(s._run_crypto_paper_closer, 'interval', minutes=15, id='crypto_paper_trade_closer', replace_existing=True)
    scheduler.add_job(s._run_crypto_adaptation_detector, 'interval', hours=6, id='crypto_adaptation_detector', replace_existing=True)
    scheduler.add_job(s._run_day_trade_scanner_equity, 'interval', minutes=5, id='day_trade_scanner_equity', replace_existing=True)
    scheduler.add_job(s._run_day_trade_scanner_crypto, 'interval', minutes=5, id='day_trade_scanner_crypto', replace_existing=True)
    scheduler.add_job(s._run_day_trade_exit_monitor, 'interval', minutes=5, id='day_trade_exit_monitor', replace_existing=True)
    scheduler.add_job(s._run_stress_event_monitor, 'interval', minutes=1, id='stress_event_monitor', replace_existing=True)
    scheduler.add_job(s._run_tier3_slippage_advisor, 'cron', day_of_week='mon', hour=13, minute=15, id='tier3_slippage_advisor', replace_existing=True)
    scheduler.add_job(s._run_research_shadow_scorer, 'interval', seconds=60, id='research_shadow_scorer', replace_existing=True)
    scheduler.add_job(s._run_agent_mean_reversion, 'interval', minutes=15, id='agent_mean_reversion')
    scheduler.add_job(s._run_agent_options_paper, 'interval', minutes=30, id='agent_options_paper')
    scheduler.add_job(s._run_agent_earnings_watchdog, 'cron', hour=8, minute=30, id='agent_earnings_watchdog')
    scheduler.add_job(s._run_agent_regime_drift, 'cron', hour=8, minute=45, id='agent_regime_drift')
    scheduler.add_job(s._run_agent_performance_monitor, 'cron', hour=8, minute=50, id='agent_performance_monitor')
    scheduler.add_job(s._run_patent_watch_refresh, 'cron', hour=4, minute=15, id='patent_watch_refresh', replace_existing=True)
    scheduler.add_job(s._run_ops_alerter_tick, 'interval', minutes=15, id='ops_alerter_tick', replace_existing=True)
    scheduler.add_job(s._run_wedge_alerter_tick, 'interval', minutes=5, id='wedge_alerter_tick', replace_existing=True)
    scheduler.add_job(s._run_ai_core_nightly, 'cron', hour=2, minute=45, id='ai_core_nightly', replace_existing=True)
    scheduler.add_job(s._run_position_reconciler, 'interval', minutes=30, id='position_reconciler', replace_existing=True)
    # 2026-02-23: live Alpaca position closer — fills the gap that left
    # AMZN/GOOGL/MSFT/NVDA accumulating in the broker because the brain
    # had no Alpaca-side close path. Default OFF (master switch
    # ``ALPACA_POSITION_CLOSER_ENABLED``); when enabled defaults to
    # dry-run so the operator can sanity-check the exit tape before
    # going live. 5-minute cadence matches position_reconciler.
    scheduler.add_job(s._run_alpaca_position_closer, 'interval', minutes=5, id='alpaca_position_closer', replace_existing=True)
    scheduler.add_job(s._run_drift_alert_watcher, 'interval', minutes=5, id='drift_alert_watcher', replace_existing=True)

    # ETL framework jobs — one scheduler entry per registered subclass
    # of BaseETLJob; pulled from the in-process registry. Each job's
    # declared ``cadence`` dict feeds the cron kwargs verbatim.
    try:
        from services.etl_registry import all_jobs as _all_etl_jobs
        for _etl_job in _all_etl_jobs():
            if not _etl_job.enabled:
                continue
            scheduler.add_job(
                s._run_etl_job, 'cron', args=[_etl_job.source_name],
                id=f'etl_{_etl_job.source_name}', replace_existing=True,
                **_etl_job.cadence,
            )
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"ETL scheduler setup failed: {_e}")

    scheduler.add_job(cb.run_universe_rebuild, 'cron', day_of_week='sun', hour=0, minute=0, args=[db], id='top_universe_rebuild', replace_existing=True)
    scheduler.add_job(cb.run_universe_warm_post_close, 'cron', hour=21, minute=5, args=[db], id='top_universe_warm_post_close', replace_existing=True)
    scheduler.add_job(cb.run_universe_warm_pre_open, 'cron', hour=13, minute=0, args=[db], id='top_universe_warm_pre_open', replace_existing=True)
    scheduler.add_job(cb.run_options_universe_warm, 'interval', minutes=5, args=[db], id='options_universe_warm', replace_existing=True)
    scheduler.add_job(
        cb.write_scheduler_heartbeat, 'interval', seconds=60, args=[db],
        id='scheduler_heartbeat', replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # write one immediately
    )
