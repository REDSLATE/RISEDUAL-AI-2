"""Market Scanner Routes — Pre-built screening strategies with real-time scanning."""
import logging
from datetime import datetime, timezone
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
    from services.ai_signal_validator import set_db as set_validator_db
    set_validator_db(database)


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



@router.get("/indicators")
async def list_indicators():
    """List all available indicators for the custom rule builder."""
    from services.scanner_service import AVAILABLE_INDICATORS, OPERATORS
    return {
        "indicators": [{"id": iid, **info} for iid, info in AVAILABLE_INDICATORS.items()],
        "operators": {k: [{"id": o["id"], "label": o["label"]} for o in v] for k, v in OPERATORS.items()},
    }


@router.post("/custom/run")
async def run_custom_scan(request: Request):
    """Run a custom rule against symbols."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    body = await request.json()
    rule = body.get("rule")
    if not rule or not rule.get("conditions"):
        raise HTTPException(status_code=400, detail="Rule must have at least one condition")

    from services.scanner_service import evaluate_custom_rule, get_user_scan_symbols
    symbols = body.get("symbols") or await get_user_scan_symbols(user_id)
    if len(symbols) > 50:
        symbols = symbols[:50]

    results = await evaluate_custom_rule(symbols, rule)
    return results


@router.post("/custom/save")
async def save_rule(request: Request):
    """Save a custom scanning rule."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    body = await request.json()
    from services.scanner_service import save_custom_rule
    result = await save_custom_rule(user_id, body)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/custom/rules")
async def list_rules(request: Request):
    """List user's saved custom rules."""
    user = await get_current_user(request)
    from services.scanner_service import get_user_rules
    return await get_user_rules(user["_id"])


@router.delete("/custom/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request):
    """Delete a custom rule."""
    user = await get_current_user(request)
    from services.scanner_service import delete_custom_rule
    result = await delete_custom_rule(user["_id"], rule_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/validate")
async def validate_scan_results(request: Request):
    """AI-validate scanner matches using Adversarial AI."""
    user = await get_current_user(request)
    # Deduct credits
    from services.credit_service import deduct_credits
    is_pro = user.get("subscription_status") == "pro" or user.get("role") in ("admin", "owner")
    cr = await deduct_credits(str(user["_id"]), "scanner_validate", is_pro)
    if not cr["allowed"]:
        raise HTTPException(status_code=402, detail=cr.get("error", "Not enough credits"))

    body = await request.json()
    matches = body.get("matches", [])
    strategy_name = body.get("strategy_name", "")

    if not matches:
        raise HTTPException(status_code=400, detail="No matches to validate")

    from services.ai_signal_validator import validate_signals
    validated = await validate_signals(matches, strategy_name)
    # Sort by AI confidence (highest first), None values last
    validated.sort(key=lambda x: x.get("ai_confidence") or 0, reverse=True)

    strong = sum(1 for m in validated if (m.get("ai_confidence") or 0) >= 70)
    moderate = sum(1 for m in validated if 40 <= (m.get("ai_confidence") or 0) < 70)
    weak = sum(1 for m in validated if (m.get("ai_confidence") or 0) < 40 and m.get("ai_validated"))

    return {
        "matches": validated,
        "summary": {
            "total": len(validated),
            "strong_signals": strong,
            "moderate_signals": moderate,
            "weak_signals": weak,
            "avg_confidence": round(sum(m.get("ai_confidence") or 0 for m in validated if m.get("ai_validated")) / max(sum(1 for m in validated if m.get("ai_validated")), 1), 1),
        },
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/validate/stats")
async def validation_stats(request: Request):
    """Get AI validation history stats."""
    await get_current_user(request)
    from services.ai_signal_validator import get_validation_stats
    return await get_validation_stats()
