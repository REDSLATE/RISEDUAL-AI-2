"""Admin Position Reconciler — manual trigger + status endpoint.

Pairs with ``services/position_reconciler.py``. The reconciler also
runs every 30 minutes from the scheduler; this route gives the
operator a "Run now" button and exposes the pending-row counts so
the admin UI can show "X equity / Y options orders awaiting close
detection".

Endpoints:
  * ``GET  /api/admin/position-reconciler/status``
      Returns counts of pending (proof_chain_entity_id set,
      outcome_appended != True) and reconciled (outcome_appended True
      in last 30 days) rows for both equity and options orders.
  * ``POST /api/admin/position-reconciler/run``
      Triggers an immediate sweep. Returns the sweep summary.

Owner-only. Read endpoints are cheap (indexed counts); the run
endpoint is gated behind a 60s rate-limit per actor so the admin
can't accidentally drown the broker API by hammering "Run now".
"""
from __future__ import annotations

__domain__ = "BRIDGE"  # Reads from DTD (orders) + writes to Proof Chain (PRD)

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/position-reconciler", tags=["admin", "ip"])

_db: Any = None
_last_run_at: dict[str, datetime] = {}  # actor_email → last run ts
_RATE_LIMIT_SECONDS = 60


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/status")
async def reconciler_status(request: Request) -> dict:
    """Return pending + recently-reconciled row counts for the UI."""
    await _require_owner(request)
    if _db is None:
        return {"available": False, "reason": "db_unavailable"}

    cutoff_recent = datetime.now(timezone.utc) - timedelta(days=30)
    pending_q = {
        "proof_chain_entity_id": {"$ne": None, "$exists": True},
        "outcome_appended": {"$ne": True},
    }
    closed_q = {
        "outcome_appended": True,
        "outcome_appended_at": {"$gte": cutoff_recent},
    }

    eq_pending = await _db.trade_orders.count_documents(pending_q)
    eq_closed = await _db.trade_orders.count_documents(closed_q)
    opt_pending = await _db.option_orders.count_documents(pending_q)
    opt_closed = await _db.option_orders.count_documents(closed_q)

    return {
        "available": True,
        "equity": {"pending": eq_pending, "closed_30d": eq_closed},
        "options": {"pending": opt_pending, "closed_30d": opt_closed},
        "rate_limit_seconds": _RATE_LIMIT_SECONDS,
    }


@router.post("/run")
async def reconciler_run(request: Request) -> dict:
    """Trigger an immediate equity + options reconciliation sweep.

    Rate-limited to once per ``_RATE_LIMIT_SECONDS`` per owner email
    so a fast-clicker can't fan out to multiple broker API calls per
    second. The 30-minute scheduled tick is the steady-state cadence
    — this is for "I just placed a manual close, reconcile now".
    """
    user = await _require_owner(request)
    actor = user.get("email", "owner")

    last = _last_run_at.get(actor)
    if last is not None:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < _RATE_LIMIT_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Wait {int(_RATE_LIMIT_SECONDS - elapsed)}s.",
            )
    _last_run_at[actor] = datetime.now(timezone.utc)

    from services.position_reconciler import run_position_reconciler
    summary = await run_position_reconciler(_db)
    logger.info("[admin.reconciler] manual run by %s: %s", actor, summary)
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "summary": summary,
    }
