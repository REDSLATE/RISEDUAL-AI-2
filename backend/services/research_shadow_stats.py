"""Stats reducer for the research shadow log.

Surfaces three operator-grade metrics, in this order of importance:

1. **Disagreement-conditional win rate** — of the dissents that have
   been scored, what fraction did shadow's hypothetical fill beat
   active's? This is the ONLY metric that earns a shadow engine its
   Tier-3 promotion. Raw agreement rate is junk telemetry by
   construction.

2. **Dissent count + actionable boolean** — until ``MIN_DISSENT_SAMPLES``
   (30) dissents have been scored, the win rate is statistical noise.
   Mirror the same maturity guardrail discipline as
   ``crypto_adversarial_stats`` and ``crypto_shadow_research_stats``.

3. **Total $ delta** — sum of ``tactical_score.delta_usd`` across all
   scored dissents. Tells the operator the cumulative dollar value
   shadow would have added or subtracted, after asset-type fill
   costs.

Pure-function design — :func:`compute_shadow_stats` takes a list of
rows so unit tests don't need a Mongo handle. The Mongo glue lives
below the pure reducer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.research_shadow import MIN_DISSENT_SAMPLES
from services.research_shadow_logger import SHADOW_COLLECTION

logger = logging.getLogger(__name__)


# Result key shape we emit per (engine, asset_type) bucket. Pinned so
# downstream UI consumers can rely on the field set even when a
# bucket has no scored dissents yet.
_EMPTY_BUCKET: dict[str, Any] = {
    "total_decisions": 0,
    "dissent_count": 0,
    "scored_dissent_count": 0,
    "scored_dissent_pct": None,  # of dissents, what fraction has been scored
    "win_count": 0,
    "win_rate": None,             # disagreement-conditional win rate
    "total_delta_usd": 0.0,
    "actionable": False,
    "min_dissent_samples_required": MIN_DISSENT_SAMPLES,
}

# Phase-breakdown sub-record. Tells the operator WHEN dissents fire
# — entry, cycle (mid-trade), or exit — and the win rate within
# each. The mid-trade exit dissent ("Council says CLOSE while active
# holds") is the highest-value scenario; phase breakdown surfaces
# whether a shadow engine adds tactical alpha (entry), positional
# alpha (cycle), or strategic alpha (exit).
_PHASE_KEYS = ("entry", "cycle", "exit")


def _empty_phase_record() -> dict[str, Any]:
    return {"dissents": 0, "scored": 0, "wins": 0, "win_rate": None}


def _bucket_key(engine: Optional[str], asset_type: Optional[str]) -> str:
    return f"{(engine or '?')}::{(asset_type or '?')}"


def compute_shadow_stats(rows: list[dict]) -> dict[str, Any]:
    """Pure reducer. Takes a list of ``research_shadow_decisions``
    rows and produces the operator payload.

    Buckets by (shadow_engine, asset_type). The win rate is
    computed STRICTLY on rows where:

    * ``is_dissent=True``
    * ``tactical_score.delta_usd`` is a real number (skips pending
      and skips rows where the lookahead price could not be resolved)

    Agreement rows are counted in ``total_decisions`` for context but
    never feed the win rate.
    """
    by_bucket: dict[str, dict[str, Any]] = {}

    def _ensure(key: str, engine: str, asset_type: str) -> dict[str, Any]:
        if key not in by_bucket:
            entry = dict(_EMPTY_BUCKET)
            entry["shadow_engine"] = engine
            entry["asset_type"] = asset_type
            entry["phase_breakdown"] = {p: _empty_phase_record() for p in _PHASE_KEYS}
            by_bucket[key] = entry
        return by_bucket[key]

    for row in rows:
        engine = row.get("shadow_engine") or "?"
        asset_type = row.get("asset_type") or "?"
        bucket = _ensure(_bucket_key(engine, asset_type), engine, asset_type)

        bucket["total_decisions"] += 1

        if not row.get("is_dissent"):
            continue
        bucket["dissent_count"] += 1

        # Phase counter — track which lifecycle stage this dissent
        # fired at. Unknown phases bucket under "cycle" defensively.
        phase = row.get("decision_phase") or "cycle"
        if phase not in _PHASE_KEYS:
            phase = "cycle"
        bucket["phase_breakdown"][phase]["dissents"] += 1

        tactical = row.get("tactical_score")
        if not isinstance(tactical, dict):
            continue
        delta = tactical.get("delta_usd")
        if not isinstance(delta, (int, float)):
            continue

        bucket["scored_dissent_count"] += 1
        bucket["total_delta_usd"] += float(delta)
        bucket["phase_breakdown"][phase]["scored"] += 1
        if delta > 0:
            bucket["win_count"] += 1
            bucket["phase_breakdown"][phase]["wins"] += 1

    # Finalise per-bucket derived numbers + the actionable flag.
    for bucket in by_bucket.values():
        scored = bucket["scored_dissent_count"]
        if scored > 0:
            bucket["win_rate"] = round(bucket["win_count"] / scored, 4)
        if bucket["dissent_count"] > 0:
            bucket["scored_dissent_pct"] = round(
                scored / bucket["dissent_count"], 4,
            )
        bucket["total_delta_usd"] = round(bucket["total_delta_usd"], 4)
        bucket["actionable"] = scored >= MIN_DISSENT_SAMPLES
        # Per-phase win rate. Same maturity logic — null until at
        # least one scored sample lands in that phase bucket.
        for phase_rec in bucket["phase_breakdown"].values():
            if phase_rec["scored"] > 0:
                phase_rec["win_rate"] = round(
                    phase_rec["wins"] / phase_rec["scored"], 4,
                )

    return {
        "buckets": list(by_bucket.values()),
        "min_dissent_samples_required": MIN_DISSENT_SAMPLES,
    }


# ── Mongo glue ────────────────────────────────────────────────────────────────


async def fetch_shadow_stats(
    db: Any,
    *,
    hours: Optional[int] = None,
    bot_id: Optional[str] = None,
) -> dict[str, Any]:
    """Pull rows + run the reducer. Optional rolling window or
    bot-scoped filter for the per-bot drilldown view.
    """
    if db is None:
        return compute_shadow_stats([])

    query: dict[str, Any] = {}
    if hours is not None and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query["ts"] = {"$gte": cutoff}
    if bot_id:
        query["bot_id"] = bot_id

    try:
        rows = await db[SHADOW_COLLECTION].find(
            query,
            {
                "_id": 0,
                "shadow_engine": 1,
                "asset_type": 1,
                "decision_phase": 1,
                "is_dissent": 1,
                "tactical_score": 1,
            },
        ).to_list(length=100_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-stats] query failed: %s", exc)
        return compute_shadow_stats([])

    summary = compute_shadow_stats(rows)
    if hours is not None:
        summary["window_hours"] = hours
    if bot_id:
        summary["bot_id"] = bot_id
    return summary


async def fetch_recent_shadow_decisions(
    db: Any,
    *,
    limit: int = 50,
    bot_id: Optional[str] = None,
    symbol: Optional[str] = None,
    only_dissents: bool = False,
) -> dict[str, Any]:
    """Paginated raw feed for the per-position drawer + admin
    timeline view. Newest first, hard-capped at 200.
    """
    if db is None:
        return {"items": [], "count": 0, "limit": 0, "filters": {}}

    safe_limit = max(1, min(int(limit or 50), 200))

    query: dict[str, Any] = {}
    if bot_id:
        query["bot_id"] = bot_id
    if symbol:
        query["symbol"] = symbol.upper()
    if only_dissents:
        query["is_dissent"] = True

    try:
        rows = await db[SHADOW_COLLECTION].find(
            query, {"_id": 0},
        ).sort("ts", -1).limit(safe_limit).to_list(length=safe_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-decisions] query failed: %s", exc)
        return {"items": [], "count": 0, "limit": safe_limit,
                "filters": {}, "error": str(exc)[:120]}

    for row in rows:
        ts = row.get("ts")
        if hasattr(ts, "isoformat"):
            row["ts"] = ts.isoformat()

    return {
        "items": rows,
        "count": len(rows),
        "limit": safe_limit,
        "filters": {k: v for k, v in
                    (("bot_id", query.get("bot_id")),
                     ("symbol", query.get("symbol")),
                     ("only_dissents", only_dissents))
                    if v},
    }


async def fetch_cost_budget(db: Any) -> dict[str, Any]:
    """Per-bot daily LLM spend on shadows, rolling 24h window.
    Surfaces the cost-ceiling tiering for the admin banner.
    """
    if db is None:
        return {"bots": [], "ceiling_usd_per_day": 0.0}

    from services.research_shadow import (
        COST_CEILING_USD_PER_DAY,
        COST_DEGRADED_FRAC,
        COST_PAUSED_FRAC,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        cursor = db[SHADOW_COLLECTION].aggregate([
            {"$match": {"ts": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"bot_id": "$bot_id", "engine": "$shadow_engine"},
                "decisions_24h": {"$sum": 1},
                "spend_24h_usd": {"$sum": {"$ifNull": ["$llm_cost_usd", 0]}},
            }},
        ])
        rows = await cursor.to_list(length=500)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-cost-budget] aggregation failed: %s", exc)
        return {"bots": [], "ceiling_usd_per_day": COST_CEILING_USD_PER_DAY,
                "error": str(exc)[:120]}

    bots = []
    for row in rows:
        spend = float(row.get("spend_24h_usd") or 0.0)
        frac = (spend / COST_CEILING_USD_PER_DAY) if COST_CEILING_USD_PER_DAY > 0 else 0.0
        if frac >= COST_PAUSED_FRAC:
            tier = "paused"
        elif frac >= COST_DEGRADED_FRAC:
            tier = "degraded"
        else:
            tier = "full"
        bots.append({
            "bot_id": (row.get("_id") or {}).get("bot_id"),
            "engine": (row.get("_id") or {}).get("engine"),
            "decisions_24h": int(row.get("decisions_24h") or 0),
            "spend_24h_usd": round(spend, 4),
            "ceiling_frac": round(frac, 4),
            "tier": tier,
        })
    bots.sort(key=lambda b: b["spend_24h_usd"], reverse=True)

    return {
        "bots": bots,
        "ceiling_usd_per_day": COST_CEILING_USD_PER_DAY,
        "degraded_frac": COST_DEGRADED_FRAC,
        "paused_frac": COST_PAUSED_FRAC,
    }
