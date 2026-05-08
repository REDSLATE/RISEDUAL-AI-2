"""Fast Veto Shadow — admin diagnostic endpoint.

Read-only operator surface for the Tier 1 Fast Veto Shadow Layer
(``services.fast_veto_layer``). Powers the Health-panel tile that
turns the raw ``fast_veto_shadow_deltas`` collection into the
metrics needed for the promotion-evidence checklist:

* total samples observed
* veto rate (overall and per reason)
* council-agreement rate when both decided
* latency p50 / p95
* most recent samples for spot-checking

Authority: NONE. This module reads, summarises, and returns. It
cannot promote, gate, or change any executor behaviour. Mirrors
the Shelly diagnostic surface contract.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/admin", tags=["admin-fast-veto"])
logger = logging.getLogger(__name__)

db = None  # set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request) -> None:
    """Same gate as the rest of the admin diagnostic surface."""
    from routes.auth import get_current_user

    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Owner access required")


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """O(1) lookup on a pre-sorted list. Returns 0.0 on empty input.

    Linear interpolation between adjacent ranks — same shape as
    numpy's default. Avoids the numpy dependency on a hot path
    that the operator might hit dozens of times per minute.
    """
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


@router.get("/fast-veto/stats")
async def fast_veto_stats(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Summary + last-N rows for the Fast Veto shadow stream.

    Returns the metrics needed to evaluate the promotion checklist:

    - ``total``: lifetime samples in ``fast_veto_shadow_deltas``
    - ``would_veto_rate``: fraction of samples flagged for veto
    - ``reason_counts``: histogram of veto reasons
    - ``agreement_with_council``: agree / disagree / unknown buckets
    - ``latency_us``: ``count``, ``p50``, ``p95``, ``min``, ``max``
    - ``shadow_enabled`` / ``enforce_enabled``: live env flag values
    - ``rows``: last ``limit`` deltas, newest-first
    - ``by_lane``: same shape as the top-level summary, broken out
      per lane (``equity`` / ``crypto`` / ``unknown``). Lets the
      Fast Veto Tile render two parallel metric strips so the
      operator can spot lane-specific anomalies in one glance.
      ``unknown`` covers schema_version=1 docs that pre-date lane
      tagging; the bucket stays visible so the gap is impossible
      to miss.

    No writes. Excludes ``_id`` per project-wide MongoDB rule.
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DB not initialised")

    from services.fast_veto_layer import (
        FAST_VETO_SHADOW_ENABLED,
        FAST_VETO_ENFORCE_ENABLED,
        FAST_VETO_CAN_APPROVE,
    )

    coll = db.fast_veto_shadow_deltas

    # Aggregate (no lane filter) + per-lane buckets share one
    # summarisation routine so the response shape stays stable
    # whether a lane has 0 or 50,000 samples.
    aggregate_summary = await _summarise_bucket(coll, lane_filter=None)
    by_lane = {
        "equity":  await _summarise_bucket(coll, lane_filter="equity"),
        "crypto":  await _summarise_bucket(coll, lane_filter="crypto"),
        # ``unknown`` covers schema_version=1 docs (created before
        # lane tagging was introduced) plus any future ingester
        # that forgets to set the tag. Keeping the bucket visible
        # makes the gap impossible to ignore.
        "unknown": await _summarise_bucket(coll, lane_filter="__unknown__"),
    }

    # Most recent rows for spot-checking. Newest first.
    rows_cursor = (
        coll.find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    rows = await rows_cursor.to_list(length=limit)

    return {
        "shadow_enabled": FAST_VETO_SHADOW_ENABLED,
        "enforce_enabled": FAST_VETO_ENFORCE_ENABLED,
        "can_approve": FAST_VETO_CAN_APPROVE,
        # Aggregate fields preserved at top level for backward
        # compatibility with the v1 tile layout and the existing
        # contract tests in ``test_fast_veto_stats_api.py``.
        "total": aggregate_summary["total"],
        "would_veto_count": aggregate_summary["would_veto_count"],
        "would_veto_rate": aggregate_summary["would_veto_rate"],
        "reason_counts": aggregate_summary["reason_counts"],
        "council_agreement": aggregate_summary["council_agreement"],
        "latency_us": aggregate_summary["latency_us"],
        "promotion_checklist": aggregate_summary["promotion_checklist"],
        # New per-lane disaggregation. Each bucket is the same
        # shape as the aggregate summary above so the UI tile can
        # render them with the same components.
        "by_lane": by_lane,
        "rows": rows,
    }


async def _summarise_bucket(
    coll: Any,
    *,
    lane_filter: str | None,
) -> dict[str, Any]:
    """Compute the full summary block for one lane bucket (or the
    aggregate when ``lane_filter`` is ``None``).

    ``lane_filter`` semantics:
      * ``None``  → no filter, sums across all docs
      * ``"equity"`` / ``"crypto"`` → exact match on ``lane`` field
      * ``"__unknown__"`` → docs with ``lane`` missing or null
        (schema_version=1 pre-lane-tagging docs)
    """
    base_query: dict[str, Any]
    if lane_filter is None:
        base_query = {}
    elif lane_filter == "__unknown__":
        # ``$or`` covers both "field missing" and "field is null".
        base_query = {
            "$or": [
                {"lane": {"$exists": False}},
                {"lane": None},
            ]
        }
    else:
        base_query = {"lane": lane_filter}

    total = await coll.count_documents(base_query)
    would_veto_count = await coll.count_documents(
        {**base_query, "would_veto": True}
    )

    reason_pipeline = [
        {"$match": {**base_query, "would_veto": True}},
        {"$group": {"_id": "$reason", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    reason_counts: dict[str, int] = {}
    async for d in coll.aggregate(reason_pipeline):
        rid = d.get("_id") or "UNKNOWN"
        reason_counts[rid] = int(d.get("count") or 0)

    agree_count = await coll.count_documents(
        {**base_query, "would_veto": True, "agreement_with_council": True}
    )
    disagree_count = await coll.count_documents(
        {**base_query, "would_veto": True, "agreement_with_council": False}
    )
    unknown_count = await coll.count_documents(
        {**base_query, "would_veto": True, "agreement_with_council": None}
    )

    latency_cursor = coll.find(
        {**base_query, "latency_us": {"$exists": True}},
        {"_id": 0, "latency_us": 1},
    )
    latencies: list[float] = []
    async for doc in latency_cursor:
        v = doc.get("latency_us")
        if isinstance(v, (int, float)):
            latencies.append(float(v))
    latencies.sort()

    latency_summary = {
        "count": len(latencies),
        "p50": _percentile(latencies, 50.0),
        "p95": _percentile(latencies, 95.0),
        "min": latencies[0] if latencies else 0.0,
        "max": latencies[-1] if latencies else 0.0,
    }

    return {
        "total": int(total),
        "would_veto_count": int(would_veto_count),
        "would_veto_rate": (
            float(would_veto_count) / float(total) if total > 0 else 0.0
        ),
        "reason_counts": reason_counts,
        "council_agreement": {
            "agree": int(agree_count),
            "disagree": int(disagree_count),
            "unknown": int(unknown_count),
            "rate": (
                float(agree_count) / float(agree_count + disagree_count)
                if (agree_count + disagree_count) > 0
                else None
            ),
        },
        "latency_us": latency_summary,
        "promotion_checklist": _promotion_checklist(
            total=total,
            agree_count=agree_count,
            disagree_count=disagree_count,
            latency_p50=latency_summary["p50"],
        ),
    }


def _promotion_checklist(
    *,
    total: int,
    agree_count: int,
    disagree_count: int,
    latency_p50: float,
) -> dict[str, Any]:
    """Operator-facing checklist matching the rules from the
    enforce-promotion criteria. Each item resolves to a boolean
    so the UI can render a simple pass/fail row."""
    samples_ok = total >= 500
    decided = agree_count + disagree_count
    false_veto_rate = (
        (disagree_count / decided) if decided > 0 else None
    )
    false_veto_ok = (
        false_veto_rate is not None and false_veto_rate < 0.03
    )
    latency_ok = latency_p50 < 1000.0

    return {
        "samples_500_plus": {
            "pass": samples_ok,
            "value": int(total),
            "target": 500,
        },
        "false_veto_rate_under_3pct": {
            "pass": false_veto_ok,
            "value": false_veto_rate,
            "target": 0.03,
        },
        "median_latency_under_1ms": {
            "pass": latency_ok,
            "value": latency_p50,
            "target": 1000.0,
        },
        "ready_to_enforce": (
            samples_ok and false_veto_ok and latency_ok
        ),
    }
