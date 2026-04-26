"""Mid-flight stats for the crypto Shadow-Mode Web Research lane.

Why this exists
---------------
We're collecting Tavily + LLM verdicts on every high-conviction crypto
fill (see :mod:`services.web_research_service`). Each closed trade now
carries both an ``r_multiple`` (realised move ÷ pre-defined risk) and a
``web_research_shadow_verdict.agreement`` label
(``agree`` / ``disagree`` / ``neutral``).

This module computes the **mid-flight expectancy split**: does the LLM's
narrative stance correlate with realised R-multiple? The answer
ultimately decides whether shadow research becomes:

* **A) ignored** (lift ≈ 0 → noise, drop the spend),
* **B) a confidence multiplier** (lift > 0 by a meaningful margin), or
* **C) a hard veto** (only if disagreement reliably predicts negative R).

Maturity guardrail
------------------
``actionable=False`` until every bucket has ≥ ``MIN_BUCKET_SAMPLES``
closed trades. Below that threshold, the lift number is statistical
noise and acting on it would overfit a handful of decisions.

Pure-function design
--------------------
:func:`compute_shadow_research_stats` is deliberately pure so it can be
unit-tested without a Mongo handle or a router. The route layer in
:mod:`routes.crypto_trading` just feeds it the rows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Minimum closed-trade count per bucket before ``lift`` is treated as
# actionable. Below this, sample noise dominates the signal.
MIN_BUCKET_SAMPLES = 15

# Trade collection — single source of truth for closed crypto fills.
TRADES_COLLECTION = "crypto_paper_trades"

_AGREEMENT_BUCKETS = ("agree", "disagree", "neutral")


def _r_stats(rows: list[dict]) -> dict[str, Any]:
    """Pure stats reducer for one bucket. ``count``, ``avg_r``,
    ``median_r``, ``win_rate``. All R-values rounded to 4 dp so the
    payload diff-friendly across reruns."""
    rs = [
        float(r.get("r_multiple"))
        for r in rows
        if isinstance(r.get("r_multiple"), (int, float))
    ]
    n = len(rs)
    if n == 0:
        return {
            "count": 0,
            "avg_r": None,
            "median_r": None,
            "win_rate": None,
        }
    avg = sum(rs) / n
    sorted_rs = sorted(rs)
    if n % 2 == 1:
        median = sorted_rs[n // 2]
    else:
        median = (sorted_rs[n // 2 - 1] + sorted_rs[n // 2]) / 2.0
    wins = sum(1 for r in rs if r > 0)
    return {
        "count": n,
        "avg_r": round(avg, 4),
        "median_r": round(median, 4),
        "win_rate": round(wins / n, 4),
    }


def compute_shadow_research_stats(rows: list[dict]) -> dict[str, Any]:
    """Compute the full bucketed payload from a list of closed
    ``crypto_paper_trades`` rows that carry a non-null
    ``web_research_shadow_verdict``.

    Returns the structure documented in the changelog entry — same
    shape regardless of sample size, with ``actionable=False`` until
    all buckets cross :data:`MIN_BUCKET_SAMPLES`.
    """
    bucketed: dict[str, list[dict]] = {b: [] for b in _AGREEMENT_BUCKETS}
    skipped = 0

    for r in rows:
        verdict = r.get("web_research_shadow_verdict") or {}
        if not isinstance(verdict, dict):
            skipped += 1
            continue
        agreement = (verdict.get("agreement") or "").lower()
        if agreement in bucketed:
            bucketed[agreement].append(r)
        else:
            skipped += 1

    buckets_payload = {b: _r_stats(bucketed[b]) for b in _AGREEMENT_BUCKETS}

    agree_avg = buckets_payload["agree"]["avg_r"]
    disagree_avg = buckets_payload["disagree"]["avg_r"]

    if agree_avg is None or disagree_avg is None:
        lift: Optional[float] = None
    else:
        lift = round(agree_avg - disagree_avg, 4)

    sample_sizes = [buckets_payload[b]["count"] for b in _AGREEMENT_BUCKETS]
    min_bucket = min(sample_sizes) if sample_sizes else 0
    actionable = (lift is not None) and (min_bucket >= MIN_BUCKET_SAMPLES)

    interpretation = _interpret(lift, actionable)

    return {
        "total": sum(sample_sizes),
        "skipped_invalid_verdict": skipped,
        "buckets": buckets_payload,
        "lift": lift,
        "min_bucket_samples_required": MIN_BUCKET_SAMPLES,
        "min_bucket_count": min_bucket,
        "actionable": actionable,
        "interpretation": interpretation,
    }


def _interpret(lift: Optional[float], actionable: bool) -> str:
    """Return a one-line operator-readable verdict.

    The thresholds (``>= 0.10`` = useful, ``<= -0.10`` = harmful) are
    deliberately conservative — picked so a noisy 50-trade window
    can't trip a "promote to live" signal. Tune later when N is
    larger.
    """
    if not actionable:
        return "insufficient_data_keep_observing"
    if lift is None:
        return "no_data"
    if lift >= 0.10:
        return "research_useful_consider_promoting"
    if lift <= -0.10:
        return "research_harmful_keep_shadow_only"
    return "research_neutral_drop_or_observe_more"


async def fetch_shadow_research_stats(
    db: Any,
    *,
    hours: Optional[int] = None,
) -> dict[str, Any]:
    """Mongo glue around :func:`compute_shadow_research_stats`.

    Pulls every closed crypto trade (optionally within a rolling
    ``hours`` window) that carries a shadow verdict, then runs the
    pure stats reducer.
    """
    if db is None:
        return compute_shadow_research_stats([])

    query: dict[str, Any] = {
        "status": "closed",
        "web_research_shadow_verdict": {"$ne": None},
    }
    if hours is not None and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query["closed_at"] = {"$gte": cutoff}

    try:
        rows = await db[TRADES_COLLECTION].find(
            query,
            {
                "_id": 0,
                "r_multiple": 1,
                "web_research_shadow_verdict": 1,
                "closed_at": 1,
                "symbol": 1,
            },
        ).to_list(length=100_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow_stats] query failed: %s", exc)
        return compute_shadow_research_stats([])

    summary = compute_shadow_research_stats(rows)
    if hours is not None:
        summary["window_hours"] = hours
    return summary
