"""RISEDUAL AI — FastAPI Application Entry Point.

Thin orchestrator: connects MongoDB, registers route modules, handles startup/shutdown.
"""
from fastapi import FastAPI, APIRouter
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup_event():
    # Pass db reference to all route modules that need it
    set_auth_db(db)
    set_ai_db(db)
    set_workspace_db(db)
    set_subscription_db(db)
    set_referral_db(db)
    set_promo_db(db)

    await create_indexes()
    await seed_admin()

    # Write test credentials
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
        "## Auth Method\n- Bearer token via localStorage\n"
        "- POST /api/auth/login → returns access_token + refresh_token\n"
    )


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
