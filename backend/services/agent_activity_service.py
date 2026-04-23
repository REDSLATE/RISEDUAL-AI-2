"""Agent activity service — unified narrative feed of what the
autonomous pieces of RISEDUAL are doing.

Collection: ``agent_activity`` — capped by caller discipline
(TTL index on ``timestamp`` at collection-bootstrap time keeps it
bounded; for now entries live until manually cleaned).

Schema::

    {
      "event_id": str (uuid),
      "timestamp": datetime (UTC-aware),
      "type": str (enum — see EventType),
      "severity": "info" | "success" | "warn" | "error",
      "title": str (short — headline for the feed row),
      "detail": str | None (longer — shown on hover / expand),
      "symbol": str | None (ticker if relevant),
      "metadata": dict | None (any structured extras — consumed by
                                future admin dashboards, safe to
                                extend without schema migration),
    }

Call sites should use the ``log_*`` convenience helpers rather than
writing raw dicts — that keeps the event-type vocabulary consistent
so the frontend can branch on it (icon selection, coloring, etc.).

The service is never-raise: activity logging must not cascade into
real trading flows. A Mongo hiccup just drops the row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_COLLECTION = "agent_activity"

# Controlled event vocabulary — keeps the frontend branching tight.
# Add sparingly; prefer richer `metadata` over a proliferation of
# event types.
EVENT_TYPES = {
    # Prediction lifecycle
    "prediction_logged": "🧠",   # strategist opinion captured
    "prediction_resolved": "✅",  # outcome labelled (win/loss)
    # Paper-trading autonomous agent
    "paper_scan_start": "🔍",
    "paper_trade_open": "📈",
    "paper_trade_skip": "⏭️",
    "paper_trade_close": "📉",
    # ML / retrain
    "retrain_start": "🔄",
    "retrain_complete": "🎯",
    "retrain_gated": "🛑",
    # Kill switch / safety
    "kill_switch_armed": "🚨",
    "kill_switch_cleared": "🟢",
    # Research / market scans
    "research": "📰",
    # Generic
    "info": "ℹ️",
}

Severity = Literal["info", "success", "warn", "error"]

_db: Any = None


def set_db(db: Any) -> None:
    """Wire the Motor DB handle from the route registry."""
    global _db
    _db = db


async def log_event(
    *,
    type: str,
    title: str,
    detail: Optional[str] = None,
    severity: Severity = "info",
    symbol: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Append an event to the agent activity feed.

    Returns the event_id on success, None on any failure. Never
    raises — callers must be able to fire-and-forget.
    """
    if _db is None:
        return None
    if type not in EVENT_TYPES:
        logger.warning("[agent_activity] unknown event type=%r, coercing to 'info'", type)
        type = "info"

    event_id = str(uuid4())
    doc: dict[str, Any] = {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc),
        "type": type,
        "severity": severity,
        "title": title.strip()[:200],
        "detail": (detail.strip()[:500] if detail else None),
        "symbol": symbol,
        "metadata": metadata,
    }
    try:
        await _db[_COLLECTION].insert_one(doc)
    except Exception as e:
        logger.warning("[agent_activity] insert failed: %s", e)
        return None
    return event_id


# ── Convenience helpers — preferred call style for each event type ──


async def log_paper_trade_opened(ticker: str, direction: str,
                                 position_usd: float, confidence: float,
                                 patterns: list[str], regime: str,
                                 why: Optional[list[dict]] = None) -> None:
    """Paper-trading agent just opened a new position. This is a
    user-delighting event — the feed row should feel like a coworker
    saying 'I just did something'.

    ``why`` is an optional list of top-feature contributions from
    :func:`ai_core.explainability.extract_top_features`; when
    present the UI renders a "Why?" block under the event detail.
    """
    patterns_str = ", ".join(patterns) if patterns else "no-pattern / high-conf"
    await log_event(
        type="paper_trade_open",
        severity="success",
        title=f"Opened paper position · {ticker} {direction.upper()}",
        detail=(f"${position_usd:,.0f} · conviction {confidence * 100:.0f}% · "
                f"{patterns_str} · regime: {regime}"),
        symbol=ticker,
        metadata={
            "position_usd": round(position_usd, 2),
            "confidence": round(confidence, 4),
            "patterns": patterns,
            "regime": regime,
            "direction": direction,
            "why": why or [],
        },
    )


async def log_paper_trade_skipped(ticker: str, reason: str,
                                  confidence: Optional[float] = None,
                                  why: Optional[list[dict]] = None) -> None:
    """Agent considered and passed on a setup — transparency event.
    These are frequent and should default to info severity (quieter
    than trade opens).

    ``why`` carries the same top-feature contributions as the open
    event; rendered identically in the feed. For a skip, the
    contributions tell users *which features outweighed the bullish
    case* — the most teachable moments.
    """
    detail = reason
    if confidence is not None:
        detail = f"{reason} · directional conviction {confidence * 100:.0f}%"
    await log_event(
        type="paper_trade_skip",
        severity="info",
        title=f"Skipped {ticker} · {reason[:60]}",
        detail=detail,
        symbol=ticker,
        metadata={"reason": reason, "confidence": confidence, "why": why or []},
    )


async def log_prediction_resolved(symbol: str, direction: str,
                                  outcome: str, pnl: float,
                                  r_multiple: float) -> None:
    """Prediction hit its resolution horizon → win or loss. This is
    a high-information event — the agent's track record updates
    here."""
    arrow = "✓" if outcome == "win" else "✗"
    severity: Severity = "success" if outcome == "win" else "warn"
    await log_event(
        type="prediction_resolved",
        severity=severity,
        title=f"{arrow} {symbol} {direction.upper()} · {outcome.upper()} · {r_multiple:+.2f}R",
        detail=f"P&L ${pnl:+,.2f}",
        symbol=symbol,
        metadata={
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "r_multiple": round(r_multiple, 3),
            "direction": direction,
        },
    )


async def log_retrain_complete(samples: int, mean_weight: float,
                               r_eligible_frac: float) -> None:
    """ML retrain finished. Show adoption metrics so the user sees
    the learning loop is actually ingesting new data."""
    await log_event(
        type="retrain_complete",
        severity="success",
        title=f"Model retrained · {samples:,} samples",
        detail=(f"mean sample weight: {mean_weight:.2f} · "
                f"R-weighted rows: {r_eligible_frac * 100:.0f}%"),
        metadata={
            "samples": samples,
            "mean_weight": round(mean_weight, 4),
            "r_eligible_frac": round(r_eligible_frac, 4),
        },
    )


async def log_retrain_gated(reason: str) -> None:
    """Retrain attempted but quality gate refused. This is a safety
    event — worth surfacing so the user knows the model *didn't*
    silently retrain on noise."""
    await log_event(
        type="retrain_gated",
        severity="warn",
        title=f"Retrain skipped · quality gate failed",
        detail=reason,
        metadata={"reason": reason},
    )


async def log_kill_switch_armed(reason: str) -> None:
    """Safety system tripped — bright red in the feed."""
    await log_event(
        type="kill_switch_armed",
        severity="error",
        title="Kill switch armed · all trading halted",
        detail=reason,
        metadata={"reason": reason},
    )


async def log_kill_switch_cleared(by_user: str) -> None:
    """Manual re-enable — trading resumes."""
    await log_event(
        type="kill_switch_cleared",
        severity="success",
        title="Kill switch cleared · trading resumed",
        detail=f"Cleared by {by_user}",
        metadata={"user": by_user},
    )


# ── Read path ──


async def fetch_feature_stability(days: int = 7, min_appearances: int = 3,
                                  top_k: int = 15) -> list[dict]:
    """Aggregate feature-importance drift metrics from the recent
    agent activity stream.

    Walks every event in the window whose metadata contains a
    ``why`` array (the per-feature contributions attached by
    :func:`ai_core.explainability.extract_top_features`) and rolls
    them up per feature::

        {
          "feature": "momentum_14",
          "appearances": 47,          # # events where this was top-3
          "avg_impact": -0.043,        # signed drift signal
          "avg_abs_impact": 0.312,    # magnitude / raw importance
          "bullish_frac": 0.38,        # fraction bullish
          "last_seen": "2026-02-22T14:30:00+00:00",
          "first_seen": "2026-02-16T09:15:00+00:00",
        }

    Returns rows sorted by ``appearances`` desc, filtered to those
    meeting ``min_appearances`` (keeps noise from brand-new features
    out of the dashboard). Top ``top_k`` rows by appearance count.

    Never-raise: returns empty list on failure. This is a reporting
    surface, must not 500 the Agent tab.
    """
    if _db is None:
        return []

    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    try:
        cursor = _db[_COLLECTION].find(
            {"timestamp": {"$gte": since},
             "metadata.why": {"$type": "array", "$ne": []}},
            {"_id": 0, "timestamp": 1, "metadata.why": 1},
        )
        rows = await cursor.to_list(length=5000)
    except Exception as e:
        logger.warning("[agent_activity] feature stability fetch failed: %s", e)
        return []

    # Per-feature accumulators. Using plain dicts rather than pandas
    # to keep the import graph lean on this hot path.
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts = row.get("timestamp")
        why_list = (row.get("metadata") or {}).get("why") or []
        for w in why_list:
            name = w.get("feature")
            if not name:
                continue
            try:
                impact = float(w.get("impact", 0.0))
                abs_impact = float(w.get("abs_impact", abs(impact)))
            except (TypeError, ValueError):
                continue
            bucket = acc.setdefault(name, {
                "appearances": 0, "sum_impact": 0.0,
                "sum_abs_impact": 0.0, "bullish_count": 0,
                "first_seen": ts, "last_seen": ts,
            })
            bucket["appearances"] += 1
            bucket["sum_impact"] += impact
            bucket["sum_abs_impact"] += abs_impact
            if impact >= 0:
                bucket["bullish_count"] += 1
            # Timestamps flow in newest-first; track min/max by
            # comparing as ISO strings (works because ISO 8601 is
            # lex-comparable when UTC-normalised).
            if ts:
                if not bucket["first_seen"] or ts < bucket["first_seen"]:
                    bucket["first_seen"] = ts
                if not bucket["last_seen"] or ts > bucket["last_seen"]:
                    bucket["last_seen"] = ts

    out: list[dict] = []
    for name, b in acc.items():
        if b["appearances"] < max(1, int(min_appearances)):
            continue
        n = b["appearances"]
        first = b["first_seen"]
        last = b["last_seen"]
        if isinstance(first, datetime):
            first = first.isoformat()
        if isinstance(last, datetime):
            last = last.isoformat()
        out.append({
            "feature": name,
            "appearances": n,
            "avg_impact": round(b["sum_impact"] / n, 4),
            "avg_abs_impact": round(b["sum_abs_impact"] / n, 4),
            "bullish_frac": round(b["bullish_count"] / n, 4),
            "first_seen": first,
            "last_seen": last,
        })
    out.sort(key=lambda r: r["appearances"], reverse=True)
    return out[: max(1, int(top_k))]


async def fetch_recent(limit: int = 50, since: Optional[datetime] = None,
                       symbol: Optional[str] = None) -> list[dict]:
    """Return the most recent events, newest first.

    Args:
        limit: max rows
        since: if set, return only events with ``timestamp > since``
            (polling clients pass their last-seen timestamp here)
        symbol: optional ticker filter for per-symbol drill-down
    """
    if _db is None:
        return []

    query: dict[str, Any] = {}
    if since is not None:
        query["timestamp"] = {"$gt": since}
    if symbol:
        query["symbol"] = symbol.upper()

    try:
        cursor = (
            _db[_COLLECTION]
            .find(query, {"_id": 0})
            .sort("timestamp", -1)
            .limit(max(1, min(int(limit), 500)))
        )
        rows = await cursor.to_list(length=limit)
    except Exception as e:
        logger.warning("[agent_activity] fetch failed: %s", e)
        return []

    # ISO-format timestamps for clean JSON — clients expect strings,
    # not bson datetime. Icons resolved server-side so the frontend
    # doesn't have to maintain the vocabulary separately.
    for r in rows:
        ts = r.get("timestamp")
        if isinstance(ts, datetime):
            r["timestamp"] = ts.isoformat()
        r["icon"] = EVENT_TYPES.get(r.get("type", "info"), "ℹ️")
    return rows
