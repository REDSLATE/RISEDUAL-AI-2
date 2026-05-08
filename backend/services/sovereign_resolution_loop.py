"""
Sovereign AI — Resolution Loop (P1).

Scheduled job that back-patches ``sovereign_decisions.outcomes.{60m|4h|eod}``
by joining against the paper_trades (equity) and crypto_paper_trades tables
via the ``sovereign_decision_id`` link persisted on each trade row.

**Scope (per operator decision on 2026-05-03):**
Only resolves decisions that have a linked fired trade. Decisions with NO
matching trade (e.g. strategist HOLD so no trade fired) stay unresolved —
we evaluate sovereign on trades that actually fired. The promotion gate
math already factors this: the gate counts RESOLVED rows, so unresolved
shadow rows simply don't contribute to the 500-row threshold.

Fired-trade resolution logic:

* Find paper_trades (or crypto_paper_trades) that:
    - have ``sovereign_decision_id`` set
    - have ``status=="closed"``
    - have ``closed_at >= sovereign.created_at + horizon``
* Compute ``pnl_pct`` from ``trade.pnl_pct`` (persisted by the trade closer).
* Compute ``was_right`` = sovereign's action matched the realised direction
  of the P&L (LONG + positive P&L → right; SHORT + negative P&L → right;
  HOLD is never joined because a HOLD decision cannot link to a fired trade).
* Idempotent: ``resolve_sovereign_decision`` uses ``$set`` so re-running on
  the same (decision_id, horizon) pair is a no-op.

Runs every 15 min via APScheduler. Per-run batch cap (50 resolutions) so a
backlog can't starve the rest of the fleet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services.sovereign_ai_core import resolve_sovereign_decision

logger = logging.getLogger(__name__)

# Horizon → cutoff delta. EOD is approximated as 8 hours (typical cash
# session close from entry). Operator can tighten via env if needed.
_HORIZONS: dict[str, timedelta] = {
    "60m": timedelta(minutes=60),
    "4h": timedelta(hours=4),
    "eod": timedelta(hours=8),
}

BATCH_CAP: int = 50


def _was_right(action: str, pnl_pct: float) -> bool:
    """Sovereign directional verdict aligned with realised P&L sign."""
    from services.prediction_tracker import canonical_ai_dir
    canon = canonical_ai_dir(action)
    if canon == "LONG":
        return pnl_pct > 0
    if canon == "SHORT":
        return pnl_pct < 0
    # HOLD / unknown — shouldn't appear here (HOLD decisions have no fired
    # trade to link to) but stay safe: treat as right only if |pnl|<0.25%.
    return abs(pnl_pct) < 0.0025


async def _resolve_one_horizon(
    db: Any, horizon: str, *, asset_type: str, trade_coll: str,
) -> dict[str, Any]:
    """Resolve all unresolved sovereign decisions for a given horizon + asset."""
    cutoff = _HORIZONS[horizon]
    now = datetime.now(timezone.utc)

    # Find unresolved sovereign decisions that have aged past this horizon
    # AND have a linked fired trade. We look up the trade via the link on
    # the trade-row side — sovereign_decisions rows stay schema-clean.
    unresolved_cur = db["sovereign_decisions"].find(
        {
            "asset_type": asset_type,
            f"outcomes.{horizon}": {"$exists": False},
            "created_at": {"$lte": now - cutoff},
        },
        {"_id": 0, "decision_id": 1, "action": 1, "symbol": 1, "created_at": 1},
    ).limit(BATCH_CAP)
    unresolved = await unresolved_cur.to_list(length=BATCH_CAP)

    resolved_count = 0
    skipped_no_trade = 0

    for row in unresolved:
        dec_id = row.get("decision_id")
        if not dec_id:
            continue
        try:
            trade = await db[trade_coll].find_one(
                {
                    "sovereign_decision_id": dec_id,
                    "status": "closed",
                    "closed_at": {"$gte": row["created_at"] + cutoff},
                },
                {"_id": 0, "pnl_pct": 1, "closed_at": 1},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[sovereign_resolve] trade lookup failed id=%s: %s", dec_id, exc)
            trade = None

        if trade is None or trade.get("pnl_pct") is None:
            skipped_no_trade += 1
            continue

        pnl_pct = float(trade["pnl_pct"])
        was_right = _was_right(str(row.get("action") or ""), pnl_pct)
        ok = await resolve_sovereign_decision(
            db, dec_id, horizon=horizon, pnl_pct=pnl_pct, was_right=was_right,
        )
        if ok:
            resolved_count += 1

    return {
        "asset_type": asset_type,
        "horizon": horizon,
        "scanned": len(unresolved),
        "resolved": resolved_count,
        "skipped_no_trade": skipped_no_trade,
    }


async def run_resolution_tick(db: Any) -> dict[str, Any]:
    """Main tick. Runs every 15 min via APScheduler."""
    if db is None:
        return {"status": "no_db"}

    try:
        tasks = []
        for horizon in _HORIZONS:
            tasks.append(await _resolve_one_horizon(
                db, horizon, asset_type="equity", trade_coll="paper_trades",
            ))
            tasks.append(await _resolve_one_horizon(
                db, horizon, asset_type="crypto", trade_coll="crypto_paper_trades",
            ))
        total_resolved = sum(t["resolved"] for t in tasks)
        return {
            "status": "ok",
            "ran_at": datetime.now(timezone.utc),
            "total_resolved": total_resolved,
            "per_horizon": tasks,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sovereign_resolve] tick failed: %s", exc)
        return {"status": "error", "error": str(exc)}
