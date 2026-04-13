"""Credit routes — balance, purchase, history, and credit pack info."""
import logging
from fastapi import APIRouter, Request, HTTPException

from services.auth_helpers import get_current_user
from services import credit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credits", tags=["credits"])
db = None


def set_db(database):
    global db
    db = database
    credit_service.set_db(database)


@router.get("/balance")
async def get_balance(request: Request):
    """Get current credit balance."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    is_pro = user.get("subscription_status") == "pro"
    balance = await credit_service.get_balance(user_id)
    balance["is_pro"] = is_pro
    balance["pro_free_actions"] = list(credit_service.PRO_FREE_ACTIONS) if is_pro else []
    return balance


@router.get("/packs")
async def get_packs(request: Request):
    """Get available credit packs."""
    user = await get_current_user(request)
    is_pro = user.get("subscription_status") == "pro"
    packs = []
    for p in credit_service.CREDIT_PACKS:
        if p.get("pro_only") and not is_pro:
            continue
        packs.append({
            "id": p["id"],
            "name": p["name"],
            "credits": p["credits"],
            "price": p["price"],
            "per_credit": round(p["price"] / p["credits"], 4),
        })
    return {"packs": packs}


@router.get("/costs")
async def get_costs():
    """Get credit costs per action."""
    return {
        "costs": credit_service.CREDIT_COSTS,
        "pro_free": list(credit_service.PRO_FREE_ACTIONS),
    }


@router.post("/purchase")
async def purchase_credits(request: Request):
    """Purchase a credit pack."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    body = await request.json()
    pack_id = body.get("pack_id")
    if not pack_id:
        raise HTTPException(status_code=400, detail="pack_id required")

    result = await credit_service.purchase_credits(user_id, pack_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Purchase failed"))
    return result


@router.get("/history")
async def get_history(request: Request, limit: int = 30):
    """Get credit transaction history."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    txns = await credit_service.get_transaction_history(user_id, limit)
    return {"transactions": txns}
