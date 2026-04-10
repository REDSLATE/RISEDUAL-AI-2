"""Prediction accuracy tracking routes (Pro only)."""
from fastapi import APIRouter, HTTPException, Request
from services.auth_helpers import get_current_user, is_pro_user
from services.prediction_tracker import get_all_feature_stats, get_recent_predictions, verify_pending_predictions, get_accuracy_stats

router = APIRouter(prefix="/api/accuracy", tags=["accuracy"])

db = None

def set_db(database):
    global db
    db = database


@router.get("/stats")
async def accuracy_stats(request: Request):
    """Get prediction accuracy stats for all features. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required for accuracy tracking")
    return await get_all_feature_stats(db)


@router.get("/stats/{feature}")
async def feature_accuracy(feature: str, request: Request):
    """Get accuracy stats for a specific feature. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    valid = {"war_room", "hypothesis", "market_prediction"}
    if feature not in valid:
        raise HTTPException(status_code=400, detail=f"Feature must be one of: {valid}")
    return await get_accuracy_stats(db, feature)


@router.get("/history")
async def prediction_history(request: Request, feature: str = None, limit: int = 20):
    """Get recent predictions with verification status. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    predictions = await get_recent_predictions(db, feature, min(limit, 50))
    return {"predictions": predictions, "count": len(predictions)}


@router.post("/verify")
async def trigger_verification(request: Request):
    """Manually trigger verification of pending predictions. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    await verify_pending_predictions(db)
    return {"status": "verification_complete"}
