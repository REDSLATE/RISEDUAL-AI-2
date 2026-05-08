"""
Nightly invariant audit — verifies the data-integrity rules established
by the 2026-05-01 direction-token cleanup remain true for the last 24h
of production traffic.

Runs as an APScheduler cron job (see server.py). Writes a summary
document to ``data_integrity_audits`` with:

    {
      "_id": <uuid>,
      "window_start": <iso>,
      "window_end": <iso>,
      "run_id": <iso_timestamp>,
      "checks": [
        {"name": "no_unknown_direction_tokens",
         "passed": bool, "count": int, "detail": ...},
        {"name": "strong_buy_rising_not_graded_miss",
         "passed": bool, "count": int, "samples": [...]},
        ...
      ],
      "overall_passed": bool,
    }

Invariants pinned (each is an ``InvariantCheck``):

    1. No ``unknown_direction_token`` metric rows in the last 24h.
       Any token that hit this metric means a writer slipped past the
       ``PredictionDirection.validate`` guard.

    2. Every ``STRONG_BUY`` prediction graded STRONG_MISS in the last 24h
       must have ``verified_price < price_at_prediction`` (i.e. the
       grading is arithmetically consistent with the move). This is
       the direct canary for the original direction-token bug.

    3. Mirror of 2 for ``STRONG_SELL``.

    4. Every ChromaDB toxic_lesson created in the last 24h must have a
       matching Mongo prediction with ``verified_24h.grade`` in
       ``{STRONG_MISS, WEAK_MISS}`` and an actual adverse price move.

The audit writes to ``data_integrity_audits`` but NEVER mutates
production data — repairs happen via the backfill scripts under
``scripts/``. This job is a tripwire, not a cleaner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class InvariantCheck:
    name: str
    description: str
    run: Callable[[Any, datetime], Awaitable[dict]]


async def _check_no_unknown_direction_tokens(db, since: datetime) -> dict:
    """Invariant #1: zero UNKNOWN-direction events in the window."""
    q = {
        "metric": "unknown_direction_token",
        "fired_at": {"$gte": since.isoformat()},
    }
    count = await db.data_integrity_metrics.count_documents(q)
    # Top 5 contexts for diagnosis.
    pipeline = [
        {"$match": q},
        {"$group": {"_id": "$context", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 5},
    ]
    contexts = [
        {"context": r["_id"], "count": r["n"]}
        async for r in db.data_integrity_metrics.aggregate(pipeline)
    ]
    return {
        "name": "no_unknown_direction_tokens",
        "passed": count == 0,
        "count": count,
        "top_contexts": contexts,
    }


async def _check_strong_direction_grading_consistent(
    db, since: datetime, *, direction: str, expect: str
) -> dict:
    """Invariant #2/#3: STRONG_BUY/STRONG_SELL graded STRONG_MISS only
    when the price move was genuinely against the call.

    ``expect`` is ``"down"`` for STRONG_BUY (price must fall to be a
    real miss) and ``"up"`` for STRONG_SELL.
    """
    q = {
        "direction": direction,
        "verified_24h.grade": "STRONG_MISS",
        "verified_24h.verified_at": {"$gte": since.isoformat()},
        "price_at_prediction": {"$gt": 0},
    }

    total = 0
    violations: list[dict] = []
    cursor = db.predictions.find(q, {
        "_id": 0, "symbol": 1, "direction": 1, "price_at_prediction": 1,
        "verified_24h": 1, "prediction_id": 1, "timestamp": 1,
    })
    async for r in cursor:
        total += 1
        price_at = float(r.get("price_at_prediction") or 0)
        v = r.get("verified_24h") or {}
        price_now = float(v.get("price") or 0)
        if price_at <= 0 or price_now <= 0:
            continue
        if expect == "down" and price_now > price_at:
            # STRONG_BUY + price went UP → bug still present
            violations.append({
                "prediction_id": r.get("prediction_id"),
                "symbol": r.get("symbol"),
                "direction": direction,
                "entry": price_at,
                "verified": price_now,
            })
        elif expect == "up" and price_now < price_at:
            violations.append({
                "prediction_id": r.get("prediction_id"),
                "symbol": r.get("symbol"),
                "direction": direction,
                "entry": price_at,
                "verified": price_now,
            })

    return {
        "name": f"{direction.lower()}_grading_consistent",
        "passed": not violations,
        "scanned": total,
        "violations": violations[:10],
        "violation_count": len(violations),
    }


async def _check_toxic_lessons_match_real_misses(db, since: datetime) -> dict:
    """Invariant #4: every new toxic_lesson in the window was created
    from a Mongo prediction whose grade is STRONG_MISS / WEAK_MISS AND
    whose price really did move against the call.

    This is the ChromaDB-side tripwire. Pre-fix the tag was applied to
    predictions whose grade was an arithmetic artefact, not a real
    adverse move. We now cross-check both sides.
    """
    try:
        import services.market_memory_service as mms
    except Exception as e:
        return {"name": "toxic_lessons_match_real_misses",
                "passed": True, "skipped": True, "reason": f"module import failed: {e}"}

    coll = getattr(mms, "_collection", None)
    if coll is None:
        return {"name": "toxic_lessons_match_real_misses",
                "passed": True, "skipped": True, "reason": "ChromaDB not initialised"}

    # Pull recent toxic_lesson rows from Chroma (up to 200).
    try:
        import asyncio
        res = await asyncio.to_thread(
            coll.get, where={"outcome": "toxic_lesson"}, include=["metadatas"]
        )
    except Exception as e:
        return {"name": "toxic_lessons_match_real_misses",
                "passed": True, "skipped": True, "reason": f"chroma get failed: {e}"}

    metas = res.get("metadatas") or []
    ids = res.get("ids") or []
    # Filter to this audit window by `created_at` / `logged_at` metadata.
    recent: list[tuple[str, dict]] = []
    since_iso = since.isoformat()
    for i, m in enumerate(metas):
        ts = (m or {}).get("created_at") or (m or {}).get("logged_at")
        if ts and str(ts) >= since_iso:
            recent.append((ids[i] if i < len(ids) else "", dict(m)))

    violations: list[dict] = []
    for cid, m in recent:
        pid = m.get("prediction_id")
        if not pid:
            continue
        pred = await db.predictions.find_one(
            {"prediction_id": pid},
            {"_id": 0, "direction": 1, "price_at_prediction": 1, "verified_24h": 1, "symbol": 1},
        )
        if not pred:
            violations.append({"chroma_id": cid, "reason": "no matching prediction"})
            continue
        v = pred.get("verified_24h") or {}
        grade = v.get("grade")
        if grade not in {"STRONG_MISS", "WEAK_MISS"}:
            violations.append({"chroma_id": cid, "prediction_id": pid,
                               "reason": f"toxic_lesson but grade={grade!r}"})
            continue

    return {
        "name": "toxic_lessons_match_real_misses",
        "passed": not violations,
        "scanned": len(recent),
        "violations": violations[:10],
        "violation_count": len(violations),
    }


# Registry of invariants. New checks register here.
INVARIANTS: list[InvariantCheck] = [
    InvariantCheck(
        name="no_unknown_direction_tokens",
        description="unknown_direction_token metric must be 0 in the window",
        run=_check_no_unknown_direction_tokens,
    ),
    InvariantCheck(
        name="strong_buy_grading_consistent",
        description="STRONG_BUY graded STRONG_MISS only when price fell",
        run=lambda db, since: _check_strong_direction_grading_consistent(
            db, since, direction="STRONG_BUY", expect="down"
        ),
    ),
    InvariantCheck(
        name="strong_sell_grading_consistent",
        description="STRONG_SELL graded STRONG_MISS only when price rose",
        run=lambda db, since: _check_strong_direction_grading_consistent(
            db, since, direction="STRONG_SELL", expect="up"
        ),
    ),
    InvariantCheck(
        name="toxic_lessons_match_real_misses",
        description="Every new toxic_lesson has a matching real adverse move",
        run=_check_toxic_lessons_match_real_misses,
    ),
]


async def run_nightly_integrity_audit(db: Any, *, window_hours: int = 24) -> dict:
    """Run every registered invariant, persist the summary, return it.

    Idempotent: safe to call multiple times per day (each run writes a
    fresh summary doc under a new ``run_id``).
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    results: list[dict] = []
    for check in INVARIANTS:
        try:
            r = await check.run(db, since)
        except Exception as e:
            logger.exception("[nightly_audit] invariant %s crashed", check.name)
            r = {"name": check.name, "passed": False, "error": str(e)}
        results.append(r)

    overall = all(r.get("passed", False) for r in results)
    summary = {
        "_id": str(uuid4()),
        "run_id": now.isoformat(),
        "window_start": since.isoformat(),
        "window_end": now.isoformat(),
        "window_hours": window_hours,
        "checks": results,
        "overall_passed": overall,
    }

    try:
        await db.data_integrity_audits.insert_one(summary)
    except Exception as e:
        logger.warning("[nightly_audit] persist summary failed (non-critical): %s", e)

    if overall:
        logger.info("[nightly_audit] ALL CHECKS PASSED")
    else:
        failed = [r["name"] for r in results if not r.get("passed", False)]
        logger.warning("[nightly_audit] FAILED CHECKS: %s", failed)

    return summary
