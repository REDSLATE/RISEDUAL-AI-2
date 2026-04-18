"""SEC 13F Holder Tracking API routes.

Endpoints:
- GET  /api/stockfit/13f/institutions            — list tracked institutions
- GET  /api/stockfit/13f/institution/{cik}       — institution's top holdings
- GET  /api/stockfit/13f/holders/{symbol}        — who owns this stock
- GET  /api/stockfit/13f/changes/{cik}           — QoQ changes for an institution
- GET  /api/stockfit/13f/alerts                  — user-relevant 13F alerts (based on watchlist)
- POST /api/stockfit/13f/refresh                 — admin: force refresh (body: {cik?: str})
"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from services.sec_13f_service import (
    TOP_INSTITUTIONS,
    refresh_institution,
    refresh_all_institutions,
    get_institution_holdings,
    get_holders_of_symbol,
    get_quarterly_changes,
)
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stockfit/13f", tags=["stockfit-13f"])

db = None


def set_db(database):
    global db
    db = database


class RefreshRequest(BaseModel):
    cik: Optional[str] = None


@router.get("/institutions")
async def list_institutions():
    """List all tracked top institutions with their latest filing period_end if available."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    out = []
    for name, cik in TOP_INSTITUTIONS.items():
        latest = await db.sec_13f_filings.find_one(
            {"cik": cik}, {"_id": 0, "period_end": 1, "filing_date": 1, "total_value_usd": 1, "holdings_count": 1},
            sort=[("period_end", -1)],
        )
        out.append({
            "cik": cik,
            "name": name,
            "latest_period_end": latest.get("period_end") if latest else None,
            "latest_filing_date": latest.get("filing_date") if latest else None,
            "total_value_usd": latest.get("total_value_usd") if latest else None,
            "holdings_count": latest.get("holdings_count") if latest else None,
        })
    return {"institutions": out, "count": len(out)}


@router.get("/institution/{cik}")
async def institution_holdings(cik: str, limit: int = 50):
    """Top holdings for an institution (latest quarter)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    cik = cik.zfill(10) if cik.isdigit() else cik
    data = await get_institution_holdings(db, cik, limit=min(limit, 200))
    if not data.get("filing"):
        raise HTTPException(status_code=404, detail=f"No 13F filings stored for CIK {cik}. Try POST /refresh first.")
    name = next((n for n, c in TOP_INSTITUTIONS.items() if c == cik), cik)
    return {
        "cik": cik,
        "institution_name": name,
        "filing": data["filing"],
        "holdings": data["holdings"],
        "holdings_count": len(data["holdings"]),
    }


@router.get("/holders/{symbol}")
async def holders_of_symbol(symbol: str, limit: int = 50):
    """Which tracked institutions hold the given symbol (latest quarters)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    data = await get_holders_of_symbol(db, symbol, limit=min(limit, 200))
    return data


@router.get("/changes/{cik}")
async def quarterly_changes(cik: str, limit: int = 30):
    """QoQ changes for an institution: new / exited / increased / decreased."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    cik = cik.zfill(10) if cik.isdigit() else cik
    data = await get_quarterly_changes(db, cik, limit=min(limit, 200))
    if not data.get("latest") or not data.get("previous"):
        raise HTTPException(
            status_code=404,
            detail=f"Need at least 2 stored filings for CIK {cik} to compute QoQ changes.",
        )
    name = next((n for n, c in TOP_INSTITUTIONS.items() if c == cik), cik)
    data["institution_name"] = name
    return data


@router.get("/alerts")
async def user_alerts(request: Request, limit: int = 30, unread_only: bool = False):
    """Return 13F alerts relevant to the authenticated user's watchlist."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    wl = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0, "tickers": 1})
    symbols = set()
    if wl:
        for t in (wl.get("tickers") or []):
            if isinstance(t, str):
                symbols.add(t.upper())
            elif isinstance(t, dict) and t.get("symbol"):
                symbols.add(t["symbol"].upper())
    if not symbols:
        return {"alerts": [], "count": 0, "watchlist_size": 0}

    query = {"symbol": {"$in": list(symbols)}}
    if unread_only:
        # Alerts get marked read per-user via a separate collection or a user_id filter — for MVP we skip read-state.
        pass
    cursor = db.sec_13f_alerts.find(query, {"_id": 0}).sort("created_at", -1).limit(min(limit, 200))
    alerts = await cursor.to_list(200)
    return {"alerts": alerts, "count": len(alerts), "watchlist_size": len(symbols)}


@router.post("/refresh")
async def trigger_refresh(body: RefreshRequest, request: Request):
    """Admin-only: force a refresh of 13F filings.

    If ``cik`` is provided, only that institution is refreshed. Otherwise all tracked
    institutions are refreshed (slow — can take 2-3 minutes).
    """
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    if body.cik:
        cik = body.cik.zfill(10) if body.cik.isdigit() else body.cik
        name = next((n for n, c in TOP_INSTITUTIONS.items() if c == cik), "Unknown")
        result = await refresh_institution(db, cik, name, max_filings=2)
        return {"ok": True, "result": result}

    # Full refresh — run inline; user is admin and expects the wait
    result = await refresh_all_institutions(db, max_filings=2)
    return {"ok": True, "summary": result}
