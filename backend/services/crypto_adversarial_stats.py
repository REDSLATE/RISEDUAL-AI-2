"""Stats reducer + Mongo glue for the adversarial decision log.

What this answers
-----------------
Once the adversarial layer wakes up (Tier 3 unlocked + env flag on),
``crypto_adversarial_decision_log`` starts filling. This module gives
operators a real-time read on whether the layer is *learning*:

* **bull_win_rate** — % of decisions where Bull's preferred direction
  matched the realised move.
* **bear_win_rate** — same for Bear.
* **commander_no_trade_avg_r_avoided** — when Commander said NO_TRADE
  in shadow phase (trade fires anyway), what was the avg r_multiple
  that the Commander wanted to skip? Negative number = Commander was
  correctly avoiding losing trades. Positive = Commander would have
  cost us money in veto phase.
* **edge_gap distribution** — sanity check on the resolver. If
  edge_gap is bimodal around the threshold, the threshold itself is
  the wrong knob; if it's diffuse, weights need tuning.

Maturity guardrail (mirrors crypto_shadow_research_stats.py)
------------------------------------------------------------
``actionable=False`` until every decision-type bucket has at least
``MIN_BUCKET_SAMPLES`` (15) closed decisions. Below that, the win
rates are statistical noise and acting on them would overfit.

Phase semantics
---------------
The endpoint reports decisions across ALL phases that have outcomes.
In shadow phase (default), every decision has an outcome because the
trade fires regardless. In veto/full phases, NO_TRADE decisions
won't have outcomes (no fill → no r_multiple → no patch). Operators
who want to compare the same metric across phase changes should
filter by ``phase`` at the route layer.

Pure-function design
--------------------
:func:`compute_adversarial_stats` is deliberately pure so it can be
unit-tested without a Mongo handle. The route layer just feeds it
the rows.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Minimum closed-decision count per decision_type bucket before
# `actionable` flips True. Same value as the shadow-research stats
# endpoint for consistency — operators read both under the same
# discipline.
MIN_BUCKET_SAMPLES = 15

DECISION_COLLECTION = "crypto_adversarial_decision_log"

_DECISION_TYPES = ("LONG", "SHORT_OR_AVOID", "NO_TRADE")


def _safe_float(value: Any) -> Optional[float]:
    """Return float(value) or None if not numeric. Used because
    final_result_r can be None (decision logged but not yet closed)
    or numeric. Pandas-free implementation — keeps the reducer
    dependency-free."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None  # True/False would otherwise pass isinstance(int)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _r_summary(rs: list[float]) -> dict[str, Any]:
    """Pure stats reducer for one bucket of r_multiples.

    Returns ``count``, ``avg_r``, ``median_r``, ``win_rate`` (where
    win_rate counts r > 0 strictly — break-even is not a win).
    All numbers rounded to 4 dp for diff-friendly payloads.
    """
    n = len(rs)
    if n == 0:
        return {"count": 0, "avg_r": None, "median_r": None, "win_rate": None}
    avg = sum(rs) / n
    s = sorted(rs)
    median = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    wins = sum(1 for r in rs if r > 0)
    return {
        "count": n,
        "avg_r": round(avg, 4),
        "median_r": round(median, 4),
        "win_rate": round(wins / n, 4),
    }


def _interpret(
    actionable: bool,
    bull_win_rate: Optional[float],
    bear_win_rate: Optional[float],
) -> str:
    """One-line operator-readable verdict.

    Thresholds match the discipline in crypto_shadow_research_stats:
    a ≥ 0.10 advantage flags promotion-ready; ≤ -0.10 flags harmful.
    Tune later when N is larger.
    """
    if not actionable:
        return "insufficient_data_keep_observing"
    if bull_win_rate is None or bear_win_rate is None:
        return "no_data"
    spread = bull_win_rate - bear_win_rate
    if spread >= 0.10:
        return "bull_dominates_check_for_long_bias_overfit"
    if spread <= -0.10:
        return "bear_dominates_strong_signal_to_promote_to_risk_only"
    return "balanced_keep_observing_or_tune_threshold"


def compute_adversarial_stats(rows: list[dict]) -> dict[str, Any]:
    """Pure reducer. Takes a list of `crypto_adversarial_decision_log`
    rows and produces the operator payload.

    Skips rows without `final_result_r` (still-open trades) — they
    don't have an outcome yet. Counted separately as ``open_count``.
    """
    by_decision: dict[str, list[float]] = {dt: [] for dt in _DECISION_TYPES}
    bull_wins = 0
    bull_total = 0
    bear_wins = 0
    bear_total = 0
    no_trade_avoided_rs: list[float] = []
    edge_gaps: list[float] = []
    open_count = 0
    skipped = 0

    for row in rows:
        final_r = _safe_float(row.get("final_result_r"))
        if final_r is None:
            open_count += 1
            continue

        decision = (row.get("decision") or "").upper()
        if decision not in by_decision:
            skipped += 1
            continue
        by_decision[decision].append(final_r)

        eg = _safe_float(row.get("edge_gap"))
        if eg is not None:
            edge_gaps.append(eg)

        # Winner attribution — already computed at close time and
        # stored on the row. Ignore neutral rows (veto-phase NO_TRADEs
        # that never got an r) — they shouldn't be in this loop
        # anyway because they fail the final_r filter above.
        winner = (row.get("winner") or "").lower()
        if winner == "bull":
            bull_wins += 1
            bull_total += 1
        elif winner == "bear":
            bear_wins += 1
            bear_total += 1
        # `winner=neutral` rows (if any slip through despite having
        # final_r) just don't contribute to either rate — same
        # treatment as the shadow-research-stats neutral bucket.

        # NO_TRADE decisions in shadow phase: the trade fired anyway
        # and we measured final_r. Track the realised r so operators
        # can answer "what would we have lost if Commander had
        # vetoed?". A negative average means Commander would have
        # correctly avoided losing trades.
        if decision == "NO_TRADE":
            no_trade_avoided_rs.append(final_r)

    by_decision_summary = {dt: _r_summary(by_decision[dt]) for dt in _DECISION_TYPES}

    bull_win_rate = round(bull_wins / bull_total, 4) if bull_total else None
    bear_win_rate = round(bear_wins / bear_total, 4) if bear_total else None

    no_trade_avoided = (
        {
            "count": len(no_trade_avoided_rs),
            "avg_r_avoided": round(
                sum(no_trade_avoided_rs) / len(no_trade_avoided_rs), 4,
            ),
        }
        if no_trade_avoided_rs
        else {"count": 0, "avg_r_avoided": None}
    )

    edge_gap_summary = (
        {
            "count": len(edge_gaps),
            "mean": round(sum(edge_gaps) / len(edge_gaps), 4),
            "min": round(min(edge_gaps), 4),
            "max": round(max(edge_gaps), 4),
        }
        if edge_gaps
        else {"count": 0, "mean": None, "min": None, "max": None}
    )

    sample_sizes = [by_decision_summary[dt]["count"] for dt in _DECISION_TYPES]
    min_bucket = min(sample_sizes) if sample_sizes else 0
    actionable = min_bucket >= MIN_BUCKET_SAMPLES
    interpretation = _interpret(actionable, bull_win_rate, bear_win_rate)

    return {
        "total_with_outcome": sum(sample_sizes),
        "open_count": open_count,
        "skipped_invalid": skipped,
        "by_decision": by_decision_summary,
        "bull_win_rate": bull_win_rate,
        "bear_win_rate": bear_win_rate,
        "bull_total": bull_total,
        "bear_total": bear_total,
        "no_trade_avoided": no_trade_avoided,
        "edge_gap": edge_gap_summary,
        "min_bucket_count": min_bucket,
        "min_bucket_samples_required": MIN_BUCKET_SAMPLES,
        "actionable": actionable,
        "interpretation": interpretation,
    }


async def fetch_adversarial_stats(
    db: Any,
    *,
    hours: Optional[int] = None,
    phase: Optional[str] = None,
) -> dict[str, Any]:
    """Mongo glue. Pulls every closed adversarial decision (optionally
    within a rolling ``hours`` window or filtered by ``phase``) and
    runs the pure reducer.

    Returns the empty-state payload when DB is missing or query fails
    — the admin tile is non-critical.
    """
    if db is None:
        return compute_adversarial_stats([])

    query: dict[str, Any] = {}
    # We DO want still-open decisions in the count for `open_count`.
    # The reducer separates them out via final_result_r=None.
    if hours is not None and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query["timestamp"] = {"$gte": cutoff}
    if phase:
        query["phase"] = phase

    try:
        rows = await db[DECISION_COLLECTION].find(
            query,
            {
                "_id": 0,
                "decision": 1,
                "phase": 1,
                "regime": 1,
                "edge_gap": 1,
                "final_result_r": 1,
                "winner": 1,
                "timestamp": 1,
            },
        ).to_list(length=100_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-stats] query failed: %s", exc)
        return compute_adversarial_stats([])

    summary = compute_adversarial_stats(rows)
    if hours is not None:
        summary["window_hours"] = hours
    if phase:
        summary["filter_phase"] = phase
    return summary


# ── Paginated raw-decisions feed ──────────────────────────────────────────────


# Hard cap on page size — protects against accidental "limit=100000"
# DOSing the API while still allowing operators to grab a meaningful
# slice for spreadsheet analysis.
DECISIONS_PAGE_LIMIT_MAX = 200
DECISIONS_PAGE_LIMIT_DEFAULT = 50


async def fetch_recent_decisions(
    db: Any,
    *,
    limit: int = DECISIONS_PAGE_LIMIT_DEFAULT,
    symbol: Optional[str] = None,
    phase: Optional[str] = None,
    decision: Optional[str] = None,
) -> dict[str, Any]:
    """Paginated raw feed of adversarial decisions, newest first.

    Filters are AND-combined. ``symbol`` / ``decision`` are case-folded
    to match how the writer stores them. Returns a structured payload
    with ``items`` + the applied filter echo so the operator UI can
    show "Showing 50 LONG decisions on BTC in shadow phase" without
    re-deriving the filter state.
    """
    if db is None:
        return {"items": [], "count": 0, "limit": 0, "filters": {}}

    safe_limit = max(1, min(int(limit or DECISIONS_PAGE_LIMIT_DEFAULT),
                            DECISIONS_PAGE_LIMIT_MAX))

    query: dict[str, Any] = {}
    if symbol:
        query["symbol"] = symbol.upper()
    if phase:
        query["phase"] = phase
    if decision:
        query["decision"] = decision.upper()

    try:
        rows = await db[DECISION_COLLECTION].find(
            query,
            # Drop _id (ObjectId not JSON-serializable). Everything
            # else on the row is already JSON-safe by construction
            # in adversarial_logger.
            {"_id": 0},
        ).sort("timestamp", -1).limit(safe_limit).to_list(length=safe_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-decisions] query failed: %s", exc)
        return {"items": [], "count": 0, "limit": safe_limit,
                "filters": {}, "error": str(exc)[:120]}

    # Mongo datetime → ISO string for JSON. Idempotent — non-datetime
    # values are passed through.
    for row in rows:
        ts = row.get("timestamp")
        if hasattr(ts, "isoformat"):
            row["timestamp"] = ts.isoformat()
        closed = row.get("closed_at")
        if hasattr(closed, "isoformat"):
            row["closed_at"] = closed.isoformat()

    return {
        "items": rows,
        "count": len(rows),
        "limit": safe_limit,
        "filters": {k: v for k, v in
                    (("symbol", symbol), ("phase", phase), ("decision", decision))
                    if v},
    }


# ── Trade ↔ Decision join view ────────────────────────────────────────────────


async def fetch_trades_with_decisions(
    db: Any,
    *,
    limit: int = DECISIONS_PAGE_LIMIT_DEFAULT,
    symbol: Optional[str] = None,
    only_closed: bool = False,
) -> dict[str, Any]:
    """Joined view: every trade that carries an ``adversarial_decision_id``
    paired with the Bull/Bear/Commander reasoning that produced it.

    Lets operators answer the "did Commander earn its keep?" question
    in one query — entry signal, Commander's case, and realised P&L
    side by side. Critical post-Tier-3 review tool.

    Implemented as a Mongo ``$lookup`` aggregation; falls back to an
    empty payload on any failure (admin tile is non-critical).
    """
    if db is None:
        return {"items": [], "count": 0, "limit": 0, "filters": {}}

    safe_limit = max(1, min(int(limit or DECISIONS_PAGE_LIMIT_DEFAULT),
                            DECISIONS_PAGE_LIMIT_MAX))

    match: dict[str, Any] = {"adversarial_decision_id": {"$ne": None}}
    if symbol:
        match["symbol"] = symbol.upper()
    if only_closed:
        match["status"] = "closed"

    pipeline = [
        {"$match": match},
        {"$sort": {"created_at": -1}},
        {"$limit": safe_limit},
        {"$lookup": {
            "from": DECISION_COLLECTION,
            "localField": "adversarial_decision_id",
            "foreignField": "decision_id",
            "as": "_decision_doc",
        }},
        {"$addFields": {
            "decision": {"$arrayElemAt": ["$_decision_doc", 0]},
        }},
        {"$project": {
            "_id": 0,
            "_decision_doc": 0,
            # Same projection the dashboard would want — keeps payload
            # tight enough for an admin table without paginating into
            # 100s of KB.
            "trade_id": 1,
            "symbol": 1,
            "direction": 1,
            "size_usd": 1,
            "confidence": 1,
            "entry_price": 1,
            "exit_price": 1,
            "stop_loss": 1,
            "take_profit": 1,
            "status": 1,
            "created_at": 1,
            "closed_at": 1,
            "exit_reason": 1,
            "pnl_usd": 1,
            "r_multiple": 1,
            "regime": 1,
            "adversarial_decision_id": 1,
            "adversarial_action": 1,
            "decision": 1,
        }},
    ]

    try:
        rows = await db["crypto_paper_trades"].aggregate(pipeline).to_list(
            length=safe_limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-trades] aggregation failed: %s", exc)
        return {"items": [], "count": 0, "limit": safe_limit,
                "filters": {}, "error": str(exc)[:120]}

    # Stringify any datetimes inside the row + the nested decision doc
    # so the whole payload is JSON-safe in one pass.
    for row in rows:
        for key in ("created_at", "closed_at"):
            v = row.get(key)
            if hasattr(v, "isoformat"):
                row[key] = v.isoformat()
        nested = row.get("decision") or {}
        if isinstance(nested, dict):
            for key in ("timestamp", "closed_at"):
                v = nested.get(key)
                if hasattr(v, "isoformat"):
                    nested[key] = v.isoformat()
            # Strip nested _id if Mongo sneaks it through (we already
            # exclude it in the projection but defence in depth).
            nested.pop("_id", None)

    return {
        "items": rows,
        "count": len(rows),
        "limit": safe_limit,
        "filters": {k: v for k, v in
                    (("symbol", symbol), ("only_closed", only_closed))
                    if v},
    }
