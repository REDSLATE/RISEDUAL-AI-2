"""RISEDUAL AI — FastAPI Application Entry Point.

Thin orchestrator: connects MongoDB, registers route modules, handles startup/shutdown.
"""
from fastapi import FastAPI, APIRouter
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List
import uuid
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Route modules
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
from routes.sectors import router as sectors_router
from routes.admin import router as admin_router, set_db as set_admin_db
from routes.accuracy import router as accuracy_router, set_db as set_accuracy_db
from services.price_provider import set_db as set_price_provider_db
from services.auth_helpers import set_db as set_auth_helpers_db

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# FastAPI app
app = FastAPI()

# Minimal status-check router (kept in server.py for health checks)
api_router = APIRouter(prefix="/api")


class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/")
async def root():
    return {"message": "RISEDUAL AI API - Ready"}


@api_router.get("/download/codebase-pdf")
async def download_codebase_pdf():
    pdf_path = "/app/RISEDUAL_AI_Complete_Codebase.pdf"
    if os.path.exists(pdf_path):
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename="RISEDUAL_AI_Complete_Codebase.pdf"
        )
    return {"error": "PDF not found"}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.model_dump())
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    return status_checks


# Register all routers
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(market_router)
app.include_router(trading_router)
app.include_router(ai_router)
app.include_router(workspace_router)
app.include_router(subscription_router)
app.include_router(referral_router)
app.include_router(promo_router)
app.include_router(digest_router)
app.include_router(push_router)
app.include_router(journal_router)
app.include_router(strategy_router)
app.include_router(intelligence_router)
app.include_router(broker_router)
app.include_router(market_data_router)
app.include_router(sectors_router)
app.include_router(admin_router)
app.include_router(accuracy_router)

# CORS — dynamic origin reflection for httpOnly cookie auth.
# The frontend uses getApiBase() so requests are same-origin in production.
# CORS is still needed for development and edge cases.
# We reflect the request Origin when credentials are required.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class DynamicCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        origin = request.headers.get("origin", "")
        if request.method == "OPTIONS":
            from starlette.responses import Response as StarletteResponse
            resp = StarletteResponse(status_code=204)
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            resp.headers["Access-Control-Max-Age"] = "600"
            return resp
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

app.add_middleware(DynamicCORSMiddleware)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)



async def _pregen_watchlist_intel(database):
    """Pre-generate watchlist intelligence for all users with watchlists (runs 5:30 AM UTC)."""
    try:
        import os
        from services.watchlist_intelligence_service import generate_watchlist_summary
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        count = 0
        cursor = database.watchlists.find({"tickers": {"$exists": True, "$ne": []}})
        async for wl_doc in cursor:
            user_id = wl_doc.get("user_id")
            tickers = wl_doc.get("tickers", [])
            if not tickers or not user_id:
                continue
            try:
                await generate_watchlist_summary(api_key, tickers, db=database, user_id=user_id)
                count += 1
                logger.info(f"Pre-generated watchlist intel for user {user_id} ({len(tickers)} tickers)")
            except Exception as e:
                logger.warning(f"Failed to pre-generate watchlist intel for {user_id}: {e}")
        logger.info(f"Watchlist intelligence pre-generation complete: {count} users processed")
    except Exception as e:
        logger.error(f"Watchlist pre-generation failed: {e}")

@app.on_event("startup")
async def startup_event():
    _wire_db_to_routes()
    await _start_schedulers()
    await create_indexes()
    await seed_admin()
    _start_cache_warmup()
    _write_test_credentials()


def _wire_db_to_routes():
    """Pass db reference to all route modules."""
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
    set_price_provider_db(db)


async def _start_schedulers():
    """Start APScheduler jobs for daily digest and watchlist pre-generation."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from services.digest_service import send_daily_digest
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_daily_digest, 'cron', hour=6, minute=0, args=[db], id='daily_digest')
        scheduler.add_job(_pregen_watchlist_intel, 'cron', hour=5, minute=30, args=[db], id='watchlist_pregen')
        scheduler.start()
        logger.info("Daily digest scheduler started (6:00 AM UTC), watchlist pre-gen (5:30 AM UTC)")
    except Exception as e:
        logger.warning(f"Digest scheduler setup failed: {e}")


def _start_cache_warmup():
    """Pre-populate expensive API caches so the first user never waits."""
    try:
        from services.cache import cache
        from services.sector_service import get_sector_heatmap
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService

        async def _warm():
            for key, fn, ttl in [
                ("sector_heatmap", get_sector_heatmap, 120),
                ("world_events", WorldEventsService().scrape_world_events, 300),
                ("foreign_markets", ForeignMarketsService().get_foreign_markets, 60),
            ]:
                try:
                    await cache.get_or_fetch(key, fn, ttl=ttl)
                    logger.info(f"Cache warm-up: {key} loaded")
                except Exception as e:
                    logger.warning(f"Cache warm-up {key} failed: {e}")

        import asyncio
        asyncio.create_task(_warm())
        logger.info("Cache warm-up started in background")

        # Start periodic prediction verification (every 60 minutes)
        async def _verify_loop():
            while True:
                await asyncio.sleep(3600)
                try:
                    from services.prediction_tracker import verify_pending_predictions
                    await verify_pending_predictions(db)
                    logger.info("Prediction verification cycle complete")
                except Exception as ve:
                    logger.warning(f"Prediction verification failed: {ve}")

        asyncio.create_task(_verify_loop())
        logger.info("Prediction verification scheduler started")
    except Exception as e:
        logger.warning(f"Cache warm-up setup failed: {e}")


def _write_test_credentials():
    """Write test credentials to memory file for testing agents."""
    creds_path = Path("/app/memory/test_credentials.md")
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        "# Test Credentials\n\n"
        "## Owner (REDSLATE)\n"
        f"- Email: {os.environ.get('OWNER_EMAIL', 'managingdirector@redslateholdings.com')}\n"
        f"- Password: {os.environ.get('OWNER_PASSWORD', '')}\n"
        "- Role: owner\n- Subscription: pro\n"
        "- Can activate/deactivate users and grant/revoke Pro\n\n"
        f"## Admin\n- Email: {os.environ.get('ADMIN_EMAIL', 'admin@risedual.ai')}\n"
        f"- Password: {os.environ.get('ADMIN_PASSWORD', '')}\n"
        "- Role: admin\n- Subscription: pro\n\n"
        "## Auth Method\n- httpOnly secure cookies (primary)\n"
        "- POST /api/auth/login → sets access_token + refresh_token cookies\n"
        "- CORS: credentials: 'include' required on all fetch calls\n"
    )


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
