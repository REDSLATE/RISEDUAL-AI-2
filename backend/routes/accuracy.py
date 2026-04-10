"""Prediction accuracy tracking routes (Pro only)."""
from fastapi import APIRouter, HTTPException, Request
from services.auth_helpers import get_current_user, is_pro_user
from services.prediction_tracker import (
    get_all_feature_stats, get_recent_predictions,
    verify_pending_predictions, get_accuracy_stats, FAILURE_MODES,
)
import logging

logger = logging.getLogger(__name__)

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


@router.post("/post-mortem/{prediction_id}")
async def run_post_mortem_endpoint(prediction_id: str, request: Request):
    """Run AI-powered post-mortem analysis on a failed prediction. Pro only.

    Fetches news context for the ticker, sends to GPT-4o-mini for classification,
    and updates both MongoDB and ChromaDB with the AI-classified failure mode.
    """
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    pred = await db.predictions.find_one({"prediction_id": prediction_id}, {"_id": 0})
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")

    # Check if prediction has been verified and was wrong
    v24h = pred.get("verified_24h")
    if not v24h:
        raise HTTPException(status_code=400, detail="Prediction hasn't been verified yet")
    if v24h.get("correct"):
        raise HTTPException(status_code=400, detail="Prediction was correct — no post-mortem needed")

    price_now = v24h.get("price", 0)
    heuristic_code = v24h.get("failure_code", "UNKNOWN")

    from services.post_mortem_service import run_and_update_post_mortem
    result = await run_and_update_post_mortem(db, pred, price_now, heuristic_code)

    return {
        "prediction_id": prediction_id,
        "failure_code": result.get("failure_code", "UNKNOWN"),
        "reasoning": result.get("reasoning", ""),
        "key_headline": result.get("key_headline"),
        "source": result.get("source", "heuristic"),
        "heuristic_code": heuristic_code,
    }


@router.get("/post-mortem/history")
async def post_mortem_history(request: Request, limit: int = 20):
    """Get recent AI post-mortem results. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    cursor = db.post_mortem_log.find({}, {"_id": 0}).sort("run_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        results.append(doc)
    return {"post_mortems": results, "count": len(results)}



@router.get("/failure-modes")
async def get_failure_modes(request: Request):
    """Get available failure mode categories."""
    await get_current_user(request)
    return {"failure_modes": FAILURE_MODES}


@router.post("/classify/{prediction_id}")
async def classify_failure(prediction_id: str, request: Request):
    """Manually classify a prediction failure. Pro only.

    Body: {"failure_code": "TECH_FAKEOUT" | "MACRO_SHOCK" | "LIQUIDITY_GAP" | "REGIME_SHIFT" | "UNKNOWN"}
    """
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    body = await request.json()
    code = body.get("failure_code", "UNKNOWN").upper()
    if code not in FAILURE_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid failure_code. Must be one of: {list(FAILURE_MODES.keys())}")

    # Update MongoDB prediction
    pred = await db.predictions.find_one({"prediction_id": prediction_id}, {"_id": 0})
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")

    reason = FAILURE_MODES[code]
    update_fields = {}
    if pred.get("verified_24h") and not pred["verified_24h"].get("correct"):
        update_fields["verified_24h.failure_code"] = code
        update_fields["verified_24h.failure_reason"] = reason
    if pred.get("verified_1w") and not pred["verified_1w"].get("correct"):
        update_fields["verified_1w.failure_code"] = code
        update_fields["verified_1w.failure_reason"] = reason

    if not update_fields:
        raise HTTPException(status_code=400, detail="Prediction was not a failure or hasn't been verified yet")

    await db.predictions.update_one(
        {"prediction_id": prediction_id},
        {"$set": update_fields}
    )

    # Also update ChromaDB metadata if the episode exists
    try:
        from services.market_memory_service import _collection
        if _collection:
            import asyncio
            import hashlib
            doc_id = hashlib.md5(
                f"{pred['symbol']}|{pred.get('timestamp', '')[:10]}|{pred.get('price_at_prediction', '')}".encode()
            ).hexdigest()
            existing = await asyncio.to_thread(_collection.get, ids=[doc_id])
            if existing and existing.get("ids"):
                meta = existing["metadatas"][0].copy() if existing.get("metadatas") else {}
                meta["failure_code"] = code
                await asyncio.to_thread(_collection.update, ids=[doc_id], metadatas=[meta])
                logger.info(f"Updated ChromaDB failure_code for {pred['symbol']}: {code}")
    except Exception as e:
        logger.warning(f"ChromaDB failure classification update failed: {e}")

    return {
        "prediction_id": prediction_id,
        "failure_code": code,
        "failure_reason": reason,
        "message": f"Classified as {code}",
    }


@router.get("/failure-breakdown")
async def failure_breakdown(request: Request):
    """Get breakdown of failure modes across all wrong predictions. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    pipeline = [
        {"$match": {"verified_24h.correct": False}},
        {"$group": {
            "_id": "$verified_24h.failure_code",
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]
    breakdown = {}
    async for doc in db.predictions.aggregate(pipeline):
        code = doc["_id"] or "UNCLASSIFIED"
        breakdown[code] = {
            "count": doc["count"],
            "description": FAILURE_MODES.get(code, "Pre-classification prediction"),
        }

    total_failures = sum(v["count"] for v in breakdown.values())
    return {
        "total_failures": total_failures,
        "breakdown": breakdown,
        "failure_modes": FAILURE_MODES,
    }



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



@router.post("/memory/cleanup")
async def trigger_cleanup(request: Request, days: int = 90, threshold: float = 80.0):
    """Run nightly memory cleanup: remove toxic outliers + obsolete data. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    from services.market_memory_service import nightly_cleanup
    result = await nightly_cleanup(days_to_keep=days, toxic_confidence_threshold=threshold)
    return result


@router.get("/memory/cleanup/history")
async def cleanup_history(request: Request):
    """Get recent cleanup run history. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    if db is None:
        return {"runs": []}
    cursor = db.memory_cleanup_log.find({}, {"_id": 0}).sort("run_at", -1).limit(10)
    runs = []
    async for doc in cursor:
        runs.append(doc)
    return {"runs": runs}
