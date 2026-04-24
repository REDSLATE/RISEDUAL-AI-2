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
from typing import Any, Callable, Literal, overload

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


# ── Auto-soften / auto-revert safety rail ──
# Builds on the ΔR attribution layer. Instead of binary kill, we
# step the factor UP (toward 1.0 = no effect) in measured
# increments across consecutive bad retrains — 0.85 → 0.90 → 0.95
# → inactive. This is "measured self-correction" (gentler than
# revert, still firm when the evidence stacks).
#
# Guardrails (hardened from "ΔR < 0 → revert" naive take):
#   * Gated by ``ML_ADAPTATION_AUTO_REVERT_ENABLED`` (default off)
#   * ``AUTO_REVERT_CONSECUTIVE_NEGATIVE`` runs of evidence
#   * Epsilon floor — ignore "negative but noisy" deltas
#   * Coverage floor — don't act on adaptations that barely touch
#     the training set (small samples can't earn statistical
#     confidence in ΔR)
#   * Risk-compression guard — if Δwin_rate > 0 the adaptation may
#     be trading wins for smaller losses (downside control). We
#     require BOTH ΔR negative AND Δwin_rate non-positive to act.
#   * Factor-match — only count retrains that ran at the CURRENT
#     factor. After softening, the 3-run counter resets so the
#     rule gets a fresh window at its new strength before the next
#     step. Prevents rapid-fire 0.85→0.95→off collapse.
#   * Cooldown — re-creation is already gated by the
#     ``_has_recent_adaptation(COOLDOWN_DAYS=7)`` check in
#     detection; auto-reverted rows (created within the window)
#     count, so a flip-flop loop is structurally impossible.
#   * Grace period — require ≥ MIN_RETRAINS runs at the current
#     factor before considering. No adaptation gets touched on its
#     first retrain.
AUTO_REVERT_CONSECUTIVE_NEGATIVE = 3
AUTO_REVERT_EPSILON = 0.01  # |ΔR| < 0.01 treated as "no effect"
AUTO_REVERT_MIN_COVERAGE = 0.05  # need ≥5% row coverage to act
AUTO_SOFTEN_STEP = 0.05  # per-hit increment on adjustment_factor
AUTO_SOFTEN_MAX_FACTOR = 0.95  # next step above → disable instead


def auto_revert_enabled() -> bool:
    """Secondary kill switch — ``ML_ADAPTATION_AUTO_REVERT_ENABLED``
    defaults to off so the safety rail is opt-in. Pairs with
    ``ML_ADAPTATION_ENABLED`` — when the primary switch is off we
    skip anyway since no weights were moved."""
    return os.environ.get("ML_ADAPTATION_AUTO_REVERT_ENABLED", "").lower() in (
        "1", "true", "yes", "on",
    )


async def evaluate_auto_revert_candidates(db: Any) -> list[dict]:
    """Scan active adaptations and for each one whose last
    `AUTO_REVERT_CONSECUTIVE_NEGATIVE` retrains AT THE CURRENT
    FACTOR meet every gate (consistent negative ΔR + coverage +
    no risk-compression), either:

    * **Soften** the adaptation — raise its ``adjustment_factor``
      by ``AUTO_SOFTEN_STEP`` (closer to 1.0 = less down-weight),
      OR
    * **Revert** it — flip ``active=False`` when the next step
      would push the factor above ``AUTO_SOFTEN_MAX_FACTOR``.

    Returns the list of actions taken (each record tagged with
    ``action`` ∈ {"soften", "revert"}) so the caller can narrate.
    Safe to call once per retrain — every candidate is evaluated
    independently and the update is idempotent (the ``active:True``
    gate makes concurrent operator reverts a no-op on our side).
    """
    if not auto_revert_enabled():
        return []

    active = await list_active_adaptations(db)
    if not active:
        return []

    actions: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for ad in active:
        aid = ad.get("adaptation_id")
        if not aid:
            continue
        current_factor = float(ad.get("adjustment_factor") or 1.0)

        # Pull the most recent retrain rows that applied THIS
        # adaptation AT THE CURRENT FACTOR. We only consider runs
        # where ``adaptations_applied[i].factor == current_factor``
        # so softening resets the evidence counter — the rule gets
        # a fresh 3-run window to prove itself at its new strength
        # before the next step.
        cursor = (
            db["ml_training_log"]
            .find(
                {"adaptations_applied.adaptation_id": aid},
                {"_id": 0, "started_at": 1, "samples": 1,
                 "adaptations_applied": 1, "model_version": 1},
            )
            .sort("started_at", -1)
            .limit(AUTO_REVERT_CONSECUTIVE_NEGATIVE * 3)  # headroom
        )
        runs = await cursor.to_list(length=AUTO_REVERT_CONSECUTIVE_NEGATIVE * 3)

        # Filter runs whose recorded factor matches the current one
        # (within a small tolerance — rounding in the log row).
        deltas_r: list[float] = []
        deltas_wr: list[float] = []
        coverages: list[float] = []
        complete = True
        for run in runs:
            if len(deltas_r) >= AUTO_REVERT_CONSECUTIVE_NEGATIVE:
                break
            match = next(
                (a for a in (run.get("adaptations_applied") or [])
                 if a.get("adaptation_id") == aid),
                None,
            )
            if match is None:
                continue
            run_factor = match.get("factor")
            if run_factor is None or abs(float(run_factor) - current_factor) > 0.001:
                # Run used a different factor (pre-soften) — skip,
                # not out-of-scope for the current window.
                continue
            d_r = match.get("delta_mean_r")
            d_wr = match.get("delta_win_rate")
            rows_m = match.get("rows_matched") or 0
            samples = run.get("samples") or 0
            if d_r is None or samples <= 0:
                complete = False
                break
            try:
                deltas_r.append(float(d_r))
                deltas_wr.append(float(d_wr) if d_wr is not None else 0.0)
                coverages.append(rows_m / samples if samples else 0.0)
            except (TypeError, ValueError):
                complete = False
                break
        if not complete or len(deltas_r) < AUTO_REVERT_CONSECUTIVE_NEGATIVE:
            continue

        # ── Gate 1: consistently negative ΔR below epsilon ──
        if not all(d < -AUTO_REVERT_EPSILON for d in deltas_r):
            continue
        # ── Gate 2: sufficient coverage on at least one run ──
        if max(coverages) < AUTO_REVERT_MIN_COVERAGE:
            continue
        # ── Gate 3: not just risk compression ──
        if not all(wr <= 0 for wr in deltas_wr):
            continue

        # All gates trip — decide between soften and revert.
        next_factor = round(current_factor + AUTO_SOFTEN_STEP, 4)
        is_final_step = next_factor > AUTO_SOFTEN_MAX_FACTOR

        base_reason_parts = (
            f"ΔR={[round(d, 4) for d in deltas_r]}, "
            f"Δwin_rate={[round(w, 4) for w in deltas_wr]}, "
            f"coverage={[round(c, 3) for c in coverages]}, "
            f"factor={current_factor}"
        )

        if is_final_step:
            # Next step would exceed MAX — flip inactive.
            reason = (
                f"delta_mean_r negative x{AUTO_REVERT_CONSECUTIVE_NEGATIVE} "
                f"at softened factor ({current_factor:.2f} ≥ "
                f"MAX {AUTO_SOFTEN_MAX_FACTOR:.2f}); final revert. "
                f"{base_reason_parts}"
            )
            res = await db[_COLLECTION].update_one(
                {"adaptation_id": aid, "active": True},
                {"$set": {
                    "active": False,
                    "reverted_at": now_iso,
                    "auto_reverted": True,
                    "auto_reverted_reason": reason,
                    "auto_reverted_deltas_r": [round(d, 4) for d in deltas_r],
                    "auto_reverted_deltas_wr": [round(w, 4) for w in deltas_wr],
                    "auto_reverted_coverages": [round(c, 3) for c in coverages],
                }},
            )
            if res.modified_count:
                actions.append({
                    "action": "revert",
                    "adaptation_id": aid,
                    "metric": ad.get("metric"),
                    "direction": ad.get("direction"),
                    "factor": current_factor,
                    "next_factor": None,
                    "deltas_r": [round(d, 4) for d in deltas_r],
                    "deltas_wr": [round(w, 4) for w in deltas_wr],
                    "coverages": [round(c, 3) for c in coverages],
                    "reason": reason,
                })
                try:
                    await db["adaptation_audit"].insert_one({
                        "adaptation_id": aid,
                        "action": "auto_revert",
                        "reason": reason,
                        "metric": ad.get("metric"),
                        "direction": ad.get("direction"),
                        "factor_before": current_factor,
                        "factor_after": None,
                        "deltas_r": [round(d, 4) for d in deltas_r],
                        "deltas_wr": [round(w, 4) for w in deltas_wr],
                        "coverages": [round(c, 3) for c in coverages],
                        "at": now_iso,
                    })
                except Exception as audit_err:
                    logger.warning(f"[auto-revert] audit insert failed: {audit_err}")
        else:
            # Soften — raise factor toward 1.0, leave active.
            reason = (
                f"delta_mean_r negative x{AUTO_REVERT_CONSECUTIVE_NEGATIVE} — "
                f"softening {current_factor:.2f} → {next_factor:.2f}. "
                f"{base_reason_parts}"
            )
            res = await db[_COLLECTION].update_one(
                {"adaptation_id": aid, "active": True},
                {"$set": {
                    "adjustment_factor": next_factor,
                    "auto_softened": True,
                    "last_auto_softened_at": now_iso,
                    "auto_softening_reason": reason,
                },
                 "$inc": {"auto_softening_steps": 1}},
            )
            if res.modified_count:
                actions.append({
                    "action": "soften",
                    "adaptation_id": aid,
                    "metric": ad.get("metric"),
                    "direction": ad.get("direction"),
                    "factor": current_factor,
                    "next_factor": next_factor,
                    "deltas_r": [round(d, 4) for d in deltas_r],
                    "deltas_wr": [round(w, 4) for w in deltas_wr],
                    "coverages": [round(c, 3) for c in coverages],
                    "reason": reason,
                })
                try:
                    await db["adaptation_audit"].insert_one({
                        "adaptation_id": aid,
                        "action": "auto_soften",
                        "reason": reason,
                        "metric": ad.get("metric"),
                        "direction": ad.get("direction"),
                        "factor_before": current_factor,
                        "factor_after": next_factor,
                        "deltas_r": [round(d, 4) for d in deltas_r],
                        "deltas_wr": [round(w, 4) for w in deltas_wr],
                        "coverages": [round(c, 3) for c in coverages],
                        "at": now_iso,
                    })
                except Exception as audit_err:
                    logger.warning(f"[auto-soften] audit insert failed: {audit_err}")

    # Narrate into the agent activity feed. Fire-and-forget — a
    # feed failure must never break the retrain.
    if actions:
        try:
            from services.agent_activity_service import log_event
            softens = [a for a in actions if a["action"] == "soften"]
            reverts = [a for a in actions if a["action"] == "revert"]
            if softens:
                metrics = ", ".join(
                    f"{a['metric']}/{a.get('direction') or 'ANY'} "
                    f"({a['factor']:.2f}→{a['next_factor']:.2f})"
                    for a in softens
                )
                await log_event(
                    type="adaptation_auto_softened",
                    severity="info",
                    title=(
                        f"Softened {len(softens)} adaptation"
                        f"{'s' if len(softens) != 1 else ''} · "
                        f"ΔR negative × {AUTO_REVERT_CONSECUTIVE_NEGATIVE}"
                    ),
                    detail=f"Metrics: {metrics}",
                    metadata={"softened": softens},
                )
            if reverts:
                metrics = ", ".join(
                    f"{a['metric']}/{a.get('direction') or 'ANY'}"
                    for a in reverts
                )
                await log_event(
                    type="adaptation_auto_reverted",
                    severity="warn",
                    title=(
                        f"Auto-reverted {len(reverts)} adaptation"
                        f"{'s' if len(reverts) != 1 else ''} · "
                        f"reached softening ceiling"
                    ),
                    detail=f"Metrics: {metrics}",
                    metadata={"reverted": reverts},
                )
        except Exception as e:
            logger.warning(f"[auto-revert] activity log failed: {e}")

    return actions




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


async def _has_recent_adaptation(db: Any, metric: str, direction: str = "ANY") -> bool:
    """Cooldown check — don't stack the same (metric, direction)
    pair within ``COOLDOWN_DAYS``. Active OR recently-expired both
    count. Legacy rows without a ``direction`` field are treated
    as ``ANY`` so the cooldown still fires for them."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    dir_filter: dict[str, Any]
    if direction == "ANY":
        dir_filter = {"$or": [
            {"direction": "ANY"}, {"direction": {"$exists": False}},
        ]}
    else:
        dir_filter = {"direction": direction}
    row = await db[_COLLECTION].find_one(
        {
            "metric": metric,
            "created_at": {"$gte": cutoff.isoformat()},
            **dir_filter,
        },
        {"_id": 0, "adaptation_id": 1},
    )
    return row is not None


async def _active_count(db: Any) -> int:
    return int(await db[_COLLECTION].count_documents({"active": True}))


async def _compute_contrast_and_severity(
    db: Any, metric: str, direction: str = "ANY",
) -> dict | None:
    """Measure how much worse this bucket fails vs. the global
    baseline, plus the mean absolute return of the failing bucket
    for severity-aware factor selection.

    ``direction`` is one of:

    * ``"LONG"``  — failure proxy is ``outcome='down'`` (a long
      bet would have lost). Baseline is the global LONG-failure rate.
    * ``"SHORT"`` — failure proxy is ``outcome='up'`` (a short
      bet would have lost). Baseline is the global SHORT-failure rate.
    * ``"ANY"``   — failure proxy is ``outcome='down'`` (matches the
      original non-directional behavior — most of our toxic events
      are long-side, so this stays the sensible default when we
      can't infer direction from the alert).

    Returns a dict with:

    * ``bucket_total`` — rows in window where the rule's condition fires
    * ``bucket_failures`` — subset of those where the directional
      failure condition fires
    * ``global_total`` — all rows in window
    * ``global_failures`` — subset of those with the directional
      failure
    * ``bucket_rate`` / ``global_rate`` — failure ratios
    * ``contrast`` — ``bucket_rate / global_rate`` (None when no baseline)
    * ``severity`` — mean absolute ``return_1d`` on the failing bucket
    * ``bucket_snapshots`` — alias of ``bucket_total`` for readability

    We don't store the model's prediction per snapshot directly, so
    the proxy is documented on every adaptation row (``direction``)
    for drift audit.

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

    # Directional failure proxy. LONG failure = price went down
    # (a buy would have lost); SHORT failure = price went up.
    # ANY keeps legacy behaviour (= LONG).
    fail_outcome = "up" if direction == "SHORT" else "down"

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
            {**base_window, "outcome": fail_outcome},
        )
        bucket_total = await coll.count_documents({**base_window, **cond})
        bucket_failures = await coll.count_documents(
            {**base_window, **cond, "outcome": fail_outcome},
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
        rule = ADAPTATION_RULES.get(metric)
        if rule is None:
            continue

        # Directional split — compute contrast + severity separately
        # for the LONG side (outcome='down' proxy) and the SHORT
        # side (outcome='up' proxy). Create one adaptation per
        # direction that clears the contrast gate, so the engine
        # learns "low volume is bad for LONG, not SHORT" when the
        # data actually says that. Subject to MAX_ACTIVE.
        directions_to_try = ["LONG", "SHORT"]
        for direction in directions_to_try:
            if await _has_recent_adaptation(db, metric, direction):
                continue
            if await _active_count(db) >= MAX_ACTIVE_ADAPTATIONS:
                logger.info(
                    "[adaptation] MAX_ACTIVE_ADAPTATIONS reached, "
                    "skipping remaining"
                )
                break

            stats = await _compute_contrast_and_severity(db, metric, direction)
            if stats is None:
                continue
            contrast = stats.get("contrast")
            if contrast is not None and contrast < CONTRAST_MULTIPLIER:
                logger.info(
                    f"[adaptation] skip {metric}/{direction}: contrast="
                    f"{contrast:.3f} < {CONTRAST_MULTIPLIER}"
                )
                continue

            now = datetime.now(timezone.utc)
            adaptation_id = str(uuid.uuid4())
            severity = float(stats.get("severity") or 0.0)
            if severity > 0:
                factor = _factor_from_severity(severity)
            else:
                factor = max(min(BASE_DOWN_WEIGHT, ADJUSTMENT_CEILING), ADJUSTMENT_FLOOR)
            row = {
                "adaptation_id": adaptation_id,
                "metric": metric,
                "direction": direction,
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
                row["expires_at"] = row["expires_at"].isoformat()
                new_rows.append(row)
                logger.info(
                    f"[adaptation] created {metric}/{direction} "
                    f"factor={factor:.2f} evidence={count} "
                    f"contrast={contrast} severity={severity:.4f} "
                    f"(enabled={adaptation_enabled()})"
                )
                try:
                    await log_retrain_adaptation_planned(
                        metric=f"{metric} ({direction})",
                        factor=factor,
                        evidence_count=count,
                        description=rule["description"],
                        enabled=adaptation_enabled(),
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.warning(
                    f"[adaptation] failed to persist {metric}/{direction}: {e}"
                )

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


@overload
async def apply_adaptations_to_weights(
    db: Any, df: Any, w: Any, y: Any | None = ...,
    *, return_masks: Literal[False] = ...,
) -> tuple[Any, list[dict]]: ...


@overload
async def apply_adaptations_to_weights(
    db: Any, df: Any, w: Any, y: Any | None = ...,
    *, return_masks: Literal[True],
) -> tuple[Any, list[dict], list[Any]]: ...


async def apply_adaptations_to_weights(
    db: Any, df: Any, w: Any, y: Any | None = None,
    return_masks: bool = False,
) -> tuple[Any, list[dict]] | tuple[Any, list[dict], list[Any]]:
    """Multiply per-row sample weights by each active adaptation's
    factor for the rows that match the adaptation's condition AND
    directional proxy.

    Returns ``(adjusted_weights, applied_summary)`` by default.
    When ``return_masks=True``, returns a 3-tuple with an extra
    ``per_adaptation_masks`` list aligned with ``applied_summary``
    — each entry is a boolean pandas Series over ``df.index``
    marking the rows that particular adaptation touched. Used by
    the retrain pipeline for per-adaptation impact attribution.

    When ``ML_ADAPTATION_ENABLED`` is false this is a dry-run: the
    summary reflects what WOULD change but ``w`` is returned
    unmodified.

    Directional filter: each adaptation carries a ``direction``
    field (``LONG`` / ``SHORT`` / ``ANY``). When ``y`` is provided,
    ``LONG`` adaptations only match rows where ``y == 0`` (the
    bearish-outcome label), ``SHORT`` only where ``y == 1``, and
    ``ANY`` matches all. When ``y`` is not provided we fall back to
    non-directional filtering so legacy callers keep working.

    Safety:
      * Cumulative multiplier per row is clamped to
        ``[MIN_CUMULATIVE_WEIGHT, 1.0]`` — an adaptation can only
        DECREASE a row's contribution; it can never amplify.
      * Missing column → adaptation is skipped for that row set.
    """
    import numpy as np
    import pandas as pd

    adaptations = await list_active_adaptations(db)
    if not adaptations:
        empty_masks: list[Any] = []
        if return_masks:
            return w, [], empty_masks
        return w, []

    enabled = adaptation_enabled()
    cumulative = pd.Series([1.0] * len(df), index=df.index, dtype=float)
    # y is derived from outcome (binarized bullish/bearish). In the
    # existing retrain pipeline, y==0 corresponds to bearish rows
    # ("LONG would have lost"), y==1 to bullish ("SHORT would have
    # lost"). If the label semantics ever invert, this mapping is
    # the one place to flip.
    y_arr: Any = None
    if y is not None:
        y_arr = np.asarray(y).astype(int)
        if len(y_arr) != len(df):
            y_arr = None  # shape mismatch — skip directional filtering

    summary: list[dict] = []
    masks: list[Any] = []
    for ad in adaptations:
        col = ad.get("column")
        direction = ad.get("direction", "ANY")
        if col not in df.columns:
            summary.append({
                "adaptation_id": ad["adaptation_id"],
                "metric": ad["metric"],
                "direction": direction,
                "rows_matched": 0,
                "skipped_reason": f"column_missing:{col}",
            })
            masks.append(pd.Series([False] * len(df), index=df.index))
            continue
        rule = ADAPTATION_RULES.get(ad["metric"])
        if rule is None:
            masks.append(pd.Series([False] * len(df), index=df.index))
            continue
        condition: Callable[[Any], bool] = rule["condition"]
        cond_mask = df[col].apply(condition).astype(bool)
        if direction == "LONG" and y_arr is not None:
            mask = cond_mask & (pd.Series(y_arr == 0, index=df.index))
        elif direction == "SHORT" and y_arr is not None:
            mask = cond_mask & (pd.Series(y_arr == 1, index=df.index))
        else:
            mask = cond_mask
        n_match = int(mask.sum())
        factor = float(ad.get("adjustment_factor", 1.0))
        # Clamp factor into the allowed band as a belt-and-braces
        # defence against malformed DB rows.
        factor = max(min(factor, ADJUSTMENT_CEILING), ADJUSTMENT_FLOOR)
        if n_match > 0:
            cumulative.loc[mask] = cumulative.loc[mask] * factor
        masks.append(mask)
        summary.append({
            "adaptation_id": ad["adaptation_id"],
            "metric": ad["metric"],
            "direction": direction,
            "column": col,
            "factor": factor,
            "rows_matched": n_match,
            # Narrative fields — surfaced in the activity feed and
            # used by /api/admin/adaptations/why/{id}. Defensively
            # cast so a malformed DB row never crashes the retrain.
            "lift": (float(ad["contrast"]) if ad.get("contrast") is not None else None),
            "severity": (float(ad["severity"]) if ad.get("severity") is not None else None),
            "evidence_count": int(ad.get("evidence_count") or 0),
            "description": ad.get("description"),
        })

    # Floor the cumulative multiplier so stacking doesn't nuke a
    # row entirely. Also cap at 1.0 — adaptations only down-weight.
    cumulative = cumulative.clip(lower=MIN_CUMULATIVE_WEIGHT, upper=1.0)

    if enabled:
        adjusted = w * cumulative
    else:
        adjusted = w  # dry-run: return untouched weights

    # Mean weight before/after — observable evidence of the
    # adaptation's effect on the training distribution. When
    # disabled (dry-run) the values are identical by design.
    try:
        mean_before = float(pd.Series(w).astype(float).mean())
        mean_after = float(pd.Series(adjusted).astype(float).mean())
    except Exception:
        mean_before = mean_after = 0.0

    # Narrate the apply step so admins see exactly what the retrain
    # saw. Fire-and-forget; never blocks. Compute a projected
    # impact stat: "coverage" of the last 50 resolved-or-pending
    # predictions — how many would fall into at least one adapted
    # bucket next retrain. Informational (no training effect).
    projected_impact = await _projected_impact(db, adaptations)
    try:
        from services.agent_activity_service import log_retrain_adaptation_applied
        total_matched = sum(int(s.get("rows_matched", 0)) for s in summary)
        await log_retrain_adaptation_applied(
            enabled=enabled,
            adaptations=summary,
            total_matched=total_matched,
            projected_impact=projected_impact,
            mean_weight_before=mean_before,
            mean_weight_after=mean_after,
        )
    except Exception:
        pass

    return (np.asarray(adjusted), summary, masks) if return_masks else (np.asarray(adjusted), summary)


async def _projected_impact(db: Any, adaptations: list[dict]) -> dict:
    """Coverage statistic: of the last ~50 resolved predictions,
    how many would fall into at least one active adapted bucket?

    This is descriptive, not predictive — we don't simulate the
    new model, we just measure the relevance of the current
    adaptation set. Safe to run (read-only aggregate over
    features_snapshots).
    """
    if not adaptations:
        return {"sample_size": 0, "matched_predictions": 0, "coverage": None}

    try:
        # Build an $or clause that mirrors the adaptation conditions,
        # each scoped to the appropriate direction where known.
        clauses: list[dict] = []
        for ad in adaptations:
            metric = ad.get("metric")
            col = ad.get("column")
            direction = ad.get("direction", "ANY")
            if not col:
                continue
            if metric == "volume.liquidity":
                cond: dict[str, Any] = {col: {"$lt": 0.8, "$ne": None}}
            elif metric == "volume.spike":
                cond = {col: {"$gt": 2.0}}
            elif metric == "rsi.overbought":
                cond = {col: {"$gt": 70}}
            elif metric == "rsi.oversold":
                cond = {col: {"$lt": 30, "$ne": None}}
            elif metric == "macd.crossover":
                cond = {col: {"$lt": 0, "$ne": None}}
            elif metric == "sector.momentum":
                cond = {col: {"$lt": -0.02, "$ne": None}}
            elif metric == "sentiment.negative":
                cond = {col: {"$lt": -0.3, "$ne": None}}
            elif (metric or "").startswith("pattern."):
                cond = {col: True}
            else:
                continue
            if direction == "LONG":
                cond = {**cond, "outcome": "down"}
            elif direction == "SHORT":
                cond = {**cond, "outcome": "up"}
            clauses.append(cond)

        if not clauses:
            return {"sample_size": 0, "matched_predictions": 0, "coverage": None}

        # Grab the last 50 resolved predictions (up/down/flat) and
        # check how many match ANY of the clauses.
        coll = db["features_snapshots"]
        cursor = coll.find(
            {"outcome": {"$in": ["up", "down", "flat"]}},
            {"_id": 0, "ticker": 1, "captured_at": 1, **{c: 1 for clause in clauses for c in clause}, "outcome": 1},
        ).sort("captured_at", -1).limit(50)

        sample_size = 0
        matched = 0
        async for row in cursor:
            sample_size += 1
            for clause in clauses:
                ok = True
                for k, v in clause.items():
                    val = row.get(k)
                    if isinstance(v, dict):
                        # e.g. {"$lt": 0.8, "$ne": None}
                        if val is None and v.get("$ne") is None:
                            ok = False
                            break
                        if "$lt" in v and not (val is not None and val < v["$lt"]):
                            ok = False
                            break
                        if "$gt" in v and not (val is not None and val > v["$gt"]):
                            ok = False
                            break
                    else:
                        if val != v:
                            ok = False
                            break
                if ok:
                    matched += 1
                    break

        coverage = round(matched / sample_size, 3) if sample_size > 0 else None
        return {
            "sample_size": sample_size,
            "matched_predictions": matched,
            "coverage": coverage,
        }
    except Exception as e:
        logger.debug(f"[adaptation] projected_impact failed: {e}")
        return {"sample_size": 0, "matched_predictions": 0, "coverage": None}
