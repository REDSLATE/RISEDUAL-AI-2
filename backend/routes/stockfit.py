"""StockFit Fundamentals routes — SEC EDGAR structured data.

GET /api/stockfit/fundamentals/{symbol} — aggregated view
GET /api/stockfit/income/{symbol}       — income statement
GET /api/stockfit/balance-sheet/{symbol} — balance sheet
GET /api/stockfit/scores/{symbol}       — F-Score, Z-Score
GET /api/stockfit/earnings/{symbol}     — earnings snapshot
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException

from services.search_war_room.adapters.stockfit import (
    get_financials, get_health_scores, get_earnings,
    _get_key, _headers, _safe_get, BASE,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stockfit", tags=["stockfit"])

db = None

def set_db(database):
    global db
    db = database


def _check_key():
    if not _get_key():
        raise HTTPException(status_code=503, detail="StockFit API key not configured")


@router.get("/fundamentals/{symbol}")
async def get_fundamentals(symbol: str):
    """Aggregated fundamentals: income + balance sheet + scores + earnings."""
    _check_key()
    ticker = symbol.upper()

    import httpx
    key = _get_key()
    h = _headers(key)

    async with httpx.AsyncClient(timeout=15) as client:
        income, balance, scores, earnings = await asyncio.gather(
            _safe_get(client, f"{BASE}/api/financials/income-statement",
                      {"symbol": ticker, "period": "annual", "limit": 3}, h),
            _safe_get(client, f"{BASE}/api/financials/balance-sheet",
                      {"symbol": ticker, "period": "annual", "limit": 3}, h),
            _safe_get(client, f"{BASE}/api/financials/scores",
                      {"symbol": ticker}, h),
            _safe_get(client, f"{BASE}/api/earnings/snapshot",
                      {"symbol": ticker}, h),
        )

    # Parse income statement
    income_data = []
    if isinstance(income, list):
        for entry in income[:3]:
            facts = entry.get("facts", {})
            income_data.append({
                "period": entry.get("period"),
                "revenue": facts.get("revenue"),
                "grossProfit": facts.get("grossProfit"),
                "operatingIncome": facts.get("operatingIncome"),
                "netIncome": facts.get("netIncome"),
                "eps": facts.get("eps"),
                "epsDiluted": facts.get("epsDiluted"),
                "ebitda": facts.get("ebitda"),
            })

    # Parse balance sheet
    balance_data = []
    if isinstance(balance, list):
        for entry in balance[:3]:
            facts = entry.get("facts", {})
            balance_data.append({
                "period": entry.get("period"),
                "assets": facts.get("assets"),
                "currentAssets": facts.get("currentAssets"),
                "cash": facts.get("cash"),
                "totalDebt": facts.get("totalDebt"),
                "totalLiabilities": facts.get("totalLiabilities"),
                "totalEquity": facts.get("totalEquity"),
                "netReceivables": facts.get("netReceivables"),
                "inventory": facts.get("inventory"),
            })

    # Parse scores
    scores_data = None
    if scores and not scores.get("error"):
        scores_data = {
            "period": scores.get("period"),
            "piotroskiFScore": scores.get("piotroskiFScore"),
            "altmanZScore": scores.get("altmanZScore"),
            "piotroskiDetails": scores.get("piotroskiDetails"),
        }
        z = scores.get("altmanZScore")
        if z is not None:
            scores_data["zScoreZone"] = "safe" if z > 2.99 else "grey" if z > 1.81 else "distress"

    # Parse earnings
    earnings_data = None
    if earnings and not earnings.get("error"):
        earnings_data = {
            "period": earnings.get("period"),
            "eps": earnings.get("eps"),
            "epsDiluted": earnings.get("epsDiluted"),
            "revenue": earnings.get("revenue"),
            "netIncome": earnings.get("netIncome"),
            "revenueGrowth": earnings.get("revenueGrowth"),
            "netIncomeGrowth": earnings.get("netIncomeGrowth"),
            "grossProfitMargin": earnings.get("grossProfitMargin"),
            "operatingMargin": earnings.get("operatingMargin"),
            "netMargin": earnings.get("netMargin"),
        }

    has_data = bool(income_data or balance_data or scores_data or earnings_data)
    if not has_data:
        raise HTTPException(status_code=404, detail=f"No StockFit data available for {ticker}")

    return {
        "symbol": ticker,
        "income": income_data,
        "balanceSheet": balance_data,
        "scores": scores_data,
        "earnings": earnings_data,
    }


@router.get("/income/{symbol}")
async def get_income_statement(symbol: str, period: str = "annual", limit: int = 4):
    """Income statement data."""
    _check_key()
    data = await get_financials(symbol, period=period, limit=min(limit, 8))
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(status_code=502, detail=data["error"])
    return {"symbol": symbol.upper(), "statements": data}


@router.get("/balance-sheet/{symbol}")
async def get_balance_sheet(symbol: str, limit: int = 4):
    """Balance sheet data."""
    _check_key()
    import httpx
    key = _get_key()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE}/api/financials/balance-sheet",
                params={"symbol": symbol.upper(), "period": "annual", "limit": min(limit, 8)},
                headers=_headers(key),
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"StockFit returned {resp.status_code}")
            return {"symbol": symbol.upper(), "statements": resp.json()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:100])


@router.get("/scores/{symbol}")
async def get_scores(symbol: str):
    """Financial health scores (Piotroski F-Score, Altman Z-Score)."""
    _check_key()
    data = await get_health_scores(symbol)
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(status_code=502, detail=data["error"])
    return {"symbol": symbol.upper(), "scores": data}


@router.get("/earnings/{symbol}")
async def get_earnings_data(symbol: str):
    """Earnings snapshot."""
    _check_key()
    data = await get_earnings(symbol)
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(status_code=502, detail=data["error"])
    return {"symbol": symbol.upper(), "earnings": data}
