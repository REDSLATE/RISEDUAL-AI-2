"""
Tier-3 Slippage Advisor — read-only segmentation + env-tweak
proposals based on round-trip slippage data.

Why this exists
───────────────
The slippage attribution panel shows AGGREGATE drag. The advisor
goes one level deeper: which SEGMENT is bleeding the most? E.g.
"bid_fill SHORTs in pre-market" or "AAPL round-trips on Mondays".
When a segment's drag exceeds the system-wide baseline by a
meaningful margin AND has a statistically meaningful sample, the
advisor drafts a proposal — never auto-applies — and writes it to
``tier3_advisor_proposals`` for operator review.

Design constraints
──────────────────
* **Deterministic math** — segmentation, outlier detection, and
  proposal text are all rule-based. No LLM calls per run. Means
  the operator can audit the math via the same code path the UI
  reads. (LLM polish on the rationale is a future ticket.)
* **Read-only** — proposals are advisory. ``status`` field
  (``pending`` / ``accepted`` / ``dismissed``) tracks operator
  review; *applying* the proposed env change is still a manual
  edit + restart. This protects against blind self-modification
  loops.
* **Idempotent** — keyed by ``(segment_key, lookback_days,
  generated_week_iso)`` so re-running daily / on demand doesn't
  spam the collection.
* **Sample-size gate** — proposals only fire when the segment has
  at least ``MIN_SEGMENT_SAMPLE_SIZE`` (default 20) trades. Bigger
  the segment, more authority to act.

Pipeline
────────
1. ``analyze_slippage_segments(db, lookback_days=30)`` — pure
   query-only segmentation. Returns ``{segments: [...], baseline:
   {...}}`` where each segment carries count, dollar_cost,
   bps_total, drag_pct_of_pnl + a stable ``segment_key``.
2. ``detect_outliers(segments, baseline)`` — flags segments
   whose drag exceeds (baseline drag × OUTLIER_MULTIPLIER) AND
   meets the sample-size floor.
3. ``draft_proposals(outliers)`` — template-based env-tweak
   suggestions. Each outlier maps to a known proposal kind:
   * ``method_drag`` → "consider widening entry confidence floor"
   * ``symbol_drag`` → "consider abandoning the symbol"
   * ``session_drag`` → "consider raising MIN_LIQUIDITY_BPS for
     this session"
4. ``run_advisor_cycle(db)`` — orchestrator. Idempotent write
   path with dedup.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

logger = logging.getLogger(__name__)

PROPOSAL_COLLECTION: str = "tier3_advisor_proposals"

# Sample-size floor — a segment with fewer than this many trades is
# noise and the advisor SHOULD NOT propose env tweaks based on it.
MIN_SEGMENT_SAMPLE_SIZE: int = int(
    os.environ.get("TIER3_ADVISOR_MIN_SAMPLE_SIZE", "20"),
)
# A segment must drag at least this many bps over the baseline
# average to clear the outlier gate. Conservative — we want
# meaningful signal, not noise.
OUTLIER_MULTIPLIER: float = float(
    os.environ.get("TIER3_ADVISOR_OUTLIER_MULTIPLIER", "1.75"),
)
# Hard floor — segments below this avg-bps never produce proposals
# regardless of multiplier (1bps × 2 is still 2bps; not actionable).
ABSOLUTE_BPS_FLOOR: float = float(
    os.environ.get("TIER3_ADVISOR_ABSOLUTE_BPS_FLOOR", "8.0"),
)


# ── Segmentation ──────────────────────────────────────────────────


def _us_session_label(opened_at: datetime | None) -> str:
    """Map an entry timestamp to a coarse session label for
    equity attribution. Matches the ``_market_session_label``
    semantics in routes/admin.py."""
    if opened_at is None:
        return "unknown"
    if opened_at.weekday() >= 5:
        return "weekend"
    minutes = opened_at.hour * 60 + opened_at.minute
    if 13 * 60 + 30 <= minutes < 20 * 60:
        return "rth"
    if 8 * 60 <= minutes < 13 * 60 + 30:
        return "pre"
    if 20 * 60 <= minutes < 24 * 60:
        return "post"
    return "closed"


def _segment_keys(trade: dict[str, Any]) -> dict[str, str]:
    """One-row → per-axis segment keys. Used for grouping."""
    lane = "crypto" if "position_size_usd" in trade else "equity"
    method_in = trade.get("slippage_method") or "unknown"
    method_out = trade.get("exit_slippage_method") or "unknown"
    symbol = trade.get("symbol") or trade.get("ticker") or "UNKNOWN"
    direction = (trade.get("direction") or "").upper() or "UNKNOWN"
    opened_at = trade.get("opened_at")
    if isinstance(opened_at, str):
        try:
            opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        except ValueError:
            opened_at = None
    session = _us_session_label(opened_at) if lane == "equity" else "n/a"
    return {
        "lane": lane,
        "method_in": method_in,
        "method_out": method_out,
        "symbol": symbol,
        "direction": direction,
        "session": session,
    }


def _segment_dollar_cost(trade: dict[str, Any]) -> tuple[float, float]:
    """Round-trip drag (bps + $). Reads the autopsy slippage block
    when present, else recomputes from the trade row."""
    autopsy_slip = (trade.get("autopsy") or {}).get("slippage")
    if autopsy_slip:
        return (
            float(autopsy_slip.get("total_bps") or 0.0),
            float(autopsy_slip.get("total_dollar_cost") or 0.0),
        )
    # Fallback — best-effort sum.
    entry_bps = abs(float(trade.get("slippage_bps") or 0.0))
    exit_bps_v = trade.get("exit_slippage_bps")
    exit_bps = abs(float(exit_bps_v)) if exit_bps_v is not None else 0.0
    total_bps = entry_bps + exit_bps
    notional = (
        float(trade.get("position_size_usd") or 0.0)
        or (
            float(trade.get("shares") or 0.0)
            * float(trade.get("entry_price") or 0.0)
        )
    )
    dollar = notional * (total_bps / 10_000.0)
    return total_bps, dollar


def _stable_segment_key(axis: str, value: str) -> str:
    """Hashed segment ID — lets the dedup layer handle both
    standard axes (``method_in:ask_fill``) and combinations."""
    raw = f"{axis}:{value}"
    return hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


async def analyze_slippage_segments(
    db: Any, *, lookback_days: int = 30,
) -> dict[str, Any]:
    """Walk the closed-trade collections, group by axis, return a
    flat list of ``segment`` dicts plus a baseline summary.

    Pure read-only; never raises. Returns a dict with shape::

        {
            "lookback_days": 30,
            "trades_scanned": 412,
            "trades_with_slippage": 31,
            "baseline": {"avg_bps": 9.4, "total_dollar_cost": 6.21,
                         "total_pnl": -1180.09},
            "segments": [
                {"axis": "method_in", "value": "ask_fill",
                 "segment_key": "...", "count": 18,
                 "avg_bps": 12.1, "total_dollar_cost": 4.33,
                 "drag_pct_of_pnl": 0.37},
                ...
            ],
        }
    """
    if db is None:
        return {
            "lookback_days": lookback_days, "trades_scanned": 0,
            "trades_with_slippage": 0, "baseline": {}, "segments": [],
        }

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    axes = ("lane", "method_in", "method_out", "symbol",
            "direction", "session")
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    total_bps_sum = 0.0
    total_dollar = 0.0
    total_pnl = 0.0
    n_with_slip = 0
    n_total = 0

    for coll in ("paper_trades", "crypto_paper_trades"):
        try:
            cursor = db[coll].find({
                "status": "closed",
                "closed_at": {"$gte": cutoff},
            }, {"_id": 0})
            async for r in cursor:
                n_total += 1
                pnl = float(r.get("pnl_usd", r.get("pnl", 0.0)) or 0.0)
                total_pnl += pnl
                # Only count rows with at least an entry slippage
                # stamp — anything without it has no meaningful
                # attribution.
                if r.get("slippage_method") is None:
                    continue
                n_with_slip += 1
                bps, dollar = _segment_dollar_cost(r)
                total_bps_sum += bps
                total_dollar += dollar
                keys = _segment_keys(r)
                for axis in axes:
                    val = keys.get(axis) or "unknown"
                    bucket = grouped.setdefault(
                        (axis, val),
                        {"count": 0.0, "bps_sum": 0.0,
                         "dollar_cost": 0.0, "pnl": 0.0},
                    )
                    bucket["count"] += 1
                    bucket["bps_sum"] += bps
                    bucket["dollar_cost"] += dollar
                    bucket["pnl"] += pnl
        except Exception as exc:  # noqa: BLE001
            logger.warning("[tier3-advisor] read failed for %s: %s", coll, exc)

    baseline = {
        "avg_bps": (
            round(total_bps_sum / n_with_slip, 2)
            if n_with_slip > 0 else 0.0
        ),
        "total_dollar_cost": round(total_dollar, 2),
        "total_pnl": round(total_pnl, 2),
        "trades_with_slippage": n_with_slip,
    }

    segments: list[dict[str, Any]] = []
    for (axis, val), v in grouped.items():
        n = max(int(v["count"]), 1)
        avg_bps = v["bps_sum"] / n
        pnl_abs = abs(v["pnl"]) or 1.0
        segments.append({
            "axis": axis,
            "value": val,
            "segment_key": _stable_segment_key(axis, val),
            "count": int(v["count"]),
            "avg_bps": round(avg_bps, 2),
            "total_dollar_cost": round(v["dollar_cost"], 2),
            "drag_pct_of_pnl": round(
                (v["dollar_cost"] / pnl_abs) * 100.0, 2,
            ),
        })

    # Stable order: highest dollar_cost first.
    segments.sort(
        key=lambda s: (s["total_dollar_cost"], s["count"]), reverse=True,
    )

    return {
        "lookback_days": lookback_days,
        "trades_scanned": n_total,
        "trades_with_slippage": n_with_slip,
        "baseline": baseline,
        "segments": segments,
    }


# ── Outlier detection + proposal drafting ─────────────────────────


def detect_outliers(
    segments: Iterable[dict[str, Any]], baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter segments that meaningfully exceed baseline drag.

    Gates:
    * ``count`` ≥ ``MIN_SEGMENT_SAMPLE_SIZE``
    * ``avg_bps`` > ``baseline.avg_bps × OUTLIER_MULTIPLIER``
    * ``avg_bps`` > ``ABSOLUTE_BPS_FLOOR`` (hard floor)
    """
    base_bps = float(baseline.get("avg_bps") or 0.0)
    threshold = max(
        base_bps * OUTLIER_MULTIPLIER,
        ABSOLUTE_BPS_FLOOR,
    )
    outliers: list[dict[str, Any]] = []
    for s in segments:
        if s["count"] < MIN_SEGMENT_SAMPLE_SIZE:
            continue
        if s["avg_bps"] <= threshold:
            continue
        outliers.append({
            **s,
            "baseline_avg_bps": round(base_bps, 2),
            "outlier_threshold_bps": round(threshold, 2),
            "ratio": round(
                s["avg_bps"] / base_bps if base_bps > 0 else 0.0, 2,
            ),
        })
    return outliers


# Map ``axis`` → proposal kind + env-knob template.
# Severity is one of ``info`` / ``warn`` / ``critical`` based on
# how far the segment exceeds the baseline.
_PROPOSAL_TEMPLATES: dict[str, dict[str, str]] = {
    "method_in": {
        "kind": "method_drag",
        "env_knob": "ENTRY_METHOD_GUARDRAIL",
        "guidance": (
            "Entry slippage on '{value}' fills is averaging "
            "{avg_bps}bps ({ratio}× baseline). Consider widening the "
            "confidence floor or adding a spread guard before submitting "
            "{value} orders."
        ),
    },
    "method_out": {
        "kind": "method_drag_exit",
        "env_knob": "EXIT_METHOD_GUARDRAIL",
        "guidance": (
            "Exit slippage on '{value}' closes is averaging "
            "{avg_bps}bps ({ratio}× baseline). Consider switching to "
            "limit orders for closes when spread > 25bps."
        ),
    },
    "symbol": {
        "kind": "symbol_drag",
        "env_knob": "TICKER_ABANDONMENT_LIST",
        "guidance": (
            "Round-trip drag on {value} is averaging {avg_bps}bps "
            "({ratio}× baseline) over {count} trades. Strong candidate "
            "for the abandonment list."
        ),
    },
    "session": {
        "kind": "session_drag",
        "env_knob": "MIN_LIQUIDITY_BPS_BY_SESSION",
        "guidance": (
            "{value} session drag is averaging {avg_bps}bps "
            "({ratio}× baseline). Consider raising MIN_LIQUIDITY_BPS "
            "for {value} or pausing entries during this session."
        ),
    },
    "direction": {
        "kind": "direction_drag",
        "env_knob": "DIRECTION_BIAS_GUARDRAIL",
        "guidance": (
            "{value} directional drag is averaging {avg_bps}bps "
            "({ratio}× baseline). Auditor should re-weight against "
            "{value} signals."
        ),
    },
    "lane": {
        "kind": "lane_drag",
        "env_knob": "LANE_GUARDRAIL",
        "guidance": (
            "{value} lane drag is averaging {avg_bps}bps "
            "({ratio}× baseline). Review whether the primary quote "
            "provider is stale during volatile windows."
        ),
    },
}


def _severity_from_ratio(ratio: float) -> str:
    if ratio >= 3.0:
        return "critical"
    if ratio >= 2.0:
        return "warn"
    return "info"


def draft_proposals(
    outliers: list[dict[str, Any]],
    *,
    lookback_days: int,
) -> list[dict[str, Any]]:
    """One proposal per outlier. Severity scales with the
    over-baseline ratio. ``segment_key`` + the iso-week stamp form
    the dedup key the writer uses."""
    proposals: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    iso_week = now.isocalendar()
    week_stamp = f"{iso_week[0]}-W{iso_week[1]:02d}"

    for o in outliers:
        template = _PROPOSAL_TEMPLATES.get(o["axis"])
        if template is None:
            continue
        guidance = template["guidance"].format(**o)
        proposals.append({
            "kind": template["kind"],
            "env_knob": template["env_knob"],
            "axis": o["axis"],
            "value": o["value"],
            "segment_key": o["segment_key"],
            "severity": _severity_from_ratio(float(o["ratio"])),
            "ratio": o["ratio"],
            "metrics": {
                "count": o["count"],
                "avg_bps": o["avg_bps"],
                "baseline_avg_bps": o["baseline_avg_bps"],
                "outlier_threshold_bps": o["outlier_threshold_bps"],
                "total_dollar_cost": o["total_dollar_cost"],
                "drag_pct_of_pnl": o["drag_pct_of_pnl"],
            },
            "rationale": guidance,
            "lookback_days": lookback_days,
            "generated_at": now,
            "generated_week": week_stamp,
            "status": "pending",
        })
    return proposals


# ── Idempotent writer + orchestrator ──────────────────────────────


async def run_advisor_cycle(
    db: Any, *, lookback_days: int = 30,
) -> dict[str, Any]:
    """End-to-end: analyse → detect outliers → draft → upsert.

    Returns a JSON-friendly summary describing what was generated
    + how many were deduped vs newly inserted.
    """
    analysis = await analyze_slippage_segments(
        db, lookback_days=lookback_days,
    )
    outliers = detect_outliers(analysis["segments"], analysis["baseline"])
    proposals = draft_proposals(outliers, lookback_days=lookback_days)

    inserted = 0
    deduped = 0

    if db is not None:
        for p in proposals:
            dedup_key = {
                "segment_key": p["segment_key"],
                "lookback_days": p["lookback_days"],
                "generated_week": p["generated_week"],
            }
            try:
                existing = await db[PROPOSAL_COLLECTION].find_one(
                    dedup_key, {"_id": 1},
                )
                if existing is not None:
                    deduped += 1
                    continue
                await db[PROPOSAL_COLLECTION].insert_one(dict(p))
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[tier3-advisor] write failed: %s", exc)

    return {
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "trades_scanned": analysis["trades_scanned"],
        "trades_with_slippage": analysis["trades_with_slippage"],
        "baseline": analysis["baseline"],
        "outliers_found": len(outliers),
        "proposals_drafted": len(proposals),
        "inserted": inserted,
        "deduped": deduped,
    }


async def list_proposals(
    db: Any, *, status: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    cursor = db[PROPOSAL_COLLECTION].find(
        query, {"_id": 0},
    ).sort("generated_at", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    for r in rows:
        ga = r.get("generated_at")
        if hasattr(ga, "isoformat"):
            r["generated_at"] = ga.isoformat()
    return rows


async def update_proposal_status(
    db: Any, *, segment_key: str, generated_week: str,
    new_status: str, actor: str = "operator",
) -> dict[str, Any] | None:
    """Mark a proposal accepted / dismissed. Read-only WRT env —
    the env change itself is still a manual edit + restart."""
    if db is None:
        return None
    if new_status not in ("accepted", "dismissed", "pending"):
        return None
    res = await db[PROPOSAL_COLLECTION].find_one_and_update(
        {"segment_key": segment_key, "generated_week": generated_week},
        {"$set": {
            "status": new_status,
            "reviewed_at": datetime.now(timezone.utc),
            "reviewed_by": actor,
        }},
        return_document=True,
        projection={"_id": 0},
    )
    if res is None:
        return None
    for k in ("generated_at", "reviewed_at"):
        v = res.get(k)
        if hasattr(v, "isoformat"):
            res[k] = v.isoformat()
    return res
