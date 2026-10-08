"""Broker-authoritative position reconciliation.

Doctrine (operator, 2026-09)
----------------------------
* **Broker = truth about money, positions, orders and fills.**
* **Alpha = truth about strategy, signals, decisions and historical records.**

The duplicate-position veto used to trust Alpha's local ``equity_live_trades``
ledger over the broker. When the exit/reconcile path failed to write a close
back, a phantom ``status="open"`` row would linger forever and permanently
lock Alpha out of re-entering that symbol (``dup_open_row``). This module makes
the broker authoritative: if Public does not hold the position, any stale open
ledger row is reconciled (marked closed with ``broker_reconciled_missing``,
timestamped, original preserved) so audit/edge history stays intact.

Runs in three places for resilience: at startup, periodically in the tick
tail, and immediately before a duplicate-position veto.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Throttle the full sweep so the 60s stream tick doesn't hammer the
# broker positions endpoint. The per-symbol reconcile (before a veto)
# is never throttled — it must always reflect broker truth.
_FULL_RECONCILE_THROTTLE_S = 300.0
_last_full_reconcile_ns: int = 0


def _positions_map(positions: list) -> dict[str, float]:
    """Normalize a broker positions list into ``{SYMBOL: abs_qty}``."""
    out: dict[str, float] = {}
    for p in positions or []:
        try:
            sym = (p.get("symbol") or p.get("instrument") or "").upper()
            if not sym:
                continue
            qty = abs(float(p.get("qty") or p.get("quantity") or 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
        if qty > 0:
            out[sym] = out.get(sym, 0.0) + qty
    return out


async def _close_phantom_rows(db: Any, symbol: str) -> int:
    """Mark stale open ledger rows closed — preserve, never delete."""
    if db is None:
        return 0
    now = datetime.now(timezone.utc)
    try:
        res = await db.equity_live_trades.update_many(
            {"symbol": symbol, "status": "open", "broker_id": "public",
             "close_pending": {"$ne": True},
             "close_in_flight_at": {"$exists": False}},
            {"$set": {
                "status": "closed",
                "close_reason": "broker_reconciled_missing",
                "closed_at": now,
                "reconciled_at": now,
            }},
        )
        return int(res.modified_count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[position-reconciler] close phantom %s failed: %s", symbol, exc)
        return 0


async def reconcile_symbol(db: Any, client: Any, symbol: str) -> dict:
    """Broker-authoritative single-symbol reconcile.

    Returns ``{ok, held, qty, reconciled}``. ``ok=False`` when the broker
    could not answer — the caller MUST fail closed (never assume "not held"
    and open a blind position).
    """
    symbol = (symbol or "").upper()
    if client is None:
        return {"ok": False, "held": None, "qty": 0.0, "reconciled": 0}
    try:
        positions = client.get_positions(strict=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[position-reconciler] get_positions failed for %s: %s", symbol, exc)
        return {"ok": False, "held": None, "qty": 0.0, "reconciled": 0}
    held_qty = _positions_map(positions).get(symbol, 0.0)
    if held_qty > 0:
        return {"ok": True, "held": True, "qty": held_qty, "reconciled": 0}
    reconciled = await _close_phantom_rows(db, symbol)
    if reconciled:
        logger.info(
            "[position-reconciler] %s: broker holds 0 — reconciled %d phantom "
            "open row(s) to closed(broker_reconciled_missing)", symbol, reconciled,
        )
    return {"ok": True, "held": False, "qty": 0.0, "reconciled": reconciled}


async def reconcile_all(db: Any, client: Any) -> dict:
    """Sweep every open ledger row against broker truth. Startup/periodic."""
    if db is None or client is None:
        return {"ok": False, "checked": 0, "reconciled": 0, "held": []}
    try:
        positions = client.get_positions(strict=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[position-reconciler] full sweep get_positions failed: %s", exc)
        return {"ok": False, "checked": 0, "reconciled": 0, "held": []}
    held = _positions_map(positions)
    checked = 0
    reconciled_syms: list[str] = []
    try:
        cursor = db.equity_live_trades.find(
            {"status": "open", "broker_id": "public"}, {"symbol": 1},
        )
        seen: set[str] = set()
        async for doc in cursor:
            sym = (doc.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            checked += 1
            if sym not in held:
                n = await _close_phantom_rows(db, sym)
                if n:
                    reconciled_syms.append(sym)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[position-reconciler] full sweep iteration failed: %s", exc)
    if reconciled_syms:
        logger.info(
            "[position-reconciler] full sweep: %d symbol(s) reconciled (%s); "
            "broker holds %s", len(reconciled_syms), reconciled_syms,
            sorted(held.keys()),
        )
    return {"ok": True, "checked": checked, "reconciled": len(reconciled_syms),
            "held": sorted(held.keys()), "reconciled_symbols": reconciled_syms}


async def reconcile_open_positions_with_broker(
    db: Any, *, throttle: bool = True, force: bool = False,
) -> dict:
    """Resolve creds, build a Public client, run the full sweep.

    Best-effort. Throttled by default so the 60s stream tick can call it
    freely. ``force=True`` bypasses the throttle (startup / manual).
    """
    global _last_full_reconcile_ns
    if throttle and not force:
        now_ns = time.time_ns()
        if (now_ns - _last_full_reconcile_ns) < int(_FULL_RECONCILE_THROTTLE_S * 1e9):
            return {"ok": True, "skipped": "throttled"}
        _last_full_reconcile_ns = now_ns
    try:
        from services.public_equity_live_executor import (
            _aresolve_connect_creds, _public_client,
        )
        creds = await _aresolve_connect_creds(db)
        if not creds:
            return {"ok": False, "reason": "no_public_creds"}
        client = _public_client(creds[0], creds[1])
        from services.public_exit_lifecycle import reconcile_exits
        exits = await reconcile_exits(db, client)
        if not exits.get("ok"):
            return exits
        return await reconcile_all(db, client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[position-reconciler] reconcile_open_positions failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
