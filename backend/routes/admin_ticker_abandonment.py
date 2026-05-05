"""Ticker abandonment admin endpoints.

Extracted from ``routes/admin.py`` — the gate's per-symbol +
bulk-overview surface, plus its same-day snapshot persistence so
tomorrow's overview can render Δ-since-yesterday badges without
operator intervention.

  * ``GET /api/admin/ticker-abandonment/{symbol}`` — single-symbol probe
  * ``GET /api/admin/ticker-abandonment``           — bulk overview

URLs unchanged — no frontend / test edits required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-ticker-abandonment"])
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


def _serialize_abandonment_row(lane: str, symbol: str, inputs, decision) -> dict:
    last_p = inputs.last_profitable_at
    return {
        "lane": lane,
        "symbol": symbol,
        "inputs": {
            "recent_signals": inputs.recent_signals,
            "recent_rejections": inputs.recent_rejections,
            "recent_wins": inputs.recent_wins,
            "recent_losses": inputs.recent_losses,
            "avg_confidence": round(inputs.avg_confidence, 4),
            "avg_rr": round(inputs.avg_rr, 4),
            "last_profitable_at": last_p.isoformat() if last_p else None,
        },
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "cooldown_minutes": decision.cooldown_minutes,
        },
    }


@router.get("/ticker-abandonment/{symbol}")
async def ticker_abandonment_status(
    request: Request, symbol: str, lane: str = "equity",
):
    """Inspect the ticker-abandonment gate's current decision for a
    symbol. Inform-only — the gate runs on every paper-trade tick
    and this endpoint just shows what the gate would say right now.

    ``lane`` query param: ``"equity"`` (default) or ``"crypto"``.
    Returns the resolved inputs the gate read + the
    ``TickerExitDecision`` action / reason / cooldown_minutes.
    """
    await _require_owner(request)
    from services.ticker_abandonment import decide_ticker_exit
    from services.ticker_abandonment_stats import (
        compute_crypto_inputs, compute_equity_inputs,
    )
    sym = symbol.upper()
    if lane.lower() == "crypto":
        inputs = await compute_crypto_inputs(db, sym)
    else:
        inputs = await compute_equity_inputs(db, sym)
    decision = decide_ticker_exit(
        symbol=sym,
        recent_signals=inputs.recent_signals,
        recent_rejections=inputs.recent_rejections,
        recent_losses=inputs.recent_losses,
        recent_wins=inputs.recent_wins,
        avg_confidence=inputs.avg_confidence,
        avg_rr=inputs.avg_rr,
        last_profitable_at=inputs.last_profitable_at,
    )
    last_p = inputs.last_profitable_at
    return {
        "symbol": sym,
        "lane": lane.lower(),
        "window_days": inputs.window_days,
        "inputs": {
            "recent_signals": inputs.recent_signals,
            "recent_rejections": inputs.recent_rejections,
            "recent_wins": inputs.recent_wins,
            "recent_losses": inputs.recent_losses,
            "avg_confidence": round(inputs.avg_confidence, 4),
            "avg_rr": round(inputs.avg_rr, 4),
            "last_profitable_at": last_p.isoformat() if last_p else None,
        },
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "cooldown_minutes": decision.cooldown_minutes,
        },
    }


@router.get("/ticker-abandonment")
async def ticker_abandonment_overview(request: Request):
    """Bulk view of the ticker-abandonment gate's current decision
    across every ticker that has at least one row in the last
    ``TICKER_ABANDONMENT_WINDOW_DAYS`` for either lane.

    Discovery: scans ``paper_trades`` (equity) and
    ``crypto_paper_trades`` (crypto) for distinct symbols opened
    within the rolling window, plus distinct symbols from
    ``agent_activity`` ``paper_trade_*`` events (catches symbols
    that ONLY get rejections, never fills — which is exactly the
    high-rejection-rate cooldown bucket).

    Returns rows sorted: ABANDON first, then COOLDOWN by descending
    ``cooldown_minutes``, then KEEP. Single-glance scan for the
    operator. Inform-only.
    """
    await _require_owner(request)

    from services.ticker_abandonment import decide_ticker_exit
    from services.ticker_abandonment_stats import (
        RECENT_WINDOW_DAYS, compute_crypto_inputs, compute_equity_inputs,
    )

    since = datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
    equity_symbols: set[str] = set()
    crypto_symbols: set[str] = set()

    try:
        for sym in await db["paper_trades"].distinct(
            "ticker", {"opened_at": {"$gte": since}},
        ):
            if sym:
                equity_symbols.add(str(sym).upper())
    except Exception:
        pass

    try:
        for sym in await db["crypto_paper_trades"].distinct(
            "symbol", {"opened_at": {"$gte": since}},
        ):
            if sym:
                crypto_symbols.add(str(sym).upper())
    except Exception:
        pass

    # Discover symbols that ONLY had rejections — high-rejection-rate
    # cooldown candidates won't show in paper_trades at all.
    try:
        for sym in await db["agent_activity"].distinct(
            "symbol",
            {
                "type": {"$in": ["paper_trade_open", "paper_trade_skip"]},
                "created_at": {"$gte": since},
            },
        ):
            if not sym:
                continue
            s = str(sym).upper()
            if s in {
                "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE",
                "AVAX", "LINK", "DOT", "MATIC", "BNB",
            }:
                crypto_symbols.add(s)
            else:
                equity_symbols.add(s)
    except Exception:
        pass

    rows: list[dict] = []
    for sym in sorted(equity_symbols):
        inputs = await compute_equity_inputs(db, sym)
        decision = decide_ticker_exit(
            symbol=sym,
            recent_signals=inputs.recent_signals,
            recent_rejections=inputs.recent_rejections,
            recent_losses=inputs.recent_losses,
            recent_wins=inputs.recent_wins,
            avg_confidence=inputs.avg_confidence,
            avg_rr=inputs.avg_rr,
            last_profitable_at=inputs.last_profitable_at,
        )
        rows.append(
            _serialize_abandonment_row("equity", sym, inputs, decision),
        )

    for sym in sorted(crypto_symbols):
        inputs = await compute_crypto_inputs(db, sym)
        decision = decide_ticker_exit(
            symbol=sym,
            recent_signals=inputs.recent_signals,
            recent_rejections=inputs.recent_rejections,
            recent_losses=inputs.recent_losses,
            recent_wins=inputs.recent_wins,
            avg_confidence=inputs.avg_confidence,
            avg_rr=inputs.avg_rr,
            last_profitable_at=inputs.last_profitable_at,
        )
        rows.append(
            _serialize_abandonment_row("crypto", sym, inputs, decision),
        )

    # ── Δ-since-yesterday + same-day snapshot persistence ─────────
    # For each row, look up the most recent prior snapshot's action
    # (strictly < today's UTC date) and stamp the delta envelope.
    # Then upsert today's snapshot so tomorrow's call has yesterday
    # to compare against. Idempotent — re-running same day is a
    # no-op-write of the same fields.
    from services.ticker_abandonment_history import (
        compute_action_delta, fetch_prior_action, write_snapshot,
    )
    for r in rows:
        try:
            prior = await fetch_prior_action(
                db, lane=r["lane"], symbol=r["symbol"],
            )
        except Exception:
            prior = None
        r["delta"] = compute_action_delta(r["decision"]["action"], prior)
        try:
            await write_snapshot(
                db,
                lane=r["lane"],
                symbol=r["symbol"],
                action=r["decision"]["action"],
                reason=r["decision"]["reason"],
                cooldown_minutes=r["decision"].get("cooldown_minutes") or 0,
                inputs_view=r["inputs"],
            )
        except Exception:
            pass  # snapshot write must never break the read endpoint

    # Sort: ABANDON first, COOLDOWN by descending cooldown_minutes,
    # then KEEP alphabetical.
    _action_rank = {"ABANDON": 0, "COOLDOWN": 1, "KEEP": 2}
    rows.sort(key=lambda r: (
        _action_rank.get(r["decision"]["action"], 99),
        -int(r["decision"].get("cooldown_minutes") or 0),
        r["symbol"],
    ))

    return {
        "window_days": RECENT_WINDOW_DAYS,
        "rows": rows,
        "totals": {
            "abandon": sum(
                1 for r in rows if r["decision"]["action"] == "ABANDON"
            ),
            "cooldown": sum(
                1 for r in rows if r["decision"]["action"] == "COOLDOWN"
            ),
            "keep": sum(
                1 for r in rows if r["decision"]["action"] == "KEEP"
            ),
        },
    }
