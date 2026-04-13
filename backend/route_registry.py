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
from routes.success_fee import router as success_fee_router, set_db as set_success_fee_db
from services.price_provider import set_db as set_price_provider_db
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
]


def register_all_routers(app: FastAPI) -> None:
    """Register all route modules with the FastAPI app."""
    for router in ALL_ROUTERS:
        app.include_router(router)


def wire_db(db: AsyncIOMotorDatabase) -> None:
    """Pass the database reference to all route and service modules."""
    set_auth_helpers_db(db)
    set_auth_db(db)
    set_ai_db(db)
    set_workspace_db(db)
    set_subscription_db(db)
    set_referral_db(db)
    set_promo_db(db)
    set_digest_db(db)
    set_push_db(db)
    set_journal_db(db)
    set_strategy_db(db)
    set_intelligence_db(db)
    set_broker_db(db)
    set_market_data_db(db)
    set_admin_db(db)
    set_accuracy_db(db)
    set_stream_db(db)
    set_price_provider_db(db)
    set_paper_trading_db(db)
    set_sectors_db(db)
    set_security_audit_db(db)
    set_smart_orders_db(db)
    set_risk_calc_db(db)
    set_scanner_db(db)
    set_trading_bots_db(db)
    set_success_fee_db(db)
    set_public_api_db(db)

    from services.orderflow_ws_service import stream_manager
    stream_manager.set_db(db)
    from services.chat_memory_service import set_db as set_chat_memory_db
    set_chat_memory_db(db)
    from services.waitlist_service import set_db as set_waitlist_db
    set_waitlist_db(db)

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
