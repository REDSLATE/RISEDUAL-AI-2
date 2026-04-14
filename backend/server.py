"""RISEDUAL AI — FastAPI Application Entry Point.

Thin orchestrator: connects MongoDB, registers route modules, handles startup/shutdown.
"""
from fastapi import FastAPI, APIRouter
from fastapi.responses import FileResponse
from dotenv import load_dotenv
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

from route_registry import register_all_routers, wire_db, seed_admin, create_indexes

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


@api_router.get("/ready")
async def readiness_check():
    """Health/readiness check — confirms DB is connected and routes are registered."""
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "routes": len(app.routes),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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
register_all_routers(app)

# CORS — dynamic origin reflection for httpOnly cookie auth.
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
            except Exception as e:
                logger.warning(f"Failed to pre-generate watchlist intel for {user_id}: {e}")
        logger.info(f"Watchlist intelligence pre-generation complete: {count} users processed")
    except Exception as e:
        logger.error(f"Watchlist pre-generation failed: {e}")


async def _run_memory_cleanup():
    """Scheduled nightly memory cleanup task."""
    try:
        from services.market_memory_service import nightly_cleanup
        result = await nightly_cleanup(days_to_keep=90, toxic_confidence_threshold=80.0)
        logger.info(f"Nightly memory cleanup: {result.get('toxic_removed', 0)} toxic + {result.get('obsolete_removed', 0)} obsolete removed")
    except Exception as e:
        logger.warning(f"Memory cleanup failed: {e}")


async def _run_waitlist_auto_invite():
    """Scheduled daily task: Auto-invite top 5 users from the waitlist."""
    try:
        from services.waitlist_service import auto_invite_top_users
        invited = await auto_invite_top_users(batch_size=5)
        if invited:
            logger.info(f"Waitlist auto-invite: Sent {len(invited)} War Room invites")
            for u in invited:
                logger.info(f"  Invited: {u['email']} (rank #{u['rank']}, key: {u['beta_key']}, email_sent: {u['email_sent']})")
        else:
            logger.info("Waitlist auto-invite: No users to invite")
    except Exception as e:
        logger.warning(f"Waitlist auto-invite failed: {e}")


@app.on_event("startup")
async def startup_event():
    logger.info("=== RISEDUAL AI STARTUP BEGIN ===")
    try:
        wire_db(db)
        app.state.db = db
        logger.info("DB wired successfully")
    except Exception as e:
        logger.exception(f"CRITICAL: DB wire failed: {e}")

    try:
        await _start_schedulers()
        logger.info("Schedulers started")
    except Exception as e:
        logger.warning(f"Scheduler startup failed (non-critical): {e}")

    try:
        await create_indexes()
        logger.info("Indexes created")
    except Exception as e:
        logger.warning(f"Index creation failed (non-critical): {e}")

    try:
        await seed_admin()
        logger.info("Admin seed complete")
    except Exception as e:
        logger.warning(f"Admin seed failed (non-critical): {e}")

    try:
        _start_cache_warmup()
    except Exception as e:
        logger.warning(f"Cache warmup failed (non-critical): {e}")

    try:
        _write_test_credentials()
    except Exception as e:
        logger.warning(f"Test credentials write failed (non-critical): {e}")

    logger.info(f"=== RISEDUAL AI STARTUP COMPLETE — {len(app.routes)} routes registered ===")


async def _start_schedulers():
    """Start APScheduler jobs for daily digest, watchlist pre-gen, and memory cleanup."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from services.digest_service import send_daily_digest
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_daily_digest, 'cron', hour=6, minute=0, args=[db], id='daily_digest')
        scheduler.add_job(_pregen_watchlist_intel, 'cron', hour=5, minute=30, args=[db], id='watchlist_pregen')
        scheduler.add_job(_run_memory_cleanup, 'cron', hour=2, minute=0, id='memory_cleanup')
        scheduler.add_job(_run_waitlist_auto_invite, 'cron', hour=9, minute=0, id='waitlist_auto_invite')
        scheduler.add_job(_check_smart_orders, 'interval', seconds=30, id='smart_order_monitor')
        scheduler.add_job(_run_grid_bots, 'interval', seconds=30, id='grid_bot_monitor')
        scheduler.add_job(_run_headlines_pipeline, 'interval', minutes=15, id='headlines_pipeline')
        scheduler.add_job(_run_prediction_prewarm, 'interval', minutes=10, id='prediction_prewarm')
        scheduler.start()
        logger.info("Schedulers started: digest (6:00), watchlist (5:30), memory cleanup (2:00), waitlist invite (9:00), smart orders (30s), grid bots (30s), headlines (15m), predictions (10m)")
    except Exception as e:
        logger.warning(f"Scheduler setup failed: {e}")


async def _check_smart_orders():
    """Background: Monitor smart orders and trigger SL/TP/ladder fills."""
    try:
        from services.smart_order_service import check_smart_orders
        await check_smart_orders()
    except Exception as e:
        logger.debug(f"Smart order check error: {e}")


async def _run_grid_bots():
    """Background: Run all enabled grid bots."""
    try:
        from services.trading_bot_service import run_grid_bots
        await run_grid_bots()
    except Exception as e:
        logger.debug(f"Grid bot error: {e}")


async def _run_headlines_pipeline():
    """Background: Scrape, clean, and store financial headlines every 15 minutes."""
    try:
        from services.headlines_pipeline import HeadlinesPipeline
        pipeline = HeadlinesPipeline(db)
        result = await pipeline.run_cycle()
        if result.get("total_new", 0) > 0:
            logger.info(f"Headlines pipeline: {result['total_new']} new / {result['total_scraped']} scraped")
    except Exception as e:
        logger.debug(f"Headlines pipeline error: {e}")


async def _run_prediction_prewarm():
    """Background: Keep predictions warm every 10 minutes."""
    try:
        from routes.market_data import ensure_prediction_refresh
        await ensure_prediction_refresh()
    except Exception as e:
        logger.debug(f"Prediction prewarm error: {e}")


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
