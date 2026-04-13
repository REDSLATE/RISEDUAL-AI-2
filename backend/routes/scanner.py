"""Market Scanner Routes — Pre-built screening strategies with real-time scanning."""
import logging
from fastapi import APIRouter, Request, HTTPException
from typing import Optional, List
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scanner", tags=["scanner"])

_db = None

def set_db(database):
    global _db
    _db = database
    from services.scanner_service import set_db as set_svc_db
    set_svc_db(database)


@router.get("/strategies")
async def list_strategies():
    """List all available scanning strategies."""
    from services.scanner_service import STRATEGIES
    return {"strategies": [
        {"id": sid, **info} for sid, info in STRATEGIES.items()
    ]}


@router.post("/scan")
async def run_scan(request: Request, strategies: Optional[List[str]] = None, symbols: Optional[List[str]] = None):
    """Run scanner against selected strategies and symbols.
    
    Body (optional JSON):
      strategies: list of strategy IDs to run (default: all)
      symbols: list of symbols to scan (default: watchlist + popular)
    """
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    from services.scanner_service import scan_symbols, get_user_scan_symbols

    # Parse body if provided
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    strat_list = body.get("strategies") or strategies
    sym_list = body.get("symbols") or symbols
    if not sym_list:
        sym_list = await get_user_scan_symbols(user_id)

    if len(sym_list) > 50:
        sym_list = sym_list[:50]

    results = await scan_symbols(sym_list, strat_list)
    return results


@router.get("/quick/{strategy_id}")
async def quick_scan(strategy_id: str, request: Request):
    """Run a single strategy scan against popular tickers."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    from services.scanner_service import STRATEGIES, scan_symbols, get_user_scan_symbols

    if strategy_id not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy_id}")

    sym_list = await get_user_scan_symbols(user_id)
    results = await scan_symbols(sym_list, [strategy_id])
    strategy_result = results.get("strategies", {}).get(strategy_id, {})
    return {
        "strategy": {strategy_id: STRATEGIES[strategy_id]},
        "matches": strategy_result.get("matches", []),
        "match_count": strategy_result.get("match_count", 0),
        "scanned": results.get("scanned", 0),
        "scanned_at": results.get("scanned_at"),
    }
