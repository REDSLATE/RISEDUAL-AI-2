"""Provider Health + Model Management API.

Admin endpoints:
  GET  /api/provider-health              — full snapshot of all lanes
  GET  /api/provider-health/models       — list all registered models with health
  GET  /api/provider-health/models?lane=ai — filter by lane
  POST /api/provider-health/register     — hot-register a new provider
  POST /api/provider-health/deregister   — remove a provider at runtime
  POST /api/provider-health/heartbeat    — external health heartbeat
  POST /api/provider-health/enable       — re-enable a disabled provider
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from routes.auth import get_current_user
from services.providerrouter import ProviderRouter

router = APIRouter(prefix="/api/provider-health")
db = None


def set_db(database):
    global db
    db = database


class RegisterRequest(BaseModel):
    lane: str = Field(..., description="Lane name: ai, market_data, email, etc.")
    name: str = Field(..., description="Unique provider name, e.g. openai-backup-2")
    provider: str = Field(..., description="Provider type: openai, anthropic, alphavantage, finnhub, resend, etc.")
    api_key: str = Field(..., description="API key for this provider")
    model: str = Field("", description="Model name if applicable, e.g. gpt-4.1")
    priority: int = Field(50, description="Priority (lower = higher priority)")


class DeregisterRequest(BaseModel):
    lane: str
    name: str


class HeartbeatRequest(BaseModel):
    lane: str
    name: str
    status: str = Field("ok", description="ok, degraded, or failed")
    latency_ms: float = Field(0, ge=0)
    error_rate: float = Field(0, ge=0, le=1)


class EnableRequest(BaseModel):
    lane: str
    name: str


@router.get("")
async def get_provider_health(request: Request):
    """Full health snapshot — all lanes and providers."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"lanes": ProviderRouter.snapshot()}


@router.get("/models")
async def list_models(request: Request, lane: Optional[str] = None):
    """List all registered providers with health data."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    models = ProviderRouter.list_models(lane)
    return {"models": models, "count": len(models)}


@router.post("/register")
async def register_model(req: RegisterRequest, request: Request):
    """Hot-register a new provider into a lane without restart."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = ProviderRouter.register(req.lane, {
        "name": req.name,
        "provider": req.provider,
        "api_key": req.api_key,
        "model": req.model,
        "priority": req.priority,
    })
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Persist to DB for recovery on restart
    if db is not None:
        await db.registered_providers.update_one(
            {"lane": req.lane, "name": req.name},
            {"$set": {
                "lane": req.lane, "name": req.name, "provider": req.provider,
                "api_key": req.api_key, "model": req.model, "priority": req.priority,
                "registered_by": user.get("email", "admin"),
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    return result


@router.post("/deregister")
async def deregister_model(req: DeregisterRequest, request: Request):
    """Remove a provider from a lane at runtime."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = ProviderRouter.deregister(req.lane, req.name)

    if db is not None:
        await db.registered_providers.delete_one({"lane": req.lane, "name": req.name})

    return result


@router.post("/heartbeat")
async def provider_heartbeat(req: HeartbeatRequest):
    """External health heartbeat — services self-report their status.
    No auth required — services call this themselves."""
    result = ProviderRouter.heartbeat(req.lane, req.name, req.status, req.latency_ms, req.error_rate)
    if not result.get("accepted"):
        raise HTTPException(status_code=404, detail=result.get("error", "Provider not found"))
    return result


@router.post("/enable")
async def enable_provider(req: EnableRequest, request: Request):
    """Re-enable a provider that was disabled (e.g., after auth failure)."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")

    state = ProviderRouter._state.get(req.lane, {}).get(req.name)
    if not state:
        raise HTTPException(status_code=404, detail="Provider not found")

    state["disabled"] = False
    state["cooldown_until"] = None
    state["consecutive_failures"] = 0
    return {"enabled": True, "lane": req.lane, "name": req.name}
