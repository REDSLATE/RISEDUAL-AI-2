"""Smart Order Routes — Advanced order management with ladder, trailing SL/TP, break-even."""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/smart-orders", tags=["smart-orders"])

_db = None

def set_db(database):
    global _db
    _db = database
    from services.smart_order_service import set_db as set_svc_db
    set_svc_db(database)


class TakeProfitLevel(BaseModel):
    price: float
    pct_of_qty: float = 25
    trailing: bool = False
    trailing_pct: float = 1.0

class StopLossConfig(BaseModel):
    price: float
    trailing: bool = False
    trailing_pct: float = 2.0
    cooldown_seconds: int = 0
    emergency_price: Optional[float] = None

class LadderConfig(BaseModel):
    levels: int = Field(ge=2, le=10, default=3)
    range_low: float
    range_high: float
    distribution: str = "equal"  # equal, weighted_bottom, weighted_top

class BreakEvenConfig(BaseModel):
    enabled: bool = False
    trigger_tp_index: int = 0

class SmartOrderRequest(BaseModel):
    symbol: str
    side: str  # buy or sell
    qty: float = Field(gt=0)
    order_type: str = "smart"  # market, limit, ladder, smart
    mode: str = "paper"  # paper, live, simulate
    entry_price: Optional[float] = None
    stop_loss: Optional[StopLossConfig] = None
    take_profits: Optional[List[TakeProfitLevel]] = None
    break_even: Optional[BreakEvenConfig] = None
    ladder: Optional[LadderConfig] = None


@router.post("")
async def create_order(request: Request, order: SmartOrderRequest):
    """Create a smart order with advanced features."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    # Live mode restricted to owner
    if order.mode == "live" and user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Live trading restricted to authorized accounts")

    from services.smart_order_service import create_smart_order
    result = await create_smart_order(user_id, order.model_dump())
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("")
async def list_orders(request: Request, status: Optional[str] = None, limit: int = 50):
    """List smart orders for the current user."""
    user = await get_current_user(request)
    from services.smart_order_service import get_smart_orders
    return await get_smart_orders(user["_id"], status=status, limit=limit)


@router.delete("/{order_id}")
async def cancel_order(order_id: str, request: Request):
    """Cancel an active smart order."""
    user = await get_current_user(request)
    from services.smart_order_service import cancel_smart_order
    result = await cancel_smart_order(user["_id"], order_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result
