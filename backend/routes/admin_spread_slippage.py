"""Spread-watch + slippage-attribution admin routes.

Extracted from ``routes/admin.py``. Two read-only liquidity / cost
analytics surfaces that share the same operator mental model
("what is spread costing us, and is the market in stress?").

  * ``GET /api/admin/spread-watch``          — cross-asset live snapshot
  * ``GET /api/admin/slippage-attribution``  — closed-trade drag report

URLs unchanged. Owner-gated.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-spread-slippage"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _market_session_label() -> str:
    """Coarse US market session classifier — RTH / pre / post /
    closed. Pure UTC math, no holiday calendar."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sat / Sun
        return "closed"
    # RTH: 13:30–20:00 UTC (covers EST 9:30–16:00 with no DST guard
    # — the chip just informs the operator; precise calendar logic
    # lives in services.kraken_equity_shadow_service).
    minutes = now.hour * 60 + now.minute
    if 13 * 60 + 30 <= minutes < 20 * 60:
        return "rth"
    if 8 * 60 <= minutes < 13 * 60 + 30:
        return "pre"
    if 20 * 60 <= minutes < 24 * 60:
        return "post"
    return "closed"


@router.get("/spread-watch")
async def spread_watch(
    request: Request,
    crypto_symbols: str = "BTC,ETH,SOL,DOGE,XRP",
    equity_symbols: str = "SPY,QQQ,AAPL,MSFT,NVDA",
    stress_threshold_bps: float = 25.0,
):
    """Cross-asset live spread snapshot.

    Pulls Kraken (crypto) + Alpaca (equity) in parallel, normalises
    the response shape, and flags any symbol whose ``spread_bps``
    exceeds ``stress_threshold_bps`` as a liquidity-stress signal.

    For equities, post-close one-sided quotes (``spread_bps=null``)
    are explicitly NOT counted as stress — that's the normal AH
    state. The frontend can use ``market_session`` to gate the
    "WIDE" pill so wide AH spreads don't trigger false alarms.
    """
    await _require_owner(request)

    from services.kraken_crypto_quotes import fetch_kraken_quotes_batch
    from services.alpaca_equity_quotes import fetch_alpaca_equity_quotes_batch

    crypto_list = [s.strip() for s in crypto_symbols.split(",") if s.strip()]
    equity_list = [s.strip() for s in equity_symbols.split(",") if s.strip()]

    crypto_task = fetch_kraken_quotes_batch(crypto_list) if crypto_list else None
    equity_task = fetch_alpaca_equity_quotes_batch(equity_list) if equity_list else None

    crypto_out: dict = {}
    equity_out: dict = {}
    if crypto_task is not None and equity_task is not None:
        crypto_out, equity_out = await asyncio.gather(crypto_task, equity_task)
    elif crypto_task is not None:
        crypto_out = await crypto_task
    elif equity_task is not None:
        equity_out = await equity_task

    session = _market_session_label()

    def _row(lane: str, symbol: str, q: dict) -> dict:
        spread = q.get("spread_bps")
        stressed = (
            spread is not None
            and lane == "crypto" or (lane == "equity" and session == "rth")
        ) and (spread is not None and spread > stress_threshold_bps)
        return {
            "lane": lane,
            "symbol": symbol,
            "price": q.get("price"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "last": q.get("last"),
            "spread_bps": spread,
            "source": q.get("source"),
            "stressed": bool(stressed),
        }

    rows: list[dict] = []
    for sym in crypto_list:
        q = crypto_out.get(sym.upper())
        if q is not None:
            rows.append(_row("crypto", sym.upper(), q))
    for sym in equity_list:
        q = equity_out.get(sym.upper())
        if q is not None:
            rows.append(_row("equity", sym.upper(), q))

    stressed_count = sum(1 for r in rows if r["stressed"])
    return {
        "rows": rows,
        "row_count": len(rows),
        "stressed_count": stressed_count,
        "market_session": session,
        "stress_threshold_bps": stress_threshold_bps,
        "missing_crypto": [
            s for s in crypto_list if s.upper() not in crypto_out
        ],
        "missing_equity": [
            s for s in equity_list if s.upper() not in equity_out
        ],
    }


@router.get("/slippage-attribution")
async def slippage_attribution_summary(
    request: Request, lane: str = "all", lookback_days: int = 30,
):
    """Aggregate realised slippage cost across recently closed
    trades. Returns per-lane and per-method roll-ups so the
    operator can see what the spread is taking from the strategy.

    * ``lane`` ∈ ``equity`` / ``crypto`` / ``all``
    * ``lookback_days`` window applies to ``closed_at``
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    from services.post_trade_autopsy import build_post_trade_autopsy

    lane_norm = (lane or "all").strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))

    colls: list[tuple[str, str]] = []
    if lane_norm in ("equity", "all"):
        colls.append(("paper_trades", "equity"))
    if lane_norm in ("crypto", "all"):
        colls.append(("crypto_paper_trades", "crypto"))

    aggregates: dict[str, dict] = {}
    by_method: dict[str, dict] = {}
    grand_pnl = 0.0
    grand_drag = 0.0
    grand_count = 0
    rows_with_slippage = 0

    for coll, label in colls:
        cursor = db[coll].find({
            "status": "closed",
            "closed_at": {"$gte": cutoff},
        }, {"_id": 0})
        async for r in cursor:
            grand_count += 1
            pnl = float(r.get("pnl_usd", r.get("pnl", 0.0)) or 0.0)
            grand_pnl += pnl
            slippage = (r.get("autopsy") or {}).get("slippage")
            if slippage is None:
                # Compute on-the-fly for rows that predate the
                # autopsy stamp.
                slippage = build_post_trade_autopsy(r).get("slippage")
            if slippage is None:
                continue
            rows_with_slippage += 1
            cost = float(slippage.get("total_dollar_cost") or 0.0)
            grand_drag += cost
            method = slippage.get("fill_method") or "unknown"
            method_row = by_method.setdefault(
                method,
                {"count": 0, "dollar_cost": 0.0, "bps_sum": 0.0},
            )
            method_row["count"] += 1
            method_row["dollar_cost"] += cost
            method_row["bps_sum"] += float(slippage.get("total_bps") or 0.0)
            lane_row = aggregates.setdefault(
                label,
                {"count": 0, "dollar_cost": 0.0, "bps_sum": 0.0, "pnl": 0.0},
            )
            lane_row["count"] += 1
            lane_row["dollar_cost"] += cost
            lane_row["bps_sum"] += float(slippage.get("total_bps") or 0.0)
            lane_row["pnl"] += pnl

    def _finalise(rows: dict) -> dict:
        out: dict = {}
        for k, v in rows.items():
            n = max(v["count"], 1)
            out[k] = {
                "count": v["count"],
                "dollar_cost": round(v["dollar_cost"], 2),
                "avg_bps": round(v["bps_sum"] / n, 2),
            }
            if "pnl" in v:
                out[k]["pnl"] = round(v["pnl"], 2)
                # Drag % = drag / |pnl| × 100 (capped, useful only
                # when there's some pnl to compare against).
                pnl_abs = abs(v["pnl"]) or 1.0
                out[k]["drag_pct_of_abs_pnl"] = round(
                    (v["dollar_cost"] / pnl_abs) * 100.0, 2,
                )
        return out

    return {
        "lane_filter": lane_norm,
        "lookback_days": lookback_days,
        "totals": {
            "trades": grand_count,
            "trades_with_slippage_stamp": rows_with_slippage,
            "total_pnl_usd": round(grand_pnl, 2),
            "total_slippage_drag_usd": round(grand_drag, 2),
            "drag_pct_of_abs_pnl": round(
                (grand_drag / (abs(grand_pnl) or 1.0)) * 100.0, 2,
            ),
            "avg_bps_per_trade": (
                round(
                    sum(v["bps_sum"] for v in by_method.values())
                    / max(rows_with_slippage, 1),
                    2,
                )
            ),
        },
        "by_lane": _finalise(aggregates),
        "by_method": _finalise(by_method),
    }
