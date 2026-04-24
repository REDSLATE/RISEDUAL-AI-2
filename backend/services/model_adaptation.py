"""Prescriptive ML adaptation — turns toxic-alert failure patterns
into bounded row-level sample-weight adjustments at retrain time.

Philosophy
----------
XGBoost doesn't take per-feature training weights, so "reduce weight
on low-volume breakouts" is faithfully implemented as: **down-weight
the historical training rows where the toxic pattern fires**. The
model then sees those failure-mode examples as less important,
gradually unlearning the bad generalization.

Every adaptation is:

* **Bounded** — per-metric factor clamped to ``[0.7, 1.3]``; stacked
  multipliers can't drop a row weight below ``0.1 ×`` baseline.
* **Evidenced** — requires ``MIN_EVIDENCE_COUNT`` (3) recent toxic
  alerts with the same metric before triggering.
* **Expiring** — Mongo TTL index on ``expires_at`` so stale
  adaptations auto-decay after ``ADAPTATION_TTL_DAYS`` (14).
* **Reversible** — ``revert_adaptation`` flips ``active=false``;
  ``POST /api/admin/adaptations/disable_all`` is a nuclear switch.
* **Narrated** — every create / apply emits an agent activity event.
* **Opt-in** — the whole engine is gated behind
  ``ML_ADAPTATION_ENABLED=true`` in the environment. Detection
  always runs; application is skipped when the flag is off.

None of this runs inside the critical trading path; it only touches
the nightly retrain job.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

_COLLECTION = "model_adaptations"

# ── Tunables (see module docstring for the safety rationale) ──
MIN_EVIDENCE_COUNT = 3
BASE_DOWN_WEIGHT = 0.85  # used as fallback when severity can't be measured
ADJUSTMENT_FLOOR = 0.7   # hardest a single adaptation can push
ADJUSTMENT_CEILING = 1.3
ADAPTATION_TTL_DAYS = 14
COOLDOWN_DAYS = 7
MAX_ACTIVE_ADAPTATIONS = 4
MIN_CUMULATIVE_WEIGHT = 0.1  # no row can drop below 10% of baseline

# ── Contrast gate ──
# Only adapt when the toxic-bucket's failure rate is materially
# worse than the global baseline. Without this, noisy-but-useful
# metrics (e.g. "RSI > 70" is often right but will sometimes be
# wrong at volume) would get penalized for being noisy, not for
# being systematically bad. The 1.25× multiplier matches the user's
# "materially worse" heuristic.
CONTRAST_MULTIPLIER = 1.25
# How many snapshots we need in the bucket before the rate
# comparison is trustworthy. Below this we fall back to
# "MIN_EVIDENCE_COUNT passed" and skip the contrast gate to avoid
# blocking valid adaptations on small sample size.
MIN_BUCKET_SNAPSHOTS = 50
# Sliding window for contrast + severity measurement. Same as the
# toxic-alert scan window so the two signals are apples-to-apples.
CONTRAST_WINDOW_DAYS = 7

# ── Severity tiers ──
# Scale the down-weight factor by the mean absolute return of the
# failing bucket. Uses the same thresholds as `_WEAK_THRESHOLD` /
# `_STRONG_THRESHOLD` in `ml_retrain_service` so the adaptation
# engine speaks the same "that was a real move" vocabulary as the
# severity-weighted retrain.
SEVERITY_TIERS: list[tuple[float, float]] = [
    # (upper_bound_abs_return, factor)
    (0.01, 0.95),  # mild — barely-a-move misses, light touch
    (0.03, 0.85),  # moderate — standard retail-sized miss
    (float("inf"), 0.75),  # strong — outsized move, stronger push
]

# ── Metric → training-row condition mapping ──
#
# Keys match the dotted namespace used by ``routes.admin._extract_drivers``
# so the same canonical vocabulary flows from alert drilldown →
# adaptation. Each rule carries the feature column it inspects plus
# a boolean condition identifying the "toxic zone".
ADAPTATION_RULES: dict[str, dict[str, Any]] = {
    "volume.liquidity": {
        "column": "volume_ratio",
        "condition": lambda x: x is not None and x < 0.8,
        "description": "low-volume rows (volume_ratio < 0.8x)",
    },
    "volume.spike": {
        "column": "volume_ratio",
        "condition": lambda x: x is not None and x > 2.0,
        "description": "panic-volume rows (volume_ratio > 2.0x)",
    },
    "rsi.overbought": {
        "column": "rsi_14",
        "condition": lambda x: x is not None and x > 70,
        "description": "overbought rows (RSI > 70)",
    },
    "rsi.oversold": {
        "column": "rsi_14",
        "condition": lambda x: x is not None and x < 30,
        "description": "oversold rows (RSI < 30)",
    },
    "macd.crossover": {
        "column": "macd",
        "condition": lambda x: x is not None and x < 0,
        "description": "bearish-MACD rows (macd < 0)",
    },
    "sector.momentum": {
        "column": "sector_momentum",
        "condition": lambda x: x is not None and x < -0.02,
        "description": "negative-sector rows (sector_momentum < -2%)",
    },
    "sentiment.negative": {
        "column": "sentiment_score",
        "condition": lambda x: x is not None and x < -0.3,
        "description": "negative-sentiment rows (sentiment < -0.3)",
    },
    "pattern.bull_flag": {
        "column": "pattern_bull_flag",
        "condition": lambda x: bool(x),
        "description": "bull-flag rows (pattern flag active)",
    },
    "pattern.rsi_divergence": {
        "column": "pattern_rsi_divergence",
        "condition": lambda x: bool(x),
        "description": "RSI-divergence rows",
    },
    "pattern.head_and_shoulders": {
        "column": "pattern_head_and_shoulders",
        "condition": lambda x: bool(x),
        "description": "head-and-shoulders rows",
    },
    "pattern.bearish_engulfing": {
        "column": "pattern_bearish_engulfing",
        "condition": lambda x: bool(x),
        "description": "bearish-engulfing rows",
    },
}

# Map failure_code → canonical metric key. This is how we bridge
# the ChromaDB toxic labelling into the training-pipeline world.
# Kept conservative: one failure_code picks ONE metric (the clearest
# driver), so adaptations don't explode in count.
_FAILURE_CODE_TO_METRIC: dict[str, str] = {
    "LIQUIDITY_GAP": "volume.liquidity",
    "TECH_FAKEOUT": "pattern.bull_flag",
    "REGIME_SHIFT": "rsi.overbought",
    "MACRO_SHOCK": "sector.momentum",
    # UNKNOWN has no clear metric — never triggers adaptation.
}


def adaptation_enabled() -> bool:
    """Environment-gated kill switch. Defaults to False so the
    engine runs in dry-run (detection + narration) mode until the
    operator explicitly flips it on."""
    return os.environ.get("ML_ADAPTATION_ENABLED", "").lower() in (
        "1", "true", "yes", "on",
    )


async def ensure_indexes(db: Any) -> None:
    """Create the TTL + active-lookup indexes on first boot.
    TTL index lets MongoDB auto-delete expired adaptations so the
    collection stays small and stale penalties can't linger."""
    if db is None:
        return
    try:
        await db[_COLLECTION].create_index(
            "expires_at", expireAfterSeconds=0,
        )
        await db[_COLLECTION].create_index([("metric", 1), ("active", 1)])
        await db[_COLLECTION].create_index("adaptation_id", unique=True)
    except Exception as e:
        logger.debug(f"[adaptation] index ensure: {e}")


async def _count_recent_failures(db: Any, window_days: int = 7) -> dict[str, int]:
    """Count how many toxic alerts in the last N days had each
    metric (via their failure_code mapping)."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    pipeline = [
        {"$match": {"alert_type": "toxic_spike",
                    "created_at": {"$gte": since.isoformat()}}},
        {"$project": {
            "_id": 0,
            "spikes": "$metadata.replay_payload.spike_details",
        }},
    ]
    counts: dict[str, int] = {}
    try:
        async for row in db["alerts_sent"].aggregate(pipeline):
            spikes = row.get("spikes") or []
            # One alert can contain multiple toxic predictions with
            # DIFFERENT failure codes. Count each spike once.
            for s in spikes:
                code = s.get("failure_code")
                metric = _FAILURE_CODE_TO_METRIC.get(code or "")
                if metric:
                    counts[metric] = counts.get(metric, 0) + 1
    except Exception as e:
        logger.warning(f"[adaptation] failure count failed: {e}")
    return counts


async def _has_recent_adaptation(db: Any, metric: str) -> bool:
    """Cooldown check — don't stack the same metric within
    ``COOLDOWN_DAYS``. Active OR recently-expired both count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    row = await db[_COLLECTION].find_one({
        "metric": metric,
        "created_at": {"$gte": cutoff.isoformat()},
    }, {"_id": 0, "adaptation_id": 1})
    return row is not None


async def _active_count(db: Any) -> int:
    return int(await db[_COLLECTION].count_documents({"active": True}))


async def _compute_contrast_and_severity(
    db: Any, metric: str,
) -> dict | None:
    """Measure how much worse this bucket fails vs. the global
    baseline, plus the mean absolute return of the failing bucket
    for severity-aware factor selection.

    Returns a dict with:

    * ``bucket_total`` — rows in window where the rule's condition fires
    * ``bucket_failures`` — subset of those with ``outcome='down'``
    * ``global_total`` — all rows in window
    * ``global_failures`` — subset of those with ``outcome='down'``
    * ``bucket_rate`` / ``global_rate`` — failure ratios
    * ``contrast`` — ``bucket_rate / global_rate`` (None when no baseline)
    * ``severity`` — mean absolute ``return_1d`` on the failing bucket
    * ``bucket_snapshots`` — alias of ``bucket_total`` for readability

    "Failure" is approximated by ``outcome='down'`` (model's
    typical error mode is a bullish prediction that reverses —
    we don't store the model's prediction per snapshot directly,
    but this proxy holds for the overwhelming majority of toxic
    events). The proxy is documented on the event metadata so
    drift audits know what's being measured.

    Returns None when the bucket is too small to be trustworthy
    (caller skips the contrast gate in that case).
    """
    rule = ADAPTATION_RULES.get(metric)
    if rule is None:
        return None
    column = rule["column"]

    since = (
        datetime.now(timezone.utc) - timedelta(days=CONTRAST_WINDOW_DAYS)
    )
    # features_snapshots.captured_at is stored as a BSON datetime,
    # NOT an ISO string — a string $gte would return zero rows.
    # Pass the datetime object directly so Mongo does the right
    # tz-aware comparison.

    # Serialize the condition into a Mongo filter. The rule
    # conditions are tiny (single-column threshold / boolean flag)
    # so we can express them declaratively here and let Mongo do
    # the counting server-side.
    # Serialize the condition into a Mongo filter. Typed as
    # ``dict[str, Any]`` so the mixed threshold-vs-boolean shapes
    # for pattern flags vs numeric columns coexist cleanly.
    cond: dict[str, Any]
    if metric == "volume.liquidity":
        cond = {column: {"$lt": 0.8, "$ne": None}}
    elif metric == "volume.spike":
        cond = {column: {"$gt": 2.0}}
    elif metric == "rsi.overbought":
        cond = {column: {"$gt": 70}}
    elif metric == "rsi.oversold":
        cond = {column: {"$lt": 30, "$ne": None}}
    elif metric == "macd.crossover":
        cond = {column: {"$lt": 0, "$ne": None}}
    elif metric == "sector.momentum":
        cond = {column: {"$lt": -0.02, "$ne": None}}
    elif metric == "sentiment.negative":
        cond = {column: {"$lt": -0.3, "$ne": None}}
    elif metric.startswith("pattern."):
        cond = {column: True}
    else:
        return None

    base_window = {
        "captured_at": {"$gte": since},
        "outcome": {"$in": ["up", "down", "flat"]},
    }

    try:
        coll = db["features_snapshots"]
        global_total = await coll.count_documents(base_window)
        if global_total == 0:
            return None
        global_failures = await coll.count_documents(
            {**base_window, "outcome": "down"},
        )
        bucket_total = await coll.count_documents({**base_window, **cond})
        bucket_failures = await coll.count_documents(
            {**base_window, **cond, "outcome": "down"},
        )
    except Exception as e:
        logger.warning(f"[adaptation] contrast stats failed for {metric}: {e}")
        return None

    if bucket_total < MIN_BUCKET_SNAPSHOTS:
        # Caller can still create the adaptation (MIN_EVIDENCE_COUNT
        # already cleared) but we'll skip the contrast multiplier
        # gate. Severity remains best-effort.
        bucket_rate: float | None = None
        contrast: float | None = None
    else:
        bucket_rate = bucket_failures / bucket_total
        global_rate_local = global_failures / global_total
        contrast = (
            bucket_rate / global_rate_local if global_rate_local > 0 else None
        )

    # Severity — mean |return_1d| on the FAILING rows of this
    # bucket. If nothing failed in the bucket yet we fall back to
    # the bucket mean so the factor ladder still has a number.
    severity = 0.0
    try:
        sev_pipeline = [
            {"$match": {
                **base_window,
                **cond,
                "outcome": "down",
                "return_1d": {"$ne": None},
            }},
            {"$group": {
                "_id": None,
                "sev": {"$avg": {"$abs": "$return_1d"}},
            }},
        ]
        async for row in coll.aggregate(sev_pipeline):
            if row.get("sev") is not None:
                severity = float(row["sev"])
                break
    except Exception as e:
        logger.debug(f"[adaptation] severity calc failed for {metric}: {e}")

    return {
        "metric": metric,
        "bucket_total": bucket_total,
        "bucket_snapshots": bucket_total,
        "bucket_failures": bucket_failures,
        "global_total": global_total,
        "global_failures": global_failures,
        "bucket_rate": (
            round(bucket_rate, 4) if bucket_rate is not None else None
        ),
        "global_rate": round(global_failures / global_total, 4) if global_total else 0.0,
        "contrast": round(contrast, 3) if contrast is not None else None,
        "severity": round(severity, 4),
    }


def _factor_from_severity(severity: float) -> float:
    """Map severity (mean |return_1d| on failures) to a bounded
    down-weight factor. Mild misses → light touch, strong misses
    → firmer push. Always clamped to the ADJUSTMENT_FLOOR/CEILING
    band so a rogue severity value can't push outside limits.
    """
    for upper, factor in SEVERITY_TIERS:
        if severity < upper:
            return max(min(factor, ADJUSTMENT_CEILING), ADJUSTMENT_FLOOR)
    return BASE_DOWN_WEIGHT  # unreachable in practice, kept for safety


async def detect_and_create_adaptations(db: Any) -> list[dict]:
    """Scan recent toxic alerts, create bounded adaptations for
    metrics that cleared the evidence threshold. Returns the list of
    newly-created adaptations (may be empty).

    Always safe to call — cooldown + max-active limits prevent
    runaway creation. Does not touch training weights itself.
    """
    from services.agent_activity_service import log_retrain_adaptation_planned

    counts = await _count_recent_failures(db)
    new_rows: list[dict] = []
    for metric, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        if count < MIN_EVIDENCE_COUNT:
            continue
        if await _has_recent_adaptation(db, metric):
            continue
        if await _active_count(db) >= MAX_ACTIVE_ADAPTATIONS:
            logger.info(
                f"[adaptation] MAX_ACTIVE_ADAPTATIONS reached, "
                f"skipping {metric}"
            )
            break

        now = datetime.now(timezone.utc)
        rule = ADAPTATION_RULES.get(metric)
        if rule is None:
            continue  # mapping drift — metric without a rule is a no-op

        # Contrast gate — only adapt when this bucket fails more
        # than the global baseline. Bypassed when the bucket is
        # too small for the rate comparison to be trustworthy
        # (MIN_BUCKET_SNAPSHOTS); the evidence_count threshold
        # still governs those cases.
        stats = await _compute_contrast_and_severity(db, metric)
        if stats is None:
            logger.info(
                f"[adaptation] skip {metric}: no contrast stats "
                f"(rule mapping or DB error)"
            )
            continue
        contrast = stats.get("contrast")
        if contrast is not None and contrast < CONTRAST_MULTIPLIER:
            # Bucket fails less than 1.25× the baseline — the metric
            # isn't systematically bad, just appearing in recent
            # toxic alerts. Skipping protects useful-but-noisy
            # signals from being down-weighted.
            logger.info(
                f"[adaptation] skip {metric}: contrast={contrast:.3f} "
                f"< {CONTRAST_MULTIPLIER} (bucket_rate="
                f"{stats.get('bucket_rate')} vs global="
                f"{stats.get('global_rate')})"
            )
            continue

        adaptation_id = str(uuid.uuid4())
        # Severity-aware factor. Falls through to BASE_DOWN_WEIGHT
        # when severity couldn't be computed (bucket had no failing
        # rows with return_1d populated). Belt-and-braces clamp.
        severity = float(stats.get("severity") or 0.0)
        if severity > 0:
            factor = _factor_from_severity(severity)
        else:
            factor = max(min(BASE_DOWN_WEIGHT, ADJUSTMENT_CEILING), ADJUSTMENT_FLOOR)
        row = {
            "adaptation_id": adaptation_id,
            "metric": metric,
            "column": rule["column"],
            "description": rule["description"],
            "adjustment_factor": factor,
            "evidence_count": count,
            "contrast": contrast,
            "bucket_rate": stats.get("bucket_rate"),
            "global_rate": stats.get("global_rate"),
            "severity": severity,
            "bucket_snapshots": stats.get("bucket_snapshots"),
            "created_at": now.isoformat(),
            "expires_at": now + timedelta(days=ADAPTATION_TTL_DAYS),
            "active": True,
            "reverted_at": None,
        }
        try:
            await db[_COLLECTION].insert_one(row.copy())
            # Strip the datetime for the return payload.
            row["expires_at"] = row["expires_at"].isoformat()
            new_rows.append(row)
            logger.info(
                f"[adaptation] created {metric} factor={factor:.2f} "
                f"evidence={count} contrast={contrast} severity={severity:.4f} "
                f"(enabled={adaptation_enabled()})"
            )
            try:
                await log_retrain_adaptation_planned(
                    metric=metric,
                    factor=factor,
                    evidence_count=count,
                    description=rule["description"],
                    enabled=adaptation_enabled(),
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[adaptation] failed to persist {metric}: {e}")

    return new_rows


async def list_active_adaptations(db: Any) -> list[dict]:
    """Returns active adaptation rows, sorted by created_at desc.
    Strips ``_id`` (ObjectId) and serializes ``expires_at`` for
    JSON safety."""
    rows: list[dict] = []
    cursor = db[_COLLECTION].find({"active": True}, {"_id": 0}).sort("created_at", -1)
    async for r in cursor:
        exp = r.get("expires_at")
        if isinstance(exp, datetime):
            r["expires_at"] = exp.isoformat()
        rows.append(r)
    return rows


async def revert_adaptation(db: Any, adaptation_id: str) -> bool:
    """Flip an adaptation to inactive so the next retrain ignores
    it. Does not delete (audit trail)."""
    res = await db[_COLLECTION].update_one(
        {"adaptation_id": adaptation_id, "active": True},
        {"$set": {
            "active": False,
            "reverted_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return bool(res.matched_count)


async def disable_all_adaptations(db: Any) -> int:
    """Kill switch — flip every active adaptation to inactive.
    Returns the count affected."""
    res = await db[_COLLECTION].update_many(
        {"active": True},
        {"$set": {
            "active": False,
            "reverted_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return int(res.modified_count)


async def apply_adaptations_to_weights(db: Any, df: Any, w: Any) -> tuple[Any, list[dict]]:
    """Multiply per-row sample weights by each active adaptation's
    factor for the rows that match the adaptation's condition.

    Returns ``(adjusted_weights, applied_summary)``. When
    ``ML_ADAPTATION_ENABLED`` is false this is a dry-run: the
    summary reflects what WOULD change but ``w`` is returned
    unmodified.

    Safety:
      * Cumulative multiplier per row is clamped to
        ``[MIN_CUMULATIVE_WEIGHT, 1.0]`` — an adaptation can only
        DECREASE a row's contribution; it can never amplify.
      * Missing column → adaptation is skipped for that row set
        (can't apply if we don't have the feature).
    """
    import numpy as np
    import pandas as pd

    adaptations = await list_active_adaptations(db)
    if not adaptations:
        return w, []

    enabled = adaptation_enabled()
    cumulative = pd.Series([1.0] * len(df), index=df.index, dtype=float)
    summary: list[dict] = []
    for ad in adaptations:
        col = ad.get("column")
        if col not in df.columns:
            summary.append({
                "adaptation_id": ad["adaptation_id"],
                "metric": ad["metric"],
                "rows_matched": 0,
                "skipped_reason": f"column_missing:{col}",
            })
            continue
        rule = ADAPTATION_RULES.get(ad["metric"])
        if rule is None:
            continue
        condition: Callable[[Any], bool] = rule["condition"]
        mask = df[col].apply(condition).astype(bool)
        n_match = int(mask.sum())
        factor = float(ad.get("adjustment_factor", 1.0))
        # Clamp factor into the allowed band as a belt-and-braces
        # defence against malformed DB rows.
        factor = max(min(factor, ADJUSTMENT_CEILING), ADJUSTMENT_FLOOR)
        if n_match > 0:
            cumulative.loc[mask] = cumulative.loc[mask] * factor
        summary.append({
            "adaptation_id": ad["adaptation_id"],
            "metric": ad["metric"],
            "column": col,
            "factor": factor,
            "rows_matched": n_match,
        })

    # Floor the cumulative multiplier so stacking doesn't nuke a
    # row entirely. Also cap at 1.0 — adaptations only down-weight.
    cumulative = cumulative.clip(lower=MIN_CUMULATIVE_WEIGHT, upper=1.0)

    if enabled:
        adjusted = w * cumulative
    else:
        adjusted = w  # dry-run: return untouched weights

    # Narrate the apply step so admins see exactly what the retrain
    # saw. Fire-and-forget; never blocks.
    try:
        from services.agent_activity_service import log_retrain_adaptation_applied
        total_matched = sum(int(s.get("rows_matched", 0)) for s in summary)
        await log_retrain_adaptation_applied(
            enabled=enabled,
            adaptations=summary,
            total_matched=total_matched,
        )
    except Exception:
        pass

    return np.asarray(adjusted), summary
