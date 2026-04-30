"""Smart Order Routes — Advanced order management with ladder, trailing SL/TP, break-even."""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
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
    take_profits: Optional[list[TakeProfitLevel]] = None
    break_even: Optional[BreakEvenConfig] = None
    ladder: Optional[LadderConfig] = None


@router.post("")
async def create_order(request: Request, order: SmartOrderRequest):
    """Create a smart order with advanced features.

    Mode coherence: the payload's ``order.mode`` must match the user's
    global trading mode (set via the navbar pill). Mismatch → 403
    ``wrong_mode`` so the UI can prompt the user to flip first.
    """
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    # Live mode restricted to owner
    if order.mode == "live" and user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Live trading restricted to authorized accounts")

    # Mode coherence — payload ``mode`` must match user's global pill.
    # ``simulate`` is a developer-only mode that bypasses the gate.
    if order.mode in ("paper", "live"):
        from services.trading_mode_service import get_user_trading_mode
        user_mode = await get_user_trading_mode(user)
        if user_mode != order.mode:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "wrong_mode",
                    "required_mode": order.mode,
                    "current_mode": user_mode,
                    "message": (
                        f"This smart order is configured for {order.mode.upper()} "
                        f"but your account is in {user_mode.upper()} mode. "
                        f"Switch via the navbar pill to continue."
                    ),
                },
            )

    from services.smart_order_service import create_smart_order
    # ── Patent J/K/M/I — pre-create guard (live orders only) ─────────
    # Live orders flow through the full guard pipeline. Paper /
    # simulate are observational — they short-circuit the gateway
    # entirely since paper trading is sandboxed.
    if order.mode == "live":
        from services.manual_order_guard import run_manual_order_guard
        _qty = float(getattr(order, "qty", 0) or 0)
        _entry = float(getattr(order, "entry_price", 0) or 0)
        _est_notional = _qty * _entry if _entry > 0 else 0.0
        if _est_notional > 0:
            from server import db as _server_db
            guard = await run_manual_order_guard(
                db=_server_db,
                user=user,
                asset_class="equity",
                symbol=str(order.symbol),
                side=str(order.side),
                base_notional=_est_notional,
                context={
                    "route": "smart_orders",
                    "order_type": getattr(order, "order_type", None),
                },
            )
            if not guard["allow"]:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "patent_guard_denied",
                        "reason_code": guard.get("reason_code"),
                        "reasons": guard.get("reasons", []),
                        "proof_hashes": guard.get("proof_hashes", []),
                        "message": guard.get("message", "Order blocked by guard"),
                    },
                )
            # Persist the IP-contract entity_id on the order payload so
            # ``smart_order_service`` carries it through to the
            # smart_orders Mongo row. The position-close path can then
            # call ``record_manual_order_outcome`` to append
            # OUTCOME_VERIFIED — closing the IP chain proposal-through
            # realized-P&L for manual orders.
            entity_id = guard.get("proof_chain_entity_id")
            if entity_id:
                # Stash in a side-channel field consumed by
                # ``create_smart_order``. Keeping it on the dict (not
                # the pydantic model) avoids schema-bumping the public
                # SmartOrderCreate contract.
                order_dict_extra = {"proof_chain_entity_id": entity_id}
            else:
                order_dict_extra = {}
        else:
            order_dict_extra = {}
    else:
        order_dict_extra = {}

    result = await create_smart_order(
        user_id, {**order.model_dump(), **order_dict_extra},
    )
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
