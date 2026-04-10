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


@router.get("/memory")
async def memory_stats(request: Request):
    """Get vector memory stats — how many market regimes are stored. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    from services.market_memory_service import get_memory_stats
    stats = await get_memory_stats()
    return stats


# ── Training state (module-level for single-process visibility) ──
_training_task = None
_training_status = {"status": "idle"}


@router.post("/memory/train")
async def start_memory_training(request: Request):
    """Bulk-ingest 2 years of historical market regimes into vector memory.
    Runs as a background task. Pro/Admin only."""
    global _training_task, _training_status
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    if _training_task and not _training_task.done():
        return {"status": "already_running", "progress": _training_status}

    import asyncio
    from services.memory_training_service import run_memory_training

    async def _progress_cb(update):
        _training_status.update(update)

    _training_status = {"status": "running", "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}

    async def _run():
        global _training_status
        try:
            result = await run_memory_training(db, progress_callback=_progress_cb)
            _training_status = {**result, "status": "complete"}
        except Exception as e:
            logger.error(f"Training task error: {e}")
            _training_status["status"] = "error"
            _training_status["error"] = str(e)

    _training_task = asyncio.create_task(_run())
    return {"status": "started", "message": "Memory training started in background. Check /api/accuracy/memory/train/status for progress."}


@router.get("/memory/train/status")
async def training_status(request: Request):
    """Check the status of the memory training background task. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    return _training_status

