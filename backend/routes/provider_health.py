from fastapi import APIRouter, Request, HTTPException
from routes.auth import get_current_user
from services.providerrouter import ProviderRouter

router = APIRouter(prefix="/api/provider-health")
db = None


def set_db(database):
    global db
    db = database


@router.get("")
async def get_provider_health(request: Request):
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"lanes": ProviderRouter.snapshot()}
