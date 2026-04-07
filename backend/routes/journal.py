"""Trading Journal routes: trade CRUD, analytics, hypothesis attachment."""
import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from services.auth_helpers import get_current_user, is_pro_user

router = APIRouter(prefix="/api/journal")

db = None
FREE_TRADE_LIMIT = 5

def set_db(database):
    global db
    db = database


class TradeCreate(BaseModel):
    ticker: str
    side: str  # "buy" or "sell"
    entry_price: float
    quantity: float
    entry_date: str
    notes: str = ""
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None


class TradeUpdate(BaseModel):
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    notes: Optional[str] = None


def trade_response(doc: dict) -> dict:
    pnl = 0
    pnl_pct = 0
    status = "open"
    if doc.get("exit_price") is not None and doc.get("entry_price"):
        if doc["side"] == "buy":
            pnl = (doc["exit_price"] - doc["entry_price"]) * doc["quantity"]
            pnl_pct = ((doc["exit_price"] - doc["entry_price"]) / doc["entry_price"]) * 100
        else:
            pnl = (doc["entry_price"] - doc["exit_price"]) * doc["quantity"]
            pnl_pct = ((doc["entry_price"] - doc["exit_price"]) / doc["entry_price"]) * 100
        status = "closed"
    return {
        "id": str(doc["_id"]),
        "ticker": doc["ticker"],
        "side": doc["side"],
        "entry_price": doc["entry_price"],
        "exit_price": doc.get("exit_price"),
        "quantity": doc["quantity"],
        "entry_date": doc.get("entry_date", ""),
        "exit_date": doc.get("exit_date"),
        "notes": doc.get("notes", ""),
        "status": status,
        "pnl": round(pnl, 2),
        "pnl_percent": round(pnl_pct, 2),
        "hypothesis": doc.get("hypothesis"),
        "created_at": doc.get("created_at", ""),
    }


@router.post("/trade")
async def create_trade(req: TradeCreate, request: Request):
    """Log a new trade."""
    user = await get_current_user(request)
    user_id = user["_id"]

    # Free user trade limit
    if not is_pro_user(user):
        count = await db.trades.count_documents({"user_id": user_id})
        if count >= FREE_TRADE_LIMIT:
            raise HTTPException(status_code=403, detail=f"Free accounts limited to {FREE_TRADE_LIMIT} trades. Upgrade to Pro for unlimited.")

    doc = {
        "user_id": user_id,
        "ticker": req.ticker.upper().strip(),
        "side": req.side.lower(),
        "entry_price": req.entry_price,
        "exit_price": req.exit_price,
        "quantity": req.quantity,
        "entry_date": req.entry_date,
        "exit_date": req.exit_date,
        "notes": req.notes,
        "hypothesis": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.trades.insert_one(doc)
    doc["_id"] = result.inserted_id
    return trade_response(doc)


@router.get("/trades")
async def get_trades(request: Request, skip: int = 0, limit: int = 50):
    """Get user's trades, newest first."""
    user = await get_current_user(request)
    cursor = db.trades.find({"user_id": user["_id"]}).sort("created_at", -1).skip(skip).limit(limit)
    trades = []
    async for doc in cursor:
        trades.append(trade_response(doc))
    total = await db.trades.count_documents({"user_id": user["_id"]})
    return {
        "trades": trades,
        "total": total,
        "limit": FREE_TRADE_LIMIT if not is_pro_user(user) else -1,
    }


@router.put("/trade/{trade_id}")
async def update_trade(trade_id: str, req: TradeUpdate, request: Request):
    """Update a trade (close it, add notes)."""
    user = await get_current_user(request)
    trade = await db.trades.find_one({"_id": ObjectId(trade_id), "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    updates = {}
    if req.exit_price is not None:
        updates["exit_price"] = req.exit_price
    if req.exit_date is not None:
        updates["exit_date"] = req.exit_date
    if req.notes is not None:
        updates["notes"] = req.notes

    if updates:
        await db.trades.update_one({"_id": ObjectId(trade_id)}, {"$set": updates})

    updated = await db.trades.find_one({"_id": ObjectId(trade_id)})
    return trade_response(updated)


@router.delete("/trade/{trade_id}")
async def delete_trade(trade_id: str, request: Request):
    """Delete a trade."""
    user = await get_current_user(request)
    result = await db.trades.delete_one({"_id": ObjectId(trade_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"message": "Trade deleted"}


@router.post("/trade/{trade_id}/attach-hypothesis")
async def attach_hypothesis(trade_id: str, request: Request):
    """Attach the latest AI hypothesis for the trade's ticker."""
    user = await get_current_user(request)
    trade = await db.trades.find_one({"_id": ObjectId(trade_id), "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Find latest hypothesis for this ticker
    hypothesis = await db.hypothesis_history.find_one(
        {"user_id": user["_id"], "ticker": trade["ticker"]},
        sort=[("created_at", -1)]
    )
    if not hypothesis:
        raise HTTPException(status_code=404, detail=f"No AI hypothesis found for {trade['ticker']}")

    hyp_data = {
        "verdict": hypothesis.get("verdict", "N/A"),
        "confidence": hypothesis.get("confidence", 0),
        "summary": hypothesis.get("summary", ""),
        "attached_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.trades.update_one({"_id": ObjectId(trade_id)}, {"$set": {"hypothesis": hyp_data}})

    updated = await db.trades.find_one({"_id": ObjectId(trade_id)})
    return trade_response(updated)


def _calculate_win_rate(closed: list) -> dict:
    """Calculate win/loss statistics from closed trades."""
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    return {
        "total_pnl": round(sum(t["pnl"] for t in closed), 2),
        "win_rate": round((len(wins) / len(closed)) * 100, 1) if closed else 0,
        "avg_gain": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "best_trade": max(closed, key=lambda t: t["pnl"]),
        "worst_trade": min(closed, key=lambda t: t["pnl"]),
    }


def _aggregate_by_ticker(closed: list) -> dict:
    """Group closed trades by ticker with P&L and win count."""
    by_ticker = {}
    for t in closed:
        tk = t["ticker"]
        if tk not in by_ticker:
            by_ticker[tk] = {"pnl": 0, "trades": 0, "wins": 0}
        by_ticker[tk]["pnl"] = round(by_ticker[tk]["pnl"] + t["pnl"], 2)
        by_ticker[tk]["trades"] += 1
        if t["pnl"] > 0:
            by_ticker[tk]["wins"] += 1
    return by_ticker


def _build_pnl_timeline(closed: list) -> list:
    """Build cumulative P&L timeline sorted by exit date."""
    sorted_closed = sorted(closed, key=lambda t: t.get("exit_date") or t.get("entry_date") or "")
    cumulative = 0
    timeline = []
    for t in sorted_closed:
        cumulative += t["pnl"]
        timeline.append({
            "date": t.get("exit_date") or t.get("entry_date", ""),
            "pnl": round(t["pnl"], 2),
            "cumulative": round(cumulative, 2),
            "ticker": t["ticker"],
        })
    return timeline


@router.get("/analytics")
async def get_analytics(request: Request):
    """Get trading performance analytics."""
    user = await get_current_user(request)
    user_id = user["_id"]

    cursor = db.trades.find({"user_id": user_id}).limit(1000)
    trades = []
    async for doc in cursor:
        trades.append(trade_response(doc))

    closed = [t for t in trades if t["status"] == "closed"]
    open_trades = [t for t in trades if t["status"] == "open"]

    empty = {
        "total_trades": len(trades), "open_trades": len(open_trades), "closed_trades": 0,
        "total_pnl": 0, "win_rate": 0, "avg_gain": 0, "avg_loss": 0,
        "best_trade": None, "worst_trade": None, "by_ticker": {}, "pnl_timeline": [],
    }
    if not closed:
        return empty

    stats = _calculate_win_rate(closed)
    best = stats["best_trade"]
    worst = stats["worst_trade"]

    return {
        "total_trades": len(trades),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "total_pnl": stats["total_pnl"],
        "win_rate": stats["win_rate"],
        "avg_gain": stats["avg_gain"],
        "avg_loss": stats["avg_loss"],
        "best_trade": {"ticker": best["ticker"], "pnl": best["pnl"], "pnl_percent": best["pnl_percent"]},
        "worst_trade": {"ticker": worst["ticker"], "pnl": worst["pnl"], "pnl_percent": worst["pnl_percent"]},
        "by_ticker": _aggregate_by_ticker(closed),
        "pnl_timeline": _build_pnl_timeline(closed),
    }
