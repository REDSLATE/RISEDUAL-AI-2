"""Broker connection management routes — per-user API key storage & trading."""
import os
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from bson import ObjectId
from cryptography.fernet import Fernet
import base64
import hashlib

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])

db = None

def set_db(database):
    global db
    db = database


# --- Encryption helpers (derive Fernet key from JWT_SECRET) ---
def _get_fernet():
    secret = os.environ.get("JWT_SECRET", "fallback-secret-key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)

def encrypt_value(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt_value(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# --- Request Models ---
class ConnectBrokerRequest(BaseModel):
    broker_id: str  # 'alpaca', 'schwab', 'ibkr', etc.
    api_key: str
    api_secret: str
    paper: bool = True  # Paper trading mode (Alpaca)

class PlaceOrderRequest(BaseModel):
    symbol: str
    quantity: float
    side: str  # 'buy' or 'sell'
    order_type: str = "market"  # 'market', 'limit', 'stop', 'stop_limit'
    time_in_force: str = "day"  # 'day', 'gtc', 'ioc'
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


# --- Auth helper (inline to avoid circular imports) ---
async def _get_user(request: Request) -> dict:
    from routes.auth import get_current_user
    return await get_current_user(request)


# --- Helper: get user's broker credentials from DB ---
async def _get_user_broker(user_id: str, broker_id: str) -> dict:
    conn = await db.broker_connections.find_one(
        {"user_id": user_id, "broker_id": broker_id, "is_active": True},
        {"_id": 0}
    )
    if not conn:
        raise HTTPException(status_code=404, detail=f"No active {broker_id} connection found. Please connect your broker first.")
    return conn


def _build_client(conn: dict):
    """Build a broker client from stored connection data."""
    from services.broker_service import BrokerService
    api_key = decrypt_value(conn["api_key_enc"])
    api_secret = decrypt_value(conn["api_secret_enc"])
    broker_id = conn["broker_id"]
    credentials = {
        "api_key": api_key,
        "api_secret": api_secret,
        "paper": conn.get("paper", True),
    }
    return BrokerService.get_broker_client(broker_id, credentials)


# ============================================================
# BROKER CONNECTION CRUD
# ============================================================

@router.post("/connect")
async def connect_broker(req: ConnectBrokerRequest, request: Request):
    """Connect a broker by saving encrypted API keys and validating the connection."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    # Build a temporary client to validate credentials
    from services.broker_service import BrokerService
    credentials = {"api_key": req.api_key, "api_secret": req.api_secret, "paper": req.paper}
    try:
        client = BrokerService.get_broker_client(req.broker_id, credentials)
        account = await asyncio.to_thread(client.get_account)
        if not account:
            raise HTTPException(status_code=400, detail="Could not authenticate with broker. Please check your API keys.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Broker connect validation error: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

    # Encrypt and store
    doc = {
        "user_id": user_id,
        "broker_id": req.broker_id,
        "api_key_enc": encrypt_value(req.api_key),
        "api_secret_enc": encrypt_value(req.api_secret),
        "paper": req.paper,
        "is_active": True,
        "account_id": account.get("account_number", account.get("id", "N/A")),
        "connected_at": datetime.now(timezone.utc),
        "last_used": datetime.now(timezone.utc),
    }

    # Upsert (replace existing connection for same broker)
    await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": req.broker_id},
        {"$set": doc},
        upsert=True,
    )

    return {
        "status": "connected",
        "broker_id": req.broker_id,
        "account_id": doc["account_id"],
        "paper": req.paper,
        "message": f"Successfully connected to {req.broker_id}",
    }


@router.get("/connections")
async def list_connections(request: Request):
    """List all broker connections for the authenticated user."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    cursor = db.broker_connections.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "api_key_enc": 0, "api_secret_enc": 0}
    )
    connections = []
    async for c in cursor:
        connections.append(c)
    return {"connections": connections}


@router.delete("/disconnect/{broker_id}")
async def disconnect_broker(broker_id: str, request: Request):
    """Disconnect (soft-delete) a broker connection."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    result = await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {"is_active": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disconnected", "broker_id": broker_id}


# ============================================================
# ACCOUNT & POSITIONS
# ============================================================

@router.get("/account/{broker_id}")
async def get_account(broker_id: str, request: Request):
    """Get account info for a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)
    account = await asyncio.to_thread(client.get_account)
    if not account:
        raise HTTPException(status_code=502, detail="Failed to fetch account from broker")

    # Update last_used
    await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {"last_used": datetime.now(timezone.utc)}}
    )

    return {
        "broker": broker_id,
        "account_id": account.get("account_number", account.get("id", "N/A")),
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "portfolio_value": float(account.get("portfolio_value", account.get("equity", 0))),
        "equity": float(account.get("equity", 0)),
        "status": account.get("status", "active"),
        "paper": conn.get("paper", True),
    }


@router.get("/positions/{broker_id}")
async def get_positions(broker_id: str, request: Request):
    """Get all positions for a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)
    positions = await asyncio.to_thread(client.get_positions)
    formatted = []
    for p in (positions or []):
        formatted.append({
            "symbol": p.get("symbol", ""),
            "qty": float(p.get("qty", 0)),
            "side": p.get("side", "long"),
            "avg_entry_price": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
        })
    return {"positions": formatted, "total": len(formatted)}


# ============================================================
# ORDER MANAGEMENT
# ============================================================

@router.post("/order/{broker_id}")
async def place_order(broker_id: str, req: PlaceOrderRequest, request: Request):
    """Place a trade order through a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)

    result = await asyncio.to_thread(
        client.place_order,
        symbol=req.symbol,
        qty=req.quantity,
        side=req.side,
        order_type=req.order_type,
        time_in_force=req.time_in_force,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Order rejected by broker")

    # Log order to MongoDB for history
    await db.trade_orders.insert_one({
        "user_id": user_id,
        "broker_id": broker_id,
        "broker_order_id": result.get("id", ""),
        "symbol": req.symbol.upper(),
        "side": req.side,
        "qty": req.quantity,
        "order_type": req.order_type,
        "status": result.get("status", "submitted"),
        "limit_price": req.limit_price,
        "stop_price": req.stop_price,
        "created_at": datetime.now(timezone.utc),
    })

    return {
        "status": result.get("status", "submitted"),
        "order_id": result.get("id", ""),
        "symbol": req.symbol.upper(),
        "side": req.side,
        "qty": req.quantity,
        "type": req.order_type,
    }


@router.get("/orders/{broker_id}")
async def get_orders(broker_id: str, request: Request, status: str = "all"):
    """Get order history from a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)
    orders = await asyncio.to_thread(client.get_orders, status=status)
    formatted = []
    for o in (orders or []):
        formatted.append({
            "id": o.get("id", ""),
            "symbol": o.get("symbol", ""),
            "side": o.get("side", ""),
            "qty": o.get("qty", 0),
            "type": o.get("type", ""),
            "status": o.get("status", ""),
            "filled_qty": o.get("filled_qty", 0),
            "filled_avg_price": o.get("filled_avg_price"),
            "submitted_at": o.get("submitted_at", ""),
            "created_at": o.get("created_at", ""),
        })
    return {"orders": formatted, "total": len(formatted)}


@router.delete("/order/{broker_id}/{order_id}")
async def cancel_order(broker_id: str, order_id: str, request: Request):
    """Cancel a pending order."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)
    success = await asyncio.to_thread(client.cancel_order, order_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to cancel order")
    return {"status": "cancelled", "order_id": order_id}


# ============================================================
# PORTFOLIO SYNC
# ============================================================

@router.get("/portfolio-sync/{broker_id}")
async def portfolio_sync(broker_id: str, request: Request):
    """Sync broker positions with RISEDUAL AI's strategy and watchlist system."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)

    account = await asyncio.to_thread(client.get_account)
    positions = await asyncio.to_thread(client.get_positions)
    orders = await asyncio.to_thread(client.get_orders, status="all")

    # Build portfolio summary
    position_symbols = [p.get("symbol", "") for p in (positions or [])]
    total_value = float(account.get("portfolio_value", account.get("equity", 0))) if account else 0
    total_pl = sum(float(p.get("unrealized_pl", 0)) for p in (positions or []))

    # Sync symbols to user's watchlist
    if position_symbols:
        existing = await db.watchlists.find_one({"user_id": user_id})
        if existing:
            current_symbols = set(existing.get("symbols", []))
            new_symbols = list(current_symbols | set(position_symbols))
            await db.watchlists.update_one(
                {"user_id": user_id},
                {"$set": {"symbols": new_symbols, "updated_at": datetime.now(timezone.utc)}}
            )
        else:
            await db.watchlists.insert_one({
                "user_id": user_id,
                "symbols": position_symbols,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            })

    # Store portfolio snapshot
    await db.portfolio_snapshots.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {
            "user_id": user_id,
            "broker_id": broker_id,
            "positions": [{
                "symbol": p.get("symbol", ""),
                "qty": float(p.get("qty", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            } for p in (positions or [])],
            "total_value": total_value,
            "total_unrealized_pl": total_pl,
            "synced_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    return {
        "synced": True,
        "broker_id": broker_id,
        "positions_count": len(positions or []),
        "symbols_synced": position_symbols,
        "total_portfolio_value": total_value,
        "total_unrealized_pl": total_pl,
        "open_orders": len([o for o in (orders or []) if o.get("status") in ("new", "accepted", "pending_new")]),
    }


# ============================================================
# REAL-TIME P&L TRACKER
# ============================================================

@router.get("/pnl-summary")
async def get_pnl_summary(request: Request):
    """Aggregate real-time P&L across all connected brokers."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    cursor = db.broker_connections.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0}
    )
    connections = []
    async for c in cursor:
        connections.append(c)

    if not connections:
        return {
            "total_value": 0,
            "total_pl": 0,
            "total_pl_pct": 0,
            "day_pl": 0,
            "brokers": [],
            "positions": [],
            "sector_allocation": [],
        }

    brokers = []
    all_positions = []
    total_value = 0
    total_pl = 0
    total_cost = 0

    for conn in connections:
        try:
            client = _build_client(conn)
            account = await asyncio.to_thread(client.get_account)
            positions = await asyncio.to_thread(client.get_positions)

            broker_value = float(account.get("portfolio_value", account.get("equity", 0))) if account else 0
            broker_cash = float(account.get("cash", 0)) if account else 0
            broker_pl = 0

            for p in (positions or []):
                qty = float(p.get("qty", 0))
                avg_entry = float(p.get("avg_entry_price", 0))
                current = float(p.get("current_price", 0))
                mkt_val = float(p.get("market_value", 0))
                unrealized = float(p.get("unrealized_pl", 0))
                unrealized_pct = float(p.get("unrealized_plpc", 0))
                cost_basis = qty * avg_entry

                broker_pl += unrealized
                total_cost += cost_basis

                all_positions.append({
                    "symbol": p.get("symbol", ""),
                    "broker": conn["broker_id"],
                    "qty": qty,
                    "avg_entry": round(avg_entry, 2),
                    "current_price": round(current, 2),
                    "market_value": round(mkt_val, 2),
                    "unrealized_pl": round(unrealized, 2),
                    "unrealized_pl_pct": round(unrealized_pct * 100, 2),
                    "cost_basis": round(cost_basis, 2),
                    "side": p.get("side", "long"),
                })

            total_value += broker_value
            total_pl += broker_pl

            brokers.append({
                "broker_id": conn["broker_id"],
                "account_id": conn.get("account_id", ""),
                "paper": conn.get("paper", True),
                "portfolio_value": round(broker_value, 2),
                "cash": round(broker_cash, 2),
                "unrealized_pl": round(broker_pl, 2),
                "positions_count": len(positions or []),
            })
        except Exception as e:
            logger.warning(f"P&L fetch error for {conn['broker_id']}: {e}")
            brokers.append({
                "broker_id": conn["broker_id"],
                "error": str(e),
            })

    # Sector allocation (group positions by rough sector)
    sector_map = _classify_sectors(all_positions)

    total_pl_pct = round((total_pl / total_cost) * 100, 2) if total_cost > 0 else 0

    # Sort positions by absolute P&L (biggest movers first)
    all_positions.sort(key=lambda p: abs(p["unrealized_pl"]), reverse=True)

    return {
        "total_value": round(total_value, 2),
        "total_pl": round(total_pl, 2),
        "total_pl_pct": total_pl_pct,
        "total_cost_basis": round(total_cost, 2),
        "brokers": brokers,
        "positions": all_positions,
        "sector_allocation": sector_map,
        "positions_count": len(all_positions),
    }


def _classify_sectors(positions: list) -> list:
    """Simple sector classification based on well-known tickers."""
    sector_lookup = {
        "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology", "GOOG": "Technology",
        "AMZN": "Consumer Disc.", "TSLA": "Consumer Disc.", "NKE": "Consumer Disc.",
        "META": "Communication", "NFLX": "Communication", "DIS": "Communication",
        "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "V": "Financials",
        "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "ABBV": "Healthcare",
        "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
        "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
        "CAT": "Industrials", "BA": "Industrials", "HON": "Industrials",
        "NVDA": "Technology", "AMD": "Technology", "INTC": "Technology", "CRM": "Technology",
        "SPY": "Index", "QQQ": "Index", "IWM": "Index", "VOO": "Index",
    }

    sectors = {}
    for p in positions:
        sector = sector_lookup.get(p["symbol"], "Other")
        if sector not in sectors:
            sectors[sector] = {"name": sector, "value": 0, "pl": 0, "count": 0}
        sectors[sector]["value"] += p["market_value"]
        sectors[sector]["pl"] += p["unrealized_pl"]
        sectors[sector]["count"] += 1

    total = sum(s["value"] for s in sectors.values())
    result = []
    for s in sectors.values():
        s["pct"] = round((s["value"] / total) * 100, 1) if total > 0 else 0
        s["value"] = round(s["value"], 2)
        s["pl"] = round(s["pl"], 2)
        result.append(s)

    result.sort(key=lambda s: s["value"], reverse=True)
    return result
