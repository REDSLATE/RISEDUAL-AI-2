"""Admin MooMoo — read-only status + entitlement query.

Never returns or accepts credentials. All secrets live in OpenD's
own config file or the process environment set by the runtime secret
provider — never in this route, never in the request body.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Query

from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api/admin/moomoo", tags=["admin-moomoo"])
db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/status")
async def status(request: Request):
    await _require_admin(request)
    from services import broker_router
    return broker_router.status()


@router.get("/entitlements")
async def entitlements(request: Request):
    await _require_admin(request)
    from services.moomoo_market_data_adapter import entitlements as _ent
    return {"entitlements": _ent()}


@router.get("/quote/{symbol}")
async def quote(request: Request, symbol: str):
    await _require_admin(request)
    from services.moomoo_market_data_adapter import snapshot_quote
    q = snapshot_quote(symbol)
    if q is None:
        return {"available": False, "reason": "opend_unreachable_or_no_entitlement"}
    return {"available": True, "quote": q.__dict__}


@router.get("/order-book/{symbol}")
async def order_book(request: Request, symbol: str):
    await _require_admin(request)
    from services.moomoo_market_data_adapter import to_level2_snapshot
    l2 = to_level2_snapshot(symbol)
    if l2 is None:
        return {"available": False, "reason": "no_depth_or_entitlement"}
    return {"available": True, "level2": l2}


@router.get("/account")
async def account(request: Request):
    await _require_admin(request)
    from services.moomoo_broker_adapter import account_info, positions, orders, fills
    return {
        "account": account_info(),
        "positions": positions(),
        "orders": orders(),
        "fills": fills(),
    }


@router.get("/broker-comparison")
async def broker_comparison(request: Request, limit: int = Query(50, ge=1, le=500)):
    """Compact broker-comparison telemetry rows from SQLite."""
    await _require_admin(request)
    import sqlite3
    from services import alpha_hot_store
    try:
        alpha_hot_store.init()
        path = alpha_hot_store._path()  # type: ignore[attr-defined]
    except Exception:
        return {"rows": []}
    try:
        with sqlite3.connect(path, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT broker, client_order_id, broker_order_id, symbol, side, qty, "
                "limit_price, submit_latency_ms, ack_latency_ms, fill_latency_ms, "
                "fill_price, slippage_bps, status, error, ts_ns "
                "FROM broker_comparison ORDER BY ts_ns DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return {"rows": [dict(r) for r in rows]}
    except Exception:
        return {"rows": []}
