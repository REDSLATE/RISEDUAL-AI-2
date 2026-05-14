"""Route & DB Registry — centralizes all route imports and database wiring.

This module is imported once by server.py to keep the main entry point clean.
"""
import logging

from typing import Any  # noqa: F401  (re-exported for type hints)

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ── Route imports ──
# Note: `seed_admin` and `create_indexes` are re-exported here and imported by
# server.py — do NOT remove them, even if this file doesn't use them directly.
from routes.auth import auth_router, set_db as set_auth_db, seed_admin, create_indexes  # noqa: F401
from routes.market import router as market_router
from routes.trading import router as trading_router
from routes.ai import router as ai_router, set_db as set_ai_db
from routes.workspace import router as workspace_router, set_db as set_workspace_db
from routes.subscription import router as subscription_router, set_db as set_subscription_db
from routes.referral import router as referral_router, set_db as set_referral_db
from routes.promo import router as promo_router, set_db as set_promo_db
from routes.digest import router as digest_router, set_db as set_digest_db
from routes.push import router as push_router, set_db as set_push_db
from routes.journal import router as journal_router, set_db as set_journal_db
from routes.strategy import router as strategy_router, set_db as set_strategy_db
from routes.intelligence import router as intelligence_router, set_db as set_intelligence_db
from routes.broker import router as broker_router, set_db as set_broker_db
from routes.market_data import router as market_data_router, set_db as set_market_data_db
from routes.sectors import router as sectors_router, set_db as set_sectors_db
from routes.admin import router as admin_router, set_db as set_admin_db
from routes.terminal import router as terminal_router, set_db as set_terminal_db
from routes.accuracy import router as accuracy_router, set_db as set_accuracy_db
from routes.stream import router as stream_router, set_db as set_stream_db
from routes.orderflow_stream import router as orderflow_stream_router
from routes.whale_radar import router as whale_radar_router
from routes.paper_trading import router as paper_trading_router, set_db as set_paper_trading_db
from routes.media import router as media_router
from routes.security_audit import router as security_audit_router, set_db as set_security_audit_db
from routes.waitlist import router as waitlist_router
from routes.smart_orders import router as smart_orders_router, set_db as set_smart_orders_db
from routes.risk_calculator import router as risk_calc_router, set_db as set_risk_calc_db
from routes.scanner import router as scanner_router, set_db as set_scanner_db
from routes.trading_bots import router as trading_bots_router, set_db as set_trading_bots_db
from routes.public_api import router as public_api_router, key_router as dev_key_router, set_db as set_public_api_db
from routes.credits import router as credits_router, set_db as set_credits_db
from routes.failure_loop import router as failure_loop_router, set_db as set_failure_loop_db
from routes.billing import router as billing_router, set_db as set_billing_db
from routes.web_intelligence import router as web_intel_router, set_db as set_web_intel_db
from routes.success_fee import router as success_fee_router, set_db as set_success_fee_db
from routes.provider_health import router as provider_health_router, set_db as set_provider_health_db
from routes.headlines import router as headlines_router, set_db as set_headlines_db
from routes.vault import router as vault_router, set_db as set_vault_db
from routes.signal import router as signal_router, set_db as set_signal_db
from routes.ml_orchestrator import router as ml_router, set_db as set_ml_db
from routes.stockfit import router as stockfit_router, set_db as set_stockfit_db
from routes.stockfit_13f import router as stockfit_13f_router, set_db as set_stockfit_13f_db
from routes.analytics import router as analytics_router, set_db as set_analytics_db
from routes.fred import router as fred_router, set_db as set_fred_db
from routes.demo import router as demo_router, set_db as set_demo_db
from routes.self_test import router as self_test_router, set_db as set_self_test_db, set_scheduler as set_self_test_scheduler  # noqa: F401
from routes.rejections import router as rejections_router
from routes.share import router as share_router
from routes.share_image import router as share_image_router
from routes.options_trading import router as options_trading_router, set_db as set_options_trading_db
from routes.beta import router as beta_router, set_db as set_beta_db
from routes.agent import router as agent_router
from routes.crypto_paper import router as crypto_paper_router, set_db as set_crypto_paper_db
from routes.crypto_trading import router as crypto_trading_router, set_db as set_crypto_trading_db
from routes.research_shadow import router as research_shadow_router, set_db as set_research_shadow_db
from routes.patent_watch import router as patent_watch_router, set_db as set_patent_watch_db
from routes.ops_snapshot import router as ops_snapshot_router, set_db as set_ops_snapshot_db
from routes.toxic_autopsy import router as toxic_autopsy_router, set_db as set_toxic_autopsy_db
from routes.ai_core_routes import router as ai_core_routes_router, set_db as set_ai_core_routes_db
from routes.promotion_bridge_routes import router as promotion_bridge_routes_router
from routes.whatif_replay_routes import router as whatif_replay_routes_router, set_db as set_whatif_replay_db
from routes.trading_mode import router as trading_mode_router, set_db as set_trading_mode_db
from routes.admin_proof_chain import router as admin_proof_chain_router, set_db as set_admin_proof_chain_db
from routes.admin_guard_shadow import router as admin_guard_shadow_router, set_db as set_admin_guard_shadow_db
from routes.admin_blocks_prevented import router as admin_blocks_prevented_router, set_db as set_admin_blocks_prevented_db
from routes.admin_bulk_replay import router as admin_bulk_replay_router
from services.code_evolution.api import router as code_evolution_router, set_db as set_code_evolution_db
from services.python_coach.api import router as python_coach_router
from services.alpha_knowledge.api import router as alpha_knowledge_router, set_db as set_alpha_knowledge_db
from services.operator_trading_gate_api import router as trading_gate_router, set_db as set_trading_gate_db
from services.shelly_memory_api import router as shelly_memory_router, set_db as set_shelly_memory_db
from routes.admin_position_reconciler import router as admin_position_reconciler_router, set_db as set_admin_position_reconciler_db
from routes.admin_memory_drift import router as admin_memory_drift_router, set_db as set_admin_memory_drift_db
from routes.admin_etl import router as admin_etl_router, set_db as set_admin_etl_db
from routes.sovereign_ai import router as sovereign_ai_router, set_db as set_sovereign_ai_db
from routes.regime_memory import router as regime_memory_router, set_db as set_regime_memory_db
from routes.admin_conviction import router as admin_conviction_router, set_db as set_admin_conviction_db
from routes.admin_data_integrity import router as admin_data_integrity_router, set_db as set_admin_data_integrity_db
from routes.admin_adaptations import router as admin_adaptations_router, set_db as set_admin_adaptations_db
from routes.admin_compression_gate import router as admin_compression_gate_router, set_db as set_admin_compression_gate_db
from routes.admin_news import router as admin_news_router, set_db as set_admin_news_db
from routes.admin_tier3_bootstrap import router as admin_tier3_bootstrap_router, set_db as set_admin_tier3_bootstrap_db
from routes.admin_promotion_gates import router as admin_promotion_gates_router, set_db as set_admin_promotion_gates_db
from routes.intelligence_council import router as intelligence_council_router
from routes.hypothesis_stream import router as hypothesis_stream_router
from routes.admin_ticker_abandonment import router as admin_ticker_abandonment_router, set_db as set_admin_ticker_abandonment_db
from routes.admin_day_trade import router as admin_day_trade_router, set_db as set_admin_day_trade_db
from routes.admin_spread_slippage import router as admin_spread_slippage_router, set_db as set_admin_spread_slippage_db
from routes.admin_autopsy_promotion import router as admin_autopsy_promotion_router, set_db as set_admin_autopsy_promotion_db
from routes.admin_realtime_infra import router as admin_realtime_infra_router, set_db as set_admin_realtime_infra_db
from routes.admin_notification_lifecycle import router as admin_notification_lifecycle_router, set_db as set_admin_notification_lifecycle_db
from routes.admin_introspection import router as admin_introspection_router, set_db as set_admin_introspection_db
from routes.admin_learning_core import router as admin_learning_core_router, set_db as set_admin_learning_core_db
from routes.admin_fast_veto import router as admin_fast_veto_router, set_db as set_admin_fast_veto_db
from routes.admin_roadguard import router as admin_roadguard_router, set_db as set_admin_roadguard_db
from routes.admin_ml_v2 import (
    router as admin_ml_v2_router,
    ml_safety_router as admin_ml_safety_router,
    set_db as set_admin_ml_v2_db,
)
from services.natural_language_trading import router as nl_trading_router, set_db as set_nl_trading_db
# Side-effect import: registers all ``BaseETLJob`` subclasses with
# the ETL framework registry. Must run before
# ``services.etl_registry.all_jobs()`` is consulted at startup.
import services.etl_jobs  # noqa: F401
from services.firewall import set_db as set_firewall_db
from services.dtd_replay_channel import set_db as set_dtd_replay_db
from services.role_scoped_db import set_db as set_role_scoped_db
from services.promotion_bridge import set_db as set_promotion_bridge_db
from services.agent_activity_service import set_db as set_agent_activity_db
from services.price_provider import set_db as set_price_provider_db
from services.market_data_pool import set_db as set_market_data_pool_db
from services.auth_helpers import set_db as set_auth_helpers_db
from services.usaspending_service import set_db as set_usaspending_db

# Ordered list of all routers to register
# crypto_paper_router + crypto_trading_router go FIRST so their
# specific GETs (/api/crypto/paper-trades, /api/crypto/paper-positions,
# /api/crypto/paper-bot/run) match before market_router's wildcard
# /api/crypto/{symbol}.
ALL_ROUTERS = [
    crypto_paper_router,
    crypto_trading_router,
    auth_router, market_router, trading_router, ai_router, workspace_router,
    subscription_router, referral_router, promo_router, digest_router, push_router,
    journal_router, strategy_router, intelligence_router, broker_router,
    market_data_router, sectors_router, admin_router, terminal_router, accuracy_router,
    stream_router, orderflow_stream_router, whale_radar_router,
    paper_trading_router, media_router, security_audit_router, waitlist_router,
    smart_orders_router,
    risk_calc_router,
    scanner_router,
    trading_bots_router,
    success_fee_router,
    public_api_router,
    dev_key_router,
    credits_router,
    failure_loop_router,
    billing_router,
    web_intel_router,
    provider_health_router,
    headlines_router,
    vault_router,
    signal_router,
    ml_router,
    stockfit_router,
    stockfit_13f_router,
    analytics_router,
    fred_router,
    demo_router,
    self_test_router,
    rejections_router,
    share_router,
    share_image_router,
    options_trading_router,
    beta_router,
    agent_router,
    research_shadow_router,
    patent_watch_router,
    ops_snapshot_router,
    toxic_autopsy_router,
    ai_core_routes_router,
    promotion_bridge_routes_router,
    whatif_replay_routes_router,
    trading_mode_router,
    admin_proof_chain_router,
    admin_guard_shadow_router,
    admin_blocks_prevented_router,
    admin_bulk_replay_router,
    code_evolution_router,
    python_coach_router,
    alpha_knowledge_router,
    trading_gate_router,
    shelly_memory_router,
    admin_position_reconciler_router,
    admin_memory_drift_router,
    admin_etl_router,
    sovereign_ai_router,
    regime_memory_router,
    admin_conviction_router,
    admin_data_integrity_router,
    admin_adaptations_router,
    admin_compression_gate_router,
    admin_news_router,
    admin_tier3_bootstrap_router,
    admin_promotion_gates_router,
    admin_ticker_abandonment_router,
    admin_day_trade_router,
    admin_spread_slippage_router,
    admin_autopsy_promotion_router,
    admin_realtime_infra_router,
    admin_notification_lifecycle_router,
    admin_introspection_router,
    admin_learning_core_router,
    admin_fast_veto_router,
    admin_roadguard_router,
    admin_ml_v2_router,
    admin_ml_safety_router,
    nl_trading_router,
    intelligence_council_router,
    hypothesis_stream_router,
]


def register_all_routers(app: FastAPI) -> None:
    """Register all route modules with the FastAPI app, isolating failures."""
    for router in ALL_ROUTERS:
        try:
            app.include_router(router)
        except Exception as e:
            logger.error(f"Failed to register router {getattr(router, 'prefix', '?')}: {e}")


def wire_db(db: AsyncIOMotorDatabase) -> None:
    """Pass the database reference to all route and service modules."""
    _setters = [
        set_auth_helpers_db, set_auth_db, set_ai_db, set_workspace_db,
        set_subscription_db, set_referral_db, set_promo_db, set_digest_db,
        set_push_db, set_journal_db, set_strategy_db, set_intelligence_db,
        set_broker_db, set_market_data_db, set_admin_db, set_terminal_db, set_accuracy_db,
        set_stream_db, set_price_provider_db, set_market_data_pool_db, set_paper_trading_db,
        set_options_trading_db,
        set_sectors_db, set_security_audit_db, set_smart_orders_db,
        set_risk_calc_db, set_scanner_db, set_trading_bots_db,
        set_success_fee_db, set_public_api_db, set_credits_db,
        set_failure_loop_db, set_billing_db, set_web_intel_db,
        set_provider_health_db,
        set_headlines_db,
        set_vault_db,
        set_signal_db,
        set_ml_db,
        set_stockfit_db,
        set_stockfit_13f_db,
        set_analytics_db,
        set_fred_db,
        set_demo_db,
        set_self_test_db,
        set_usaspending_db,
        set_beta_db,
        set_agent_activity_db,
        set_crypto_paper_db,
        set_crypto_trading_db,
        set_research_shadow_db,
        set_patent_watch_db,
        set_ops_snapshot_db,
        set_toxic_autopsy_db,
        set_ai_core_routes_db,
        set_firewall_db,
        set_dtd_replay_db,
        set_role_scoped_db,
        set_promotion_bridge_db,
        set_whatif_replay_db,
        set_trading_mode_db,
        set_admin_proof_chain_db,
        set_admin_guard_shadow_db,
        set_admin_blocks_prevented_db,
        set_admin_position_reconciler_db,
        set_admin_memory_drift_db,
        set_admin_etl_db,
        set_sovereign_ai_db,
        set_regime_memory_db,
        set_admin_conviction_db,
        set_admin_data_integrity_db,
        set_admin_adaptations_db,
        set_admin_compression_gate_db,
        set_admin_news_db,
        set_admin_tier3_bootstrap_db,
        set_admin_promotion_gates_db,
        set_admin_ticker_abandonment_db,
        set_admin_day_trade_db,
        set_admin_spread_slippage_db,
        set_admin_autopsy_promotion_db,
        set_admin_realtime_infra_db,
        set_admin_notification_lifecycle_db,
        set_admin_introspection_db,
        set_admin_learning_core_db,
        set_admin_fast_veto_db,
        set_admin_roadguard_db,
        set_admin_ml_v2_db,
        set_nl_trading_db,
        set_code_evolution_db,
        set_alpha_knowledge_db,
        set_trading_gate_db,
        set_shelly_memory_db,
    ]
    # Module-level db handle for the per-patent policy store so the
    # IP contract can read overrides without an explicit db arg.
    try:
        from services.guard_policy_store import set_db as _set_policy_db
        _set_policy_db(db)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"guard_policy_store db wire failed: {e}")
    for setter in _setters:
        try:
            setter(db)
        except Exception as e:
            logger.error(f"DB wire failed for {setter.__module__}.{setter.__name__}: {e}")

    # Crypto paper-trading idempotency + query indexes (best-effort).
    try:
        from services.crypto_paper_trading_service import ensure_indexes as _crypto_paper_indexes
        from services.crypto_signal_audit import ensure_indexes as _crypto_audit_indexes
        from services.research_shadow_logger import ensure_indexes as _shadow_indexes
        from services.patent_watch_service import ensure_indexes as _patent_watch_indexes
        from services.ml_paper_trader import ensure_indexes as _ml_paper_indexes
        from services.ai_core_engine import ensure_indexes as _ai_core_engine_indexes
        from services.ai_core_alerts import ensure_indexes as _ai_core_alerts_indexes
        from services.ai_cache_service import AICacheService as _AICacheService
        from services.mongo_chroma_sync_metrics import (
            ensure_history_indexes as _drift_history_indexes,
        )
        from services.etl_registry import (
            ensure_audit_indexes as _etl_audit_indexes,
            all_jobs as _all_etl_jobs,
        )
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_crypto_paper_indexes())
            loop.create_task(_crypto_audit_indexes(db))
            loop.create_task(_shadow_indexes(db))
            loop.create_task(_patent_watch_indexes())
            loop.create_task(_ml_paper_indexes(db))
            loop.create_task(_ai_core_engine_indexes(db))
            loop.create_task(_ai_core_alerts_indexes())
            loop.create_task(_AICacheService.ensure_indexes(db))
            loop.create_task(_drift_history_indexes())
            loop.create_task(_etl_audit_indexes(db))
            # Sovereign AI — index the sovereign_decisions collection
            try:
                from services.sovereign_ai_core import ensure_sovereign_indexes
                loop.create_task(ensure_sovereign_indexes(db))
            except Exception:  # noqa: BLE001
                pass
            # Each registered ETL job ensures its own (unique + TTL)
            # indexes — late registration is supported because this
            # runs at startup AFTER all modules have been imported.
            for _job in _all_etl_jobs():
                loop.create_task(_job.ensure_indexes(db))
        except RuntimeError:
            pass  # no running loop during sync init — indexes get created on first write anyway
    except Exception as e:
        logger.warning(f"Crypto paper indexes wire failed: {e}")

    try:
        from services.orderflow_ws_service import stream_manager
        stream_manager.set_db(db)
    except Exception as e:
        logger.warning(f"Orderflow WS DB wire failed: {e}")

    try:
        from services.chat_memory_service import set_db as set_chat_memory_db
        set_chat_memory_db(db)
    except Exception as e:
        logger.warning(f"Chat memory DB wire failed: {e}")

    # Patent I — risk budget gateway DB + indexes.
    try:
        from services.risk_budget_gateway import (
            set_db as set_risk_budget_db,
            ensure_indexes as _risk_budget_indexes,
        )
        set_risk_budget_db(db)
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_risk_budget_indexes())
        except RuntimeError:
            pass
    except Exception as e:
        logger.warning(f"Patent-I risk budget gateway wire failed: {e}")

    # Patent J — proof chain indexes for decision_proof_chain.
    try:
        from services.proof_chain import ensure_indexes as _proof_chain_indexes
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_proof_chain_indexes(db))
        except RuntimeError:
            pass
    except Exception as e:
        logger.warning(f"Patent-J proof chain indexes wire failed: {e}")

    # Decision Pipeline Guard — shadow mode log indexes.
    try:
        from services.guard_shadow_log import ensure_indexes as _guard_shadow_indexes
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_guard_shadow_indexes(db))
        except RuntimeError:
            pass
    except Exception as e:
        logger.warning(f"Guard shadow log indexes wire failed: {e}")

    # Patent M — equity telemetry baselines.
    try:
        from services.equity_telemetry import (
            set_db as set_equity_telemetry_db,
            ensure_indexes as _equity_telemetry_indexes,
        )
        set_equity_telemetry_db(db)
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_equity_telemetry_indexes())
        except RuntimeError:
            pass
    except Exception as e:
        logger.warning(f"Patent-M equity telemetry wire failed: {e}")

    try:
        from services.waitlist_service import set_db as set_waitlist_db
        set_waitlist_db(db)
    except Exception as e:
        logger.warning(f"Waitlist DB wire failed: {e}")

    # Initialize Market Memory (ChromaDB vector store)
    try:
        from services.market_memory_service import init_memory
        init_memory(db)
    except Exception as e:
        logger.warning(f"Market Memory init failed: {e}")

    # Wire rejection-log service (captures hard-negative training data).
    # Indexes are set up asynchronously at first use to avoid blocking
    # startup on Mongo; log_rejected() is a no-op until set_db runs.
    try:
        from services.rejection_log import set_db as set_rejection_db, ensure_indexes as _rejection_indexes
        import asyncio as _asyncio
        set_rejection_db(db)
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_rejection_indexes())
        except RuntimeError:
            pass  # no running loop during sync init — indexes get created on first write anyway
    except Exception as e:
        logger.warning(f"Rejection log DB wire failed: {e}")

    # Phase 5a — alpha_decision_log + daily_mandate indexes.
    try:
        from services import alpha_decision_log as _adl, alpha_daily_mandate as _adm
        from services.ml import broker_wire as _bw
        from services.ml import camaro_shelly_bridge as _csb
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_adl.ensure_indexes(db))
            loop.create_task(_adm.ensure_indexes(db))
            loop.create_task(_bw.ensure_indexes(db))
            loop.create_task(_csb.ensure_indexes(db))
        except RuntimeError:
            pass
    except Exception as e:
        logger.warning(f"Phase 5a alpha indexes wire failed: {e}")

    # Initialize Object Storage
    try:
        from services.storage_service import init_storage
        init_storage()
    except Exception as e:
        logger.warning(f"Object storage init failed (non-critical): {e}")
