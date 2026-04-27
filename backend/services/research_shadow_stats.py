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


# ── Cost trend (sparkline-ready) ──────────────────────────────────────────────


async def fetch_cost_history(
    db: Any, *, days: int = 14,
) -> dict[str, Any]:
    """Daily LLM spend history per bot for sparkline rendering.

    Returns up to ``days`` (clamped 1-90) of UTC-day buckets. Days
    with zero spend are emitted as zeros — sparkline renders a
    continuous line, not a dotted plot, which makes the daily
    rhythm easier to read.

    Includes the daily ceiling so the UI can plot it as a reference
    line. ``tier_today`` mirrors the cost-budget endpoint's tier
    classification for convenience.
    """
    from services.research_shadow import (
        COST_CEILING_USD_PER_DAY,
        COST_DEGRADED_FRAC,
        COST_PAUSED_FRAC,
    )

    safe_days = max(1, min(int(days or 14), 90))

    if db is None:
        return {
            "bots": [],
            "days": safe_days,
            "ceiling_usd_per_day": COST_CEILING_USD_PER_DAY,
        }

    cutoff = datetime.now(timezone.utc) - timedelta(days=safe_days)
    try:
        cursor = db[SHADOW_COLLECTION].aggregate([
            {"$match": {"ts": {"$gte": cutoff}}},
            {"$group": {
                "_id": {
                    "bot_id": "$bot_id",
                    "engine": "$shadow_engine",
                    "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}},
                },
                "spend_usd": {"$sum": {"$ifNull": ["$llm_cost_usd", 0]}},
                "decisions": {"$sum": 1},
            }},
            {"$sort": {"_id.day": 1}},
        ])
        rows = await cursor.to_list(length=10_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-cost-history] aggregation failed: %s", exc)
        return {
            "bots": [],
            "days": safe_days,
            "ceiling_usd_per_day": COST_CEILING_USD_PER_DAY,
            "error": str(exc)[:120],
        }

    # Build the contiguous day axis so the sparkline doesn't skip
    # days with no activity.
    today = datetime.now(timezone.utc).date()
    day_axis = [
        (today - timedelta(days=safe_days - 1 - i)).isoformat()
        for i in range(safe_days)
    ]

    # Pivot: {(bot_id, engine): {day: {spend, decisions}}}
    pivot: dict[tuple, dict[str, dict[str, float]]] = {}
    for r in rows:
        key = (r["_id"]["bot_id"], r["_id"]["engine"])
        pivot.setdefault(key, {})
        pivot[key][r["_id"]["day"]] = {
            "spend_usd": round(float(r.get("spend_usd") or 0.0), 6),
            "decisions": int(r.get("decisions") or 0),
        }

    bots = []
    for (bot_id, engine), daily in pivot.items():
        series = [
            {
                "day": d,
                "spend_usd": daily.get(d, {}).get("spend_usd", 0.0),
                "decisions": daily.get(d, {}).get("decisions", 0),
            }
            for d in day_axis
        ]
        spend_today = series[-1]["spend_usd"] if series else 0.0
        frac_today = (
            spend_today / COST_CEILING_USD_PER_DAY
            if COST_CEILING_USD_PER_DAY > 0 else 0.0
        )
        if frac_today >= COST_PAUSED_FRAC:
            tier_today = "paused"
        elif frac_today >= COST_DEGRADED_FRAC:
            tier_today = "degraded"
        else:
            tier_today = "full"
        bots.append({
            "bot_id": bot_id,
            "engine": engine,
            "series": series,
            "total_spend_usd": round(sum(p["spend_usd"] for p in series), 4),
            "tier_today": tier_today,
        })

    bots.sort(key=lambda b: b["total_spend_usd"], reverse=True)
    return {
        "bots": bots,
        "days": safe_days,
        "day_axis": day_axis,
        "ceiling_usd_per_day": COST_CEILING_USD_PER_DAY,
    }


# ── Regime-conditional stats (P2 scaffolding) ─────────────────────────────────


def compute_regime_stats(rows: list[dict]) -> dict[str, Any]:
    """Pure reducer — buckets dissents by ``regime_at_decision`` and
    produces per-regime win-rate + delta + actionable flag. Same
    maturity guardrail as the main stats reducer (≥30 dissents per
    bucket before actionable).

    Why this exists today even though "regime-conditional weights"
    needs ≥15 buckets: the reducer is a pure function, harmless to
    ship empty. The moment the data lands, the endpoint returns
    actionable rows automatically — no code deploy required.

    Output shape per regime bucket::

        {
            "regime": "trending",
            "shadow_engine": "council",
            "asset_type": "crypto",
            "dissent_count": int,
            "scored_dissent_count": int,
            "win_count": int,
            "win_rate": float | None,
            "total_delta_usd": float,
            "actionable": bool,
        }
    """
    by_key: dict[tuple, dict[str, Any]] = {}

    for row in rows:
        if not row.get("is_dissent"):
            continue
        regime = row.get("regime_at_decision")
        if not regime:
            # Skip un-tagged rows so legacy dissents don't pollute
            # the breakdown — they'd all bucket under "?" and
            # confuse the win-rate math.
            continue
        engine = row.get("shadow_engine") or "?"
        asset_type = row.get("asset_type") or "?"
        key = (regime, engine, asset_type)

        bucket = by_key.setdefault(key, {
            "regime": regime,
            "shadow_engine": engine,
            "asset_type": asset_type,
            "dissent_count": 0,
            "scored_dissent_count": 0,
            "win_count": 0,
            "total_delta_usd": 0.0,
        })
        bucket["dissent_count"] += 1

        tactical = row.get("tactical_score")
        if not isinstance(tactical, dict):
            continue
        delta = tactical.get("delta_usd")
        if not isinstance(delta, (int, float)):
            continue
        bucket["scored_dissent_count"] += 1
        bucket["total_delta_usd"] += float(delta)
        if delta > 0:
            bucket["win_count"] += 1

    # Finalise.
    out_buckets = []
    for bucket in by_key.values():
        scored = bucket["scored_dissent_count"]
        bucket["win_rate"] = (
            round(bucket["win_count"] / scored, 4) if scored > 0 else None
        )
        bucket["total_delta_usd"] = round(bucket["total_delta_usd"], 4)
        bucket["actionable"] = scored >= MIN_DISSENT_SAMPLES
        out_buckets.append(bucket)

    out_buckets.sort(key=lambda b: (-b["dissent_count"], b["regime"]))
    return {
        "buckets": out_buckets,
        "min_dissent_samples_required": MIN_DISSENT_SAMPLES,
    }


async def fetch_regime_stats(
    db: Any, *, hours: Optional[int] = None,
) -> dict[str, Any]:
    """Mongo glue for :func:`compute_regime_stats`."""
    if db is None:
        return compute_regime_stats([])

    query: dict[str, Any] = {}
    if hours is not None and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query["ts"] = {"$gte": cutoff}

    try:
        rows = await db[SHADOW_COLLECTION].find(
            query,
            {
                "_id": 0,
                "shadow_engine": 1,
                "asset_type": 1,
                "is_dissent": 1,
                "regime_at_decision": 1,
                "tactical_score": 1,
            },
        ).to_list(length=100_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-regime-stats] query failed: %s", exc)
        return compute_regime_stats([])

    summary = compute_regime_stats(rows)
    if hours is not None:
        summary["window_hours"] = hours
    return summary


# ── Adaptation shadow summary (companion to ML_ADAPTATION_SHADOW_MODE) ────────


async def fetch_adaptation_shadow_summary(
    db: Any, *, days: int = 14,
) -> dict[str, Any]:
    """Quick rollup of what the auto-revert rail WOULD have done in
    shadow mode over the last ``days``. Lighter-weight companion to
    ``GET /api/admin/adaptations/calibration`` — that endpoint
    computes percentile distributions for threshold tuning; this
    one returns a counts-only "are we observing anything?" view
    suitable for a small dashboard tile.

    Returns:
        {
            "window_days": int,
            "observations": int,
            "shadow_mode_active": bool,
            "by_action": {"shadow_soften": int, "shadow_revert": int},
            "by_metric": [{"metric": str, "count": int}, ...],
            "sample_recent": [{...}, ...],
        }
    """
    from services.model_adaptation import auto_revert_shadow_mode

    safe_days = max(1, min(int(days or 14), 90))
    out: dict[str, Any] = {
        "window_days": safe_days,
        "observations": 0,
        "shadow_mode_active": auto_revert_shadow_mode(),
        "by_action": {"shadow_soften": 0, "shadow_revert": 0},
        "by_metric": [],
        "sample_recent": [],
    }

    if db is None:
        return out

    since_iso = (
        datetime.now(timezone.utc) - timedelta(days=safe_days)
    ).isoformat()

    try:
        # Counts by action.
        cursor = db["adaptation_audit"].aggregate([
            {"$match": {
                "shadow": True,
                "at": {"$gte": since_iso},
                "action": {"$in": ["shadow_soften", "shadow_revert"]},
            }},
            {"$group": {"_id": "$action", "count": {"$sum": 1}}},
        ])
        async for r in cursor:
            out["by_action"][r["_id"]] = int(r["count"])

        out["observations"] = sum(out["by_action"].values())

        # Counts by metric (top 8).
        cursor2 = db["adaptation_audit"].aggregate([
            {"$match": {
                "shadow": True,
                "at": {"$gte": since_iso},
                "action": {"$in": ["shadow_soften", "shadow_revert"]},
            }},
            {"$group": {"_id": "$metric", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ])
        async for r in cursor2:
            out["by_metric"].append({
                "metric": r["_id"], "count": int(r["count"]),
            })

        # Recent sample (5 newest, useful for the operator drawer).
        sample_rows = await db["adaptation_audit"].find(
            {
                "shadow": True,
                "at": {"$gte": since_iso},
                "action": {"$in": ["shadow_soften", "shadow_revert"]},
            },
            {"_id": 0, "adaptation_id": 1, "action": 1, "reason": 1,
             "metric": 1, "at": 1},
        ).sort("at", -1).limit(5).to_list(length=5)
        out["sample_recent"] = sample_rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adaptation-shadow-summary] query failed: %s", exc)
        out["error"] = str(exc)[:120]

    return out


# ── Tier-readiness aggregator (operator gate-flip readiness) ──────────────────


async def fetch_tier_readiness(db: Any) -> dict[str, Any]:
    """Single-shot "am I clear to flip Council on yet?" answer.

    Aggregates four independent inputs into one operator-grade
    readiness payload:

    1. **Adversarial phase** — read from env via the same
       ``adversarial_core._read_phase()`` helper that drives live
       gating. Values: ``shadow``, ``risk_only``, ``veto``, ``full``.
    2. **Tier 3 progress** — composite 0-100 score from
       ``compute_tier3_score`` plus the boolean ``unlocked`` flag.
       Tier 3 is the prerequisite that unlocks the Adversarial
       phase progression in the first place.
    3. **Council bucket gate state** per ``(engine, asset_type)`` —
       reuses the same per-bucket gate function the modulator
       calls at runtime, so this readiness check returns the
       SAME answer the actual gate would. No drift between
       "what the dashboard says" and "what the modulator does".
    4. **Modulator env flag** — reports current value of
       ``COUNCIL_RISK_MODULATOR_ENABLED`` so an operator can see
       at a glance whether they've already flipped it (or
       forgotten to flip it after meeting the readiness gates).

    The composite ``ready_to_enable_council`` boolean is true ONLY
    when:
        - Tier 3 unlocked, AND
        - Adversarial phase == "full", AND
        - At least one Council bucket is open.

    Read-only — no flip button. The operator does the flip via
    .env edit + ``supervisorctl restart backend``.
    """
    from services.adversarial_core import _read_phase
    from services.council_risk_modulator import (
        COUNCIL_RISK_MODULATOR_ENABLED,
    )
    from services.council_tier_gate import (
        MIN_COUNCIL_DISSENTS,
        MIN_COUNCIL_TOTAL_DELTA,
        MIN_COUNCIL_WIN_RATE,
        council_tier_open_for_bucket,
        get_cached_council_stats,
    )
    from services.tier3_readiness import (
        build_tier3_stats,
        check_tier3_unlock,
        compute_tier3_score,
    )

    # ── 1. Adversarial phase ──────────────────────────────────
    try:
        adversarial_phase = _read_phase()
    except Exception:  # noqa: BLE001
        adversarial_phase = "shadow"

    # ── 2. Tier 3 stats + unlock flag ─────────────────────────
    tier3_progress_pct = 0.0
    tier3_unlocked = False
    tier3_blockers: list[str] = []
    try:
        if db is not None:
            t3_stats = await build_tier3_stats(db, days=30)
            tier3_progress_pct = round(compute_tier3_score(t3_stats), 2)
            unlock_view = check_tier3_unlock(t3_stats)
            tier3_unlocked = bool(unlock_view.get("unlocked"))
            tier3_blockers = list(unlock_view.get("reasons") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tier-readiness] tier3 stats failed: %s", exc)
        tier3_blockers = ["tier3_stats_unavailable"]

    # ── 3. Council bucket states ──────────────────────────────
    # Use the live cache so this endpoint sees the SAME data the
    # modulator would see at runtime (no drift between dashboard
    # and gate).
    try:
        shadow_stats = await get_cached_council_stats(db) if db is not None else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tier-readiness] council stats failed: %s", exc)
        shadow_stats = {}

    council_buckets: list[dict] = []
    any_bucket_open = False
    for bucket in (shadow_stats.get("buckets") or []):
        engine = bucket.get("shadow_engine")
        asset_type = bucket.get("asset_type")
        if engine != "council":
            # Tier-readiness is council-specific; adversarial-shadow
            # buckets aren't relevant to the flip decision.
            continue

        is_open = council_tier_open_for_bucket(
            shadow_stats, engine=engine, asset_type=asset_type,
        )
        if is_open:
            any_bucket_open = True

        dissent_count = int(bucket.get("dissent_count") or 0)
        win_rate = bucket.get("win_rate")
        total_delta = float(bucket.get("total_delta_usd") or 0.0)

        # Build a human-readable "needs N more X" string. Picks the
        # MOST blocking unmet threshold so the operator sees one
        # clear next step, not three competing complaints.
        if is_open:
            reason = "actionable — gate open"
            needed_dissents = 0
        else:
            needed_dissents = max(0, MIN_COUNCIL_DISSENTS - dissent_count)
            if needed_dissents > 0:
                reason = f"needs {needed_dissents} more dissents"
            elif (win_rate or 0.0) <= MIN_COUNCIL_WIN_RATE:
                wr_pct = (win_rate or 0.0) * 100
                target_pct = MIN_COUNCIL_WIN_RATE * 100
                reason = (
                    f"win rate {wr_pct:.1f}% needs to clear {target_pct:.0f}%"
                )
            elif total_delta <= MIN_COUNCIL_TOTAL_DELTA:
                reason = (
                    f"total Δ$ {total_delta:.2f} needs to clear "
                    f"${MIN_COUNCIL_TOTAL_DELTA:.2f}"
                )
            else:
                reason = "all thresholds met but gate reports closed"

        council_buckets.append({
            "engine": engine,
            "asset_type": asset_type,
            "open": is_open,
            "dissent_count": dissent_count,
            "needed_dissents": needed_dissents,
            "win_rate": (
                round(float(win_rate), 4) if win_rate is not None else None
            ),
            "total_delta_usd": round(total_delta, 4),
            "reason": reason,
        })

    # Stable ordering: open buckets first (operator sees them at top),
    # then by dissent_count desc.
    council_buckets.sort(
        key=lambda b: (not b["open"], -b["dissent_count"]),
    )

    # ── 4. Composite readiness flag ───────────────────────────
    ready_to_enable_council = (
        tier3_unlocked
        and adversarial_phase == "full"
        and any_bucket_open
    )

    # Build the operator-readable "what's blocking the flip" list
    # so the UI can render a clean checklist without reverse-
    # engineering the boolean.
    next_steps: list[str] = []
    if not tier3_unlocked:
        next_steps.append(
            f"Tier 3 not yet unlocked ({tier3_progress_pct:.1f}/100). "
            f"Blockers: {', '.join(tier3_blockers) or 'unknown'}"
        )
    if adversarial_phase != "full":
        next_steps.append(
            f"Adversarial phase is '{adversarial_phase}', needs to "
            f"reach 'full' before Council can ride along"
        )
    if not any_bucket_open:
        next_steps.append(
            "No Council bucket has cleared all 3 thresholds yet "
            "(see council_buckets[].reason)"
        )
    if (
        ready_to_enable_council
        and not COUNCIL_RISK_MODULATOR_ENABLED
    ):
        next_steps.append(
            "All gates green — set COUNCIL_RISK_MODULATOR_ENABLED=true "
            "in .env and restart backend"
        )

    return {
        "council_modulator_enabled": COUNCIL_RISK_MODULATOR_ENABLED,
        "adversarial_phase": adversarial_phase,
        "tier3_progress_pct": tier3_progress_pct,
        "tier3_unlocked": tier3_unlocked,
        "tier3_blockers": tier3_blockers,
        "ready_to_enable_council": ready_to_enable_council,
        "council_buckets": council_buckets,
        "next_steps": next_steps,
    }
