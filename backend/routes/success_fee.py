"""Success Fee routes — performance-based fee tracking for broker-connected users."""
import logging
from fastapi import APIRouter, Request, HTTPException

from services.auth_helpers import get_current_user
from services import success_fee_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/success-fee", tags=["success-fee"])
db = None


def set_db(database):
    global db
    db = database
    success_fee_service.set_db(database)


async def _require_admin(request: Request):
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/current")
async def get_current_fee(request: Request):
    """Get the current month's success fee summary for the logged-in user."""
    user = await get_current_user(request)
    user_id = str(user["_id"]) if "_id" in user else user.get("id", "")
    result = await success_fee_service.get_current_fee(user_id)
    return result


@router.get("/history")
async def get_fee_history(request: Request, limit: int = 12):
    """Get past fee records for the logged-in user."""
    user = await get_current_user(request)
    user_id = str(user["_id"]) if "_id" in user else user.get("id", "")
    records = await success_fee_service.get_fee_history(user_id, limit)
    return {"records": records}


@router.get("/admin/stats")
async def admin_fee_stats(request: Request):
    """Admin: get aggregate fee statistics."""
    await _require_admin(request)
    stats = await success_fee_service.get_fee_stats_admin()
    return stats


@router.get("/admin/list")
async def admin_fee_list(request: Request, status: str = "all", limit: int = 50):
    """Admin: list all fee records."""
    await _require_admin(request)
    records = await success_fee_service.get_all_fees_admin(status, limit)
    return {"records": records}


@router.post("/admin/mark-paid")
async def admin_mark_paid(request: Request):
    """Admin: mark a fee record as paid."""
    await _require_admin(request)
    body = await request.json()
    user_id = body.get("user_id")
    period = body.get("period")
    note = body.get("note", "")
    if not user_id or not period:
        raise HTTPException(status_code=400, detail="user_id and period required")
    ok = await success_fee_service.mark_fee_paid(user_id, period, note)
    if not ok:
        raise HTTPException(status_code=404, detail="Fee record not found")
    return {"success": True}


@router.post("/admin/waive")
async def admin_waive_fee(request: Request):
    """Admin: waive a fee record."""
    await _require_admin(request)
    body = await request.json()
    user_id = body.get("user_id")
    period = body.get("period")
    note = body.get("note", "")
    if not user_id or not period:
        raise HTTPException(status_code=400, detail="user_id and period required")
    ok = await success_fee_service.waive_fee(user_id, period, note)
    if not ok:
        raise HTTPException(status_code=404, detail="Fee record not found")
    return {"success": True}
