"""Promo campaign routes: admin CRUD + public active promo + user progress."""
import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api/promo")

db = None

def set_db(database):
    global db
    db = database


class PromoCreate(BaseModel):
    title: str
    message: str
    referral_target: int = 3
    reward_months: int = 2
    start_date: str
    end_date: str


def promo_response(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc["title"],
        "message": doc["message"],
        "referral_target": doc["referral_target"],
        "reward_months": doc["reward_months"],
        "start_date": doc["start_date"],
        "end_date": doc["end_date"],
        "is_active": doc.get("is_active", False),
        "created_at": doc.get("created_at", ""),
    }


@router.get("/active")
async def get_active_promo():
    """Public: get the currently active promo (if any)."""
    now = datetime.now(timezone.utc).isoformat()
    promo = await db.promos.find_one({
        "is_active": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
    })
    if not promo:
        return {"promo": None}
    return {"promo": promo_response(promo)}


@router.get("/progress")
async def get_promo_progress(request: Request):
    """Authenticated: user's progress toward the active promo."""
    user = await get_current_user(request)
    user_id = user["_id"]

    now = datetime.now(timezone.utc).isoformat()
    promo = await db.promos.find_one({
        "is_active": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
    })
    if not promo:
        return {"promo": None, "progress": 0, "target": 0, "completed": False}

    # Count referrals completed during promo period
    referrals_in_period = await db.referrals.count_documents({
        "referrer_id": user_id,
        "status": "completed",
        "completed_at": {"$gte": promo["start_date"], "$lte": promo["end_date"]},
    })

    target = promo["referral_target"]
    return {
        "promo": promo_response(promo),
        "progress": referrals_in_period,
        "target": target,
        "completed": referrals_in_period >= target,
    }


@router.get("/all")
async def get_all_promos(request: Request):
    """Admin/owner only: list all promos."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    cursor = db.promos.find({}).sort("created_at", -1)
    promos = []
    async for doc in cursor:
        promos.append(promo_response(doc))
    return {"promos": promos}


@router.post("/create")
async def create_promo(req: PromoCreate, request: Request):
    """Admin/owner only: create a new promo campaign."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    doc = {
        "title": req.title.strip(),
        "message": req.message.strip(),
        "referral_target": req.referral_target,
        "reward_months": req.reward_months,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user["_id"],
    }
    result = await db.promos.insert_one(doc)
    doc["_id"] = result.inserted_id
    logging.info(f"Promo created: {req.title} by {user.get('email')}")
    return promo_response(doc)


@router.put("/{promo_id}/toggle")
async def toggle_promo(promo_id: str, request: Request):
    """Admin/owner only: toggle promo active status."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    promo = await db.promos.find_one({"_id": ObjectId(promo_id)})
    if not promo:
        raise HTTPException(status_code=404, detail="Promo not found")

    new_status = not promo.get("is_active", False)
    await db.promos.update_one({"_id": ObjectId(promo_id)}, {"$set": {"is_active": new_status}})
    return {"id": promo_id, "is_active": new_status}


@router.delete("/{promo_id}")
async def delete_promo(promo_id: str, request: Request):
    """Admin/owner only: delete a promo."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.promos.delete_one({"_id": ObjectId(promo_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Promo not found")
    return {"message": "Promo deleted"}
