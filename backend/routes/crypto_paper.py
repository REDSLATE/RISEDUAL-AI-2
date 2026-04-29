"""Crypto Paper Trading Routes — isolated from equity paper-trading.

Endpoints
---------
* ``POST /api/crypto/paper-trade``  — open / close a crypto paper fill
* ``GET  /api/crypto/paper-trades`` — recent fill history (own user)
* ``GET  /api/crypto/paper-positions`` — mark-to-market open positions

These routes write exclusively to the ``crypto_paper_trades`` Mongo
collection. The legacy ``paper_trades`` collection (equities/options
AI fills) is never touched here. Stock-side lifecycle services
(``paper_trade_closer``, ``prediction_labeler``, etc.) cannot reach
this collection by design.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.auth_helpers import get_current_user
from services import crypto_paper_trading_service as cps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crypto", tags=["crypto-paper-trading"])


# ── DB wiring (called by route_registry) ──────────────────────────────────────


def set_db(database: Any) -> None:
    cps.set_db(database)


# ── Request models ────────────────────────────────────────────────────────────


class CryptoPaperTradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12,
                        description="Crypto ticker (BTC, ETH, …)")
    side: str = Field(..., pattern="^(?i)(BUY|SELL)$",
                      description="BUY or SELL")
    qty: float = Field(..., gt=0,
                       description="Base-asset units (e.g. 0.005 BTC)")
    stop_loss: Optional[float] = Field(default=None, ge=0)
    take_profit: Optional[float] = Field(default=None, ge=0)
    source: str = Field(default="manual_ui", max_length=32)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/paper-trade")
async def post_crypto_paper_trade(
    payload: CryptoPaperTradeRequest, request: Request,
) -> dict:
    """Open or close a crypto paper trade.

    Returns 400 with a structured ``detail`` block when:
      * symbol is a TEST_/MOCK_/FAKE_ fixture (test-contamination guard),
      * symbol isn't on the canonical crypto registry,
      * side or qty is invalid,
      * the upstream crypto quote is unavailable.

    Otherwise 200 with the persisted trade record. Idempotent retries
    within the same minute collapse onto the original ``trade_id``.

    Mode guard: caller must be in PAPER trading mode.
    """
    from services.trading_mode_guards import require_paper_mode
    user = await require_paper_mode(request)
    user_id = str(user.get("_id") or user.get("user_id") or "")

    result = await cps.execute_crypto_paper_trade(
        user_id=user_id,
        symbol=payload.symbol,
        side=payload.side,
        qty=payload.qty,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        source=payload.source,
    )

    if result.get("blocked"):
        raise HTTPException(status_code=400, detail=result)
    if result.get("status") == "rejected":
        raise HTTPException(
            status_code=400,
            detail={"error": result.get("error", "Trade rejected")},
        )
    return result


@router.get("/paper-trades")
async def get_crypto_paper_history(
    request: Request,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Most recent crypto paper-trade fills for the authenticated user."""
    user = await get_current_user(request)
    user_id = str(user.get("_id") or user.get("user_id") or "")
    rows = await cps.get_crypto_paper_history(
        user_id, symbol=symbol, limit=limit,
    )
    return {"trades": rows, "count": len(rows)}


@router.get("/paper-positions")
async def get_crypto_paper_positions(request: Request) -> dict:
    """Open crypto positions with live mark-to-market PnL."""
    user = await get_current_user(request)
    user_id = str(user.get("_id") or user.get("user_id") or "")
    return await cps.get_crypto_paper_position_summary(user_id)
