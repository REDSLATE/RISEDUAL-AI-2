"""Sovereign Learning-Health diagnostic (2026-02-26, P0).

Read-only owner endpoint that reports the *full pipeline* state for
Sovereign AI's promotion track. The operator was correctly suspicious
that "promotion gate stuck at 0/500" is not actually a "wait for data"
problem but a structural issue with **how decisions become resolved
training rows**. This endpoint exposes the exact counts at every
stage so the operator can see — at a glance — where the pipeline is
leaking.

Surfaces (per asset_type ∈ {equity, crypto}):

* ``decisions_total``        — total sovereign_decisions rows
* ``decisions_by_action``    — LONG / SHORT / HOLD distribution
* ``decisions_last_24h``     — recent throughput (is sidecar alive?)
* ``decisions_with_entry``   — count of rows carrying
                                feature_snapshot.entry_price (the
                                gating field for drift resolution)
* ``aged_past_horizon``      — for each horizon, decisions older than
                                that horizon (i.e. eligible for
                                resolution this tick)
* ``resolved_per_horizon``   — decisions with outcomes.{horizon}
                                populated
* ``unresolved_aged``        — decisions aged AND still unresolved =
                                the actual backlog
* ``promotion_gate``         — full status from
                                ``compute_sovereign_promotion_status``
                                so the operator never has to cross-
                                reference two endpoints

Doctrine: never raise. A diagnostic that 500s is worse than useless
because it leaves the operator with no signal at all. Every per-asset
section is wrapped in its own try/except and degrades to
``{"error": "..."}`` so partial failures still surface useful data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/sovereign",
    tags=["admin-sovereign-learning-health"],
)

db: Any = None


def set_db(database) -> None:
    global db
    db = database


# Match the horizon set used by the resolution loop so the numbers
# the operator sees here are the same numbers the gate sees.
_HORIZONS: dict[str, timedelta] = {
    "60m": timedelta(minutes=60),
    "4h": timedelta(hours=4),
    "eod": timedelta(hours=8),
}


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


async def _asset_section(asset_type: str) -> dict[str, Any]:
    """All pipeline counts for a single asset core."""
    coll = db["sovereign_decisions"]
    now = datetime.now(timezone.utc)
    section: dict[str, Any] = {}

    # 1. Total decisions
    try:
        section["decisions_total"] = await coll.count_documents(
            {"asset_type": asset_type},
        )
    except Exception as exc:  # noqa: BLE001
        section["decisions_total"] = {"error": str(exc)}

    # 2. Action distribution
    try:
        pipe = [
            {"$match": {"asset_type": asset_type}},
            {"$group": {"_id": "$action", "n": {"$sum": 1}}},
        ]
        rows = await coll.aggregate(pipe).to_list(length=10)
        section["decisions_by_action"] = {
            (r["_id"] or "UNKNOWN"): r["n"] for r in rows
        }
    except Exception as exc:  # noqa: BLE001
        section["decisions_by_action"] = {"error": str(exc)}

    # 3. Last 24h throughput
    try:
        section["decisions_last_24h"] = await coll.count_documents({
            "asset_type": asset_type,
            "created_at": {"$gte": now - timedelta(hours=24)},
        })
    except Exception as exc:  # noqa: BLE001
        section["decisions_last_24h"] = {"error": str(exc)}

    # 4. Decisions with entry_price (drift-resolver eligibility)
    try:
        section["decisions_with_entry_price"] = await coll.count_documents({
            "asset_type": asset_type,
            "feature_snapshot.entry_price": {"$exists": True, "$ne": None},
        })
    except Exception as exc:  # noqa: BLE001
        section["decisions_with_entry_price"] = {"error": str(exc)}

    # 5. Per-horizon resolution table
    per_horizon: dict[str, dict[str, Any]] = {}
    for horizon, delta in _HORIZONS.items():
        h: dict[str, Any] = {}
        cutoff = now - delta
        try:
            h["aged_past_horizon"] = await coll.count_documents({
                "asset_type": asset_type,
                "created_at": {"$lte": cutoff},
            })
        except Exception as exc:  # noqa: BLE001
            h["aged_past_horizon"] = {"error": str(exc)}
        try:
            h["resolved"] = await coll.count_documents({
                "asset_type": asset_type,
                f"outcomes.{horizon}": {"$exists": True},
            })
        except Exception as exc:  # noqa: BLE001
            h["resolved"] = {"error": str(exc)}
        try:
            h["was_right"] = await coll.count_documents({
                "asset_type": asset_type,
                f"outcomes.{horizon}.was_right": True,
            })
        except Exception as exc:  # noqa: BLE001
            h["was_right"] = {"error": str(exc)}
        try:
            h["unresolved_aged"] = await coll.count_documents({
                "asset_type": asset_type,
                "created_at": {"$lte": cutoff},
                f"outcomes.{horizon}": {"$exists": False},
            })
        except Exception as exc:  # noqa: BLE001
            h["unresolved_aged"] = {"error": str(exc)}
        per_horizon[horizon] = h
    section["per_horizon"] = per_horizon

    # 6. Promotion gate snapshot — the headline verdict.
    try:
        from services.sovereign_promotion_gate import (
            compute_sovereign_promotion_status,
        )
        gate = await compute_sovereign_promotion_status(db, asset_type)
        section["promotion_gate"] = gate
    except Exception as exc:  # noqa: BLE001
        section["promotion_gate"] = {"error": str(exc)}

    return section


@router.get("/learning-health")
async def get_sovereign_learning_health(request: Request) -> dict[str, Any]:
    """Full pipeline snapshot for the operator dashboard.

    Renders the breakdown that answers the operator's question
    "is Sovereign ready to take over?" — by showing what fraction
    of its voice is actually being scored.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    out: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizons": list(_HORIZONS.keys()),
    }
    for asset_type in ("equity", "crypto"):
        try:
            out[asset_type] = await _asset_section(asset_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[learning_health] %s section failed: %s", asset_type, exc,
            )
            out[asset_type] = {"error": str(exc)}
    return out


__all__ = ["router", "set_db"]
