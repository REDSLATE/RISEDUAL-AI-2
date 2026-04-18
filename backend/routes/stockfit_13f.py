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
    compute_smart_money_score,
    compute_smart_money_scores_batch,
    snapshot_smart_money_scores,
    detect_smart_money_shifts,
)
from services.cusip_mapper import backfill_from_holdings
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stockfit/13f", tags=["stockfit-13f"])

db = None


def set_db(database) -> None:
    global db
    db = database


class RefreshRequest(BaseModel):
    cik: Optional[str] = None


@router.get("/institutions")
async def list_institutions() -> dict:
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
async def institution_holdings(cik: str, limit: int = 50) -> dict:
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
async def holders_of_symbol(symbol: str, limit: int = 50) -> dict:
    """Which tracked institutions hold the given symbol (latest quarters)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    data = await get_holders_of_symbol(db, symbol, limit=min(limit, 200))
    return data


@router.get("/changes/{cik}")
async def quarterly_changes(cik: str, limit: int = 30) -> dict:
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
async def user_alerts(request: Request, limit: int = 30, unread_only: bool = False) -> dict:
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
async def trigger_refresh(body: RefreshRequest, request: Request) -> dict:
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


@router.post("/backfill-cusips")
async def backfill_cusips(request: Request, limit: int = 5000, top_only: bool = False) -> dict:
    """Admin-only: resolve CUSIP → ticker for CUSIPs stored in ``sec_13f_holdings``
    using OpenFIGI. Persists mappings to ``cusip_ticker_map`` so lookups are instant.

    Parameters:
      - ``top_only=true`` — only resolve top 50 CUSIPs per institution (~600 CUSIPs, ~3 min anon)
      - ``top_only=false`` (default) — resolve ALL distinct CUSIPs (~7800, ~30 min anon)

    Set ``OPENFIGI_API_KEY`` env var to drop runtime from minutes to seconds
    (free key at https://www.openfigi.com/api/documentation).
    """
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    result = await backfill_from_holdings(db, limit=min(limit, 20000), top_only=top_only)
    return {"ok": True, "summary": result}


@router.get("/coverage")
async def cusip_coverage() -> dict:
    """Return CUSIP→ticker mapping coverage stats."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    distinct_cusips = await db.sec_13f_holdings.distinct("cusip")
    distinct_cusips = [c for c in distinct_cusips if c]
    mapped_count = await db.cusip_ticker_map.count_documents({
        "cusip": {"$in": distinct_cusips}, "ticker": {"$ne": None},
    })
    total = len(distinct_cusips)
    return {
        "total_cusips_in_holdings": total,
        "mapped_cusips": mapped_count,
        "coverage_pct": round(mapped_count / total * 100, 2) if total else 0.0,
    }


@router.get("/smart-money-score/{symbol}")
async def smart_money_score(symbol: str) -> dict:
    """Return the Smart Money Score (0-100) for a single symbol.

    50 = neutral. >=60 = bullish institutional consensus. <=40 = bearish.
    Includes top contributor institutions for drill-down.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return await compute_smart_money_score(db, symbol)


@router.get("/smart-money-scores")
async def smart_money_scores_batch(symbols: str) -> dict:
    """Batch Smart Money Score lookup. Pass ``symbols=AAPL,NVDA,MSFT`` (max 50)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    syms = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols query parameter is required (comma-separated)")
    scores = await compute_smart_money_scores_batch(db, syms)
    return {"scores": scores, "count": len(scores)}


@router.get("/smart-money-history/{symbol}")
async def smart_money_history(symbol: str, days: int = 30) -> dict:
    """Return daily Smart Money Score history (up to ``days`` days) for a symbol."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    cursor = db.smart_money_scores.find(
        {"symbol": symbol.upper()},
        {"_id": 0, "date": 1, "score": 1, "signal": 1, "bullish_count": 1, "bearish_count": 1},
    ).sort("date", -1).limit(min(days, 180))
    rows = await cursor.to_list(180)
    rows.reverse()  # chronological asc
    return {"symbol": symbol.upper(), "history": rows, "count": len(rows)}


@router.post("/smart-money-scan")
async def smart_money_scan(request: Request, threshold: int = 10) -> dict:
    """Admin-only: snapshot Smart Money Scores for all watchlist symbols and
    emit alerts + VAPID pushes on regime shifts (``|Δ| >= threshold``)."""
    user = await get_current_user(request)
    if not user or user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    # Collect all watchlist symbols
    symbols: set[str] = set()
    async for wl in db.watchlists.find({}, {"_id": 0, "tickers": 1}):
        for t in (wl.get("tickers") or []):
            if isinstance(t, str):
                symbols.add(t.upper())
            elif isinstance(t, dict) and t.get("symbol"):
                symbols.add(t["symbol"].upper())
    if not symbols:
        return {"ok": True, "alerts_created": 0, "symbols_scanned": 0}
    alerts = await detect_smart_money_shifts(db, sorted(symbols), threshold=threshold)
    return {"ok": True, "alerts_created": alerts, "symbols_scanned": len(symbols)}
