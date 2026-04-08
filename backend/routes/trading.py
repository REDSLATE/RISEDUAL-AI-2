"""Legacy trading routes — kept for backward compatibility.
All real broker functionality is now in routes/broker.py."""
from typing import Dict
from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/trading/health")
async def trading_health() -> Dict[str, str]:
    """Health check for trading module."""
    return {"status": "ok", "module": "trading", "note": "Use /api/broker/* for all broker operations"}
