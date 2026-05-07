"""RoadGuard — admin diagnostic endpoint.

Read-only operator surface for the shared execution safety
governor (``services.roadguard``). Powers the Health-panel tile
that turns the raw ``roadguard_decisions`` collection into the
metrics needed for the promotion-evidence checklist:

* total decisions observed
* decision histogram (ALLOW / BLOCK / PAUSE_LANE)
* reason histogram for non-ALLOW verdicts
* per-lane breakdown (equity / crypto / unknown)
* recent decisions for spot-checking
* promotion checklist booleans

Authority: NONE. This module reads, summarises, returns. It
cannot promote, gate, or change any RoadGuard state. Mirrors
the Fast Veto admin surface contract.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/admin", tags=["admin-roadguard"])
logger = logging.getLogger(__name__)

db = None  # set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request) -> None:
    from routes.auth import get_current_user

    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Owner access required")


# Reason groupings used by the promotion checklist's
# "rule observed" rows. Keeping the membership in one place so
# new RoadGuard rules can be wired in without hunting through
# the UI tile.
_BROKER_HEALTH_REASONS = {"BROKER_HEALTH_DEGRADED"}
_EXPOSURE_REASONS = {
    "MAX_TOTAL_EXPOSURE",
    "MAX_EQUITY_EXPOSURE",
    "MAX_CRYPTO_EXPOSURE",
}
_DUPLICATE_REASONS = {"DUPLICATE_SYMBOL"}


@router.get("/roadguard/stats")
async def roadguard_stats(
    request: Request,
    limit: int = Query(25, ge=1, le=500),
) -> dict[str, Any]:
    """Summary + last-N rows for the RoadGuard shadow stream."""
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    from services.roadguard import (
        ROADGUARD_SHADOW_ENABLED,
        ROADGUARD_ENFORCE_ENABLED,
        ROADGUARD_CAN_APPROVE,
        MAX_TOTAL_EXPOSURE_USD,
        MAX_EQUITY_EXPOSURE_USD,
        MAX_CRYPTO_EXPOSURE_USD,
        MAX_DAILY_LOSS_USD,
        MAX_OPEN_POSITIONS_TOTAL,
        MAX_OPEN_POSITIONS_PER_LANE,
        BROKER_HEALTH_MIN,
    )

    coll = db.roadguard_decisions

    aggregate = await _summarise_bucket(coll, lane_filter=None)
    by_lane = {
        "equity":  await _summarise_bucket(coll, lane_filter="equity"),
        "crypto":  await _summarise_bucket(coll, lane_filter="crypto"),
        "unknown": await _summarise_bucket(coll, lane_filter="__unknown__"),
    }

    rows_cursor = (
        coll.find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    rows = await rows_cursor.to_list(length=limit)

    # False-block marks come from a future operator action endpoint.
    # Until that ships, a missing field is treated as "not marked"
    # so the promotion check stays simple: zero marks = pass.
    false_block_marked = await coll.count_documents(
        {"false_block_marked": True}
    )

    return {
        "shadow_enabled": ROADGUARD_SHADOW_ENABLED,
        "enforce_enabled": ROADGUARD_ENFORCE_ENABLED,
        "can_approve": ROADGUARD_CAN_APPROVE,
        "config": {
            "max_total_exposure_usd": MAX_TOTAL_EXPOSURE_USD,
            "max_equity_exposure_usd": MAX_EQUITY_EXPOSURE_USD,
            "max_crypto_exposure_usd": MAX_CRYPTO_EXPOSURE_USD,
            "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
            "max_open_positions_total": MAX_OPEN_POSITIONS_TOTAL,
            "max_open_positions_per_lane": MAX_OPEN_POSITIONS_PER_LANE,
            "broker_health_min": BROKER_HEALTH_MIN,
        },
        # Aggregate fields preserved at top level for symmetry
        # with the Fast Veto endpoint.
        "total": aggregate["total"],
        "decision_counts": aggregate["decision_counts"],
        "reason_counts": aggregate["reason_counts"],
        "by_lane": by_lane,
        "rows": rows,
        "promotion_checklist": _promotion_checklist(
            aggregate=aggregate,
            shadow_enabled=ROADGUARD_SHADOW_ENABLED,
            enforce_enabled=ROADGUARD_ENFORCE_ENABLED,
            false_block_marked=false_block_marked,
        ),
    }


async def _summarise_bucket(
    coll: Any,
    *,
    lane_filter: str | None,
) -> dict[str, Any]:
    """Summarise one lane bucket (or the aggregate when
    ``lane_filter`` is ``None``).

    Each bucket returns the same shape so the UI tile can reuse
    components across the aggregate + per-lane views.
    """
    if lane_filter is None:
        base_query: dict[str, Any] = {}
    elif lane_filter == "__unknown__":
        base_query = {
            "$or": [
                {"lane": {"$exists": False}},
                {"lane": None},
                {"lane": "unknown"},
            ]
        }
    else:
        base_query = {"lane": lane_filter}

    total = await coll.count_documents(base_query)
    allow_count = await coll.count_documents(
        {**base_query, "decision": "ALLOW"}
    )
    block_count = await coll.count_documents(
        {**base_query, "decision": "BLOCK"}
    )
    pause_lane_count = await coll.count_documents(
        {**base_query, "decision": "PAUSE_LANE"}
    )

    # Reason histogram — only meaningful for non-ALLOW verdicts
    # (ALLOW carries reason=None by contract).
    reason_pipeline = [
        {"$match": {**base_query, "decision": {"$ne": "ALLOW"}}},
        {"$group": {"_id": "$reason", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    reason_counts: dict[str, int] = {}
    async for d in coll.aggregate(reason_pipeline):
        rid = d.get("_id") or "UNKNOWN"
        reason_counts[rid] = int(d.get("count") or 0)

    # Enforce-vs-shadow split for the bucket. ``enforced=True``
    # means the verdict actually short-circuited the executor;
    # ``False`` means it was logged but the trade proceeded.
    enforced_count = await coll.count_documents(
        {**base_query, "enforced": True}
    )

    return {
        "total": int(total),
        "decision_counts": {
            "ALLOW": int(allow_count),
            "BLOCK": int(block_count),
            "PAUSE_LANE": int(pause_lane_count),
        },
        "reason_counts": reason_counts,
        "enforced_count": int(enforced_count),
    }


def _promotion_checklist(
    *,
    aggregate: dict[str, Any],
    shadow_enabled: bool,
    enforce_enabled: bool,
    false_block_marked: int,
) -> dict[str, Any]:
    """The seven gates the operator wants to clear before flipping
    enforce. Each item resolves to a pass/fail boolean so the UI
    can render a simple checklist."""
    total = aggregate["total"]
    reasons = aggregate["reason_counts"]

    samples_ok = total >= 500
    no_false_blocks = false_block_marked == 0

    # "Rule observed" — the rule has fired at least once in shadow
    # so we know it's wired into real traffic.
    broker_health_observed = any(
        reasons.get(r, 0) > 0 for r in _BROKER_HEALTH_REASONS
    )
    duplicate_observed = any(
        reasons.get(r, 0) > 0 for r in _DUPLICATE_REASONS
    )
    exposure_observed = any(
        reasons.get(r, 0) > 0 for r in _EXPOSURE_REASONS
    )

    return {
        "shadow_enabled": {
            "pass": shadow_enabled,
            "value": shadow_enabled,
        },
        "enforce_disabled": {
            "pass": not enforce_enabled,
            "value": enforce_enabled,
        },
        "samples_500_plus": {
            "pass": samples_ok,
            "value": int(total),
            "target": 500,
        },
        "zero_false_blocks": {
            "pass": no_false_blocks,
            "value": int(false_block_marked),
            "target": 0,
        },
        "broker_health_rule_observed": {
            "pass": broker_health_observed,
            "value": sum(reasons.get(r, 0) for r in _BROKER_HEALTH_REASONS),
        },
        "duplicate_symbol_rule_observed": {
            "pass": duplicate_observed,
            "value": sum(reasons.get(r, 0) for r in _DUPLICATE_REASONS),
        },
        "exposure_cap_rule_observed": {
            "pass": exposure_observed,
            "value": sum(reasons.get(r, 0) for r in _EXPOSURE_REASONS),
        },
        "ready_to_enforce": (
            samples_ok
            and no_false_blocks
            and broker_health_observed
            and duplicate_observed
            and exposure_observed
            and shadow_enabled
        ),
    }
