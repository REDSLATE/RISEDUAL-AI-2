"""Route & DB Registry — centralizes all route imports and database wiring.

This module is imported once by server.py to keep the main entry point clean.
"""
import logging
from typing import Any

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ── Route imports ──
from routes.auth import auth_router, set_db as set_auth_db, seed_admin, create_indexes
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
from services.price_provider import set_db as set_price_provider_db
from services.market_data_pool import set_db as set_market_data_pool_db
from services.auth_helpers import set_db as set_auth_helpers_db

# Ordered list of all routers to register
ALL_ROUTERS = [
    auth_router, market_router, trading_router, ai_router, workspace_router,
    subscription_router, referral_router, promo_router, digest_router, push_router,
    journal_router, strategy_router, intelligence_router, broker_router,
    market_data_router, sectors_router, admin_router, accuracy_router,
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
        set_broker_db, set_market_data_db, set_admin_db, set_accuracy_db,
        set_stream_db, set_price_provider_db, set_market_data_pool_db, set_paper_trading_db,
        set_sectors_db, set_security_audit_db, set_smart_orders_db,
        set_risk_calc_db, set_scanner_db, set_trading_bots_db,
        set_success_fee_db, set_public_api_db, set_credits_db,
        set_failure_loop_db, set_billing_db, set_web_intel_db,
        set_provider_health_db,
        set_headlines_db,
        set_vault_db,
    ]
    for setter in _setters:
        try:
            setter(db)
        except Exception as e:
            logger.error(f"DB wire failed for {setter.__module__}.{setter.__name__}: {e}")

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

    # Initialize Object Storage
    try:
        from services.storage_service import init_storage
        init_storage()
    except Exception as e:
        logger.warning(f"Object storage init failed (non-critical): {e}")
