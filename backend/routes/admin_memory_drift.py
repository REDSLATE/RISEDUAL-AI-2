"""Mongo→Chroma Drift Detector.

Single endpoint that turns silent sync corruption into a number on
a dashboard. Compares ChromaDB episode count against MongoDB's
verified-prediction count and surfaces:

  * Total drift (count + percentage) — where drift = Mongo - Chroma
  * Per-date breakdown so the operator can see *when* drift started
  * Last-rebuild timestamp so 8% drift right after a rebuild reads
    as "expected" while the same 8% one hour later reads as
    "actively broken"
  * Sync-skip counters from ``mongo_chroma_sync_metrics`` — the
    structured replacement for the broad excepts that used to
    swallow these failures
  * A threshold-driven recommendation (ok / investigate / rebuild)
    so the operator doesn't have to interpret raw numbers

Sign convention
---------------
* **Positive drift** (mongo > chroma) = sync silently dropped rows.
  This is the actionable signal — the recommendation classifier
  fires on this only.
* **Negative drift** (chroma > mongo) = benign over-supply, almost
  always from ``memory_training_service``'s yfinance bulk-training
  writing Chroma rows that never had a matching Mongo prediction.
  Surfaced in the breakdown for visibility but never triggers a
  rebuild recommendation.

Owner-only. Cheap (one ChromaDB count + one Mongo aggregate);
suitable for polling at 1-5 minute cadence from a dashboard.

Why these thresholds:
  * < 1%   → ``ok`` — within rounding noise of the eventual-consistency
            window (verifier runs hourly, sync runs on verification).
  * < 10%  → ``investigate`` — meaningful drift; check skip counters
            and the date breakdown to localize the cause.
  * ≥ 10%  → ``rebuild`` — the sync is materially broken or hasn't
            run; the operator should hit the rebuild endpoint and
            re-check after.
"""
from __future__ import annotations

__domain__ = "PRD"  # Post-Resolution Domain — observability over
                    # historic verified predictions.

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/memory", tags=["admin", "memory"])

_db: Any = None

# Drift thresholds. Tuned for the production sync cadence (hourly
# verifier + on-verification sync), not theoretical noise.
THRESHOLD_OK_PCT = 1.0
THRESHOLD_INVESTIGATE_PCT = 10.0


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


def _classify(drift_pct: float) -> str:
    if drift_pct < THRESHOLD_OK_PCT:
        return "ok"
    if drift_pct < THRESHOLD_INVESTIGATE_PCT:
        return "investigate"
    return "rebuild"


async def _mongo_per_date_counts(days: int) -> dict[str, int]:
    """Aggregate verified predictions per prediction-date over the
    lookback window.

    Window field: ``verified_24h.verified_at`` — when grading
    happened. Mirrors the rebuild endpoint so the two surfaces
    agree on "the last N days of work".

    Bucketing: the date portion of ``timestamp`` (via
    ``to_iso_date``), NOT ``prediction_date``. Earlier code used
    ``$group _id: $prediction_date`` but that field is ``None`` on
    older rows — the aggregation produced one giant null bucket
    that the drift dashboard then dropped, leaving
    ``mongo_verified_count: 0`` even after a 101-row rebuild.

    The Chroma side keys metadata on the same date-portion of the
    original timestamp (see ``save_regime`` callers), so per-date
    totals on both sides are directly comparable.

    Returns ``{ "YYYY-MM-DD": count }``.
    """
    if _db is None:
        return {}
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = _db.predictions.find(
        {
            "verified_24h.correct": {"$in": [True, False]},
            "verified_24h.verified_at": {"$gte": cutoff_iso},
        },
        {"_id": 0, "timestamp": 1, "prediction_date": 1},
    )
    out: dict[str, int] = {}
    async for doc in cursor:
        # Prefer ``prediction_date`` when present (newer rows
        # populate it); fall back to ``to_iso_date(timestamp)`` for
        # older rows. ``to_iso_date`` handles both ISO strings and
        # Mongo-datetime BSON Dates and is the same helper the
        # save path uses, guaranteeing both ends key on the same
        # YYYY-MM-DD.
        from services.datetime_utils import to_iso_date
        date_key = doc.get("prediction_date") or to_iso_date(doc.get("timestamp"))
        if not date_key:
            continue
        out[date_key] = out.get(date_key, 0) + 1
    return out


async def _chroma_per_date_counts(days: int) -> dict[str, int]:
    """Pull metadata-only from ChromaDB and bucket per ``date``.

    Note: includes ALL Chroma rows regardless of ``date`` value.
    The Mongo side is filtered on ``verified_at >= cutoff`` (when
    grading happened) but Chroma rows are keyed by
    ``prediction_date`` — so a recently-graded old prediction
    appears in Mongo's window but its Chroma counterpart has an
    older ``date``. To keep per-date comparisons honest, we don't
    drop Chroma rows by date here; the per-date breakdown
    naturally surfaces both (a) recent verifications missing from
    Chroma → positive skew → sync regression and (b) older
    training rows that never had a Mongo prediction → negative
    skew → benign.
    """
    import asyncio as _aio
    from services.market_memory_service import _collection
    if _collection is None:
        return {}
    try:
        result = await _aio.to_thread(
            _collection.get,
            include=["metadatas"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[drift] chroma_get failed: %s", exc)
        return {}
    out: dict[str, int] = {}
    for meta in (result.get("metadatas") or []):
        if not meta:
            continue
        date_str = meta.get("date") or ""
        if not date_str:
            continue
        # Skip toxic_lesson re-tags — they're a nightly mutation,
        # not a separate row, so they shouldn't double-count.
        if meta.get("outcome") == "toxic_lesson":
            continue
        out[date_str] = out.get(date_str, 0) + 1
    return out


@router.get("/drift")
async def memory_drift(
    request: Request,
    days: int = Query(30, ge=1, le=365, description="Lookback window in days."),
    top_skew: int = Query(10, ge=1, le=50,
                          description="How many top per-date skew rows to return."),
) -> dict:
    """Drift snapshot: Mongo verified-predictions vs ChromaDB episodes.

    Cheap to call repeatedly — counts only, no document read.
    """
    await _require_owner(request)

    if _db is None:
        return {"available": False, "reason": "db_unavailable"}

    mongo_per_date = await _mongo_per_date_counts(days)
    chroma_per_date = await _chroma_per_date_counts(days)

    mongo_total = sum(mongo_per_date.values())
    chroma_total = sum(chroma_per_date.values())

    # Positive drift = Mongo ahead of Chroma → sync silently dropped
    # rows. Negative drift = Chroma ahead of Mongo → benign (training
    # data from ``memory_training_service`` writes Chroma without a
    # matching Mongo prediction). The recommendation key fires on
    # *positive* drift only.
    drift = mongo_total - chroma_total
    drift_pct = (
        round(max(drift, 0) / mongo_total * 100, 2) if mongo_total > 0 else 0.0
    )

    # Per-date breakdown — show only dates where there's a meaningful
    # gap so the operator's eye lands on the regression window.
    all_dates = set(mongo_per_date) | set(chroma_per_date)
    skews = []
    for d in all_dates:
        m = mongo_per_date.get(d, 0)
        c = chroma_per_date.get(d, 0)
        gap = m - c
        if gap == 0:
            continue
        skews.append({
            "date": d,
            "mongo": m,
            "chroma": c,
            "skew": gap,
        })
    # Sort by *positive* skew first (sync regressions), then by
    # absolute magnitude. Negative skew rows still show up but
    # below — they're FYI, not action items.
    skews.sort(key=lambda r: (r["skew"] < 0, -abs(r["skew"])))
    by_date_top_skew = skews[:top_skew]

    # Sync metrics + last-rebuild for context.
    from services.mongo_chroma_sync_metrics import (
        get_skip_counters,
        get_last_rebuild,
    )
    sync_skipped = get_skip_counters()
    rebuild_meta = await get_last_rebuild()

    return {
        "available": True,
        "window_days": days,
        # Document the temporal field used for the Mongo-side
        # filter so the operator (and any future consumer) can
        # reconcile this number with the rebuild endpoint, which
        # uses the same field.
        "window_field": "verified_24h.verified_at",
        "mongo_verified_count": mongo_total,
        "chroma_episode_count": chroma_total,
        "drift": drift,
        "drift_pct": drift_pct,
        "recommendation": _classify(drift_pct),
        "thresholds": {
            "ok_pct": THRESHOLD_OK_PCT,
            "investigate_pct": THRESHOLD_INVESTIGATE_PCT,
        },
        "by_date_top_skew": by_date_top_skew,
        "sync_skipped_total": sync_skipped,
        **rebuild_meta,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
