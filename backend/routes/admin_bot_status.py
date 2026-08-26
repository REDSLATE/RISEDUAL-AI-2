"""Admin Bot Status — one endpoint that answers "why isn't Alpha trading?".

Returns everything an operator needs on one page:
* Regime (slow + fast) — the primary reason Alpha stands down.
* Universe — how many symbols Alpha is actually watching + freshness.
* Recent scans — last 5 with their terminal_result + chosen symbol.
* Skip reasons — bucketed counts for the last 24h.
* Last fill — most recent live equity trade.
* Atlas health — enabled flag + inflight task count.
* Verdict — one plain-English line summarising the state.

This is a read-only aggregation; it never mutates. Backs the
"Bot Status" admin card in ``AlphaDayTraderPanel``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin-bot-status"])

db = None


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    try:
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return str(dt)


def _minutes_since(dt: Any) -> int | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - dt).total_seconds() // 60)


@router.get("/bot-status")
async def bot_status(request: Request):
    """One-shot diagnostic. See module docstring."""
    await _require_admin(request)
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)

    # ── Regime (both layers) ────────────────────────────────────────
    slow_regime: dict[str, Any] = {}
    try:
        row = await db.alpha_regime_state.find_one({"_id": "current"})
        if row:
            slow_regime = {
                "label": row.get("label"),
                "probability": row.get("probability"),
                "updated_at": _iso(row.get("updated_at") or row.get("computed_at")),
            }
    except Exception as exc:  # noqa: BLE001
        slow_regime = {"error": str(exc)}

    fast_regime: dict[str, Any] = {}
    try:
        row = await db.alpha_fast_regime_state.find_one({"_id": "current"})
        if row:
            fast_regime = {
                "label": row.get("label"),
                "updated_at": _iso(row.get("updated_at") or row.get("computed_at")),
            }
    except Exception as exc:  # noqa: BLE001
        fast_regime = {"error": str(exc)}

    # ── Universe ────────────────────────────────────────────────────
    universe: dict[str, Any] = {}
    try:
        count = await db.top_universe.count_documents({})
        sample = await db.top_universe.find(
            {}, {"symbol": 1, "tier": 1, "_id": 0}
        ).limit(10).to_list(10)
        latest = await db.top_universe.find_one(
            {}, {"updated_at": 1, "_id": 0}, sort=[("updated_at", -1)]
        )
        universe = {
            "count": count,
            "sample": sample,
            "last_updated_at": _iso((latest or {}).get("updated_at")),
            "minutes_since_update": _minutes_since((latest or {}).get("updated_at")),
        }
    except Exception as exc:  # noqa: BLE001
        universe = {"error": str(exc)}

    # ── Recent scans ────────────────────────────────────────────────
    recent_scans: list[dict[str, Any]] = []
    try:
        cursor = db.day_trade_scan_log.find({}).sort("started_at", -1).limit(5)
        async for row in cursor:
            chosen = row.get("chosen") or {}
            recent_scans.append({
                "scan_id": row.get("scan_id"),
                "asset_class": row.get("asset_class"),
                "started_at": _iso(row.get("started_at")),
                "total_scanned": row.get("total_scanned"),
                "blocked_count": row.get("blocked_count"),
                "chosen_symbol": chosen.get("symbol") if isinstance(chosen, dict) else None,
            })
    except Exception as exc:  # noqa: BLE001
        recent_scans = [{"error": str(exc)}]

    # ── Skip reasons (24h) ──────────────────────────────────────────
    # Alpha's executor skip log is ``intent_skip_log`` with a ``ts`` field.
    skip_reasons: list[dict[str, Any]] = []
    try:
        pipeline = [
            {"$match": {"ts": {"$gte": day_ago}}},
            {"$group": {"_id": "$reason", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        async for row in db.intent_skip_log.aggregate(pipeline):
            skip_reasons.append({"reason": row["_id"], "count": row["count"]})
    except Exception as exc:  # noqa: BLE001
        skip_reasons = [{"error": str(exc)}]

    # ── Last live fill ──────────────────────────────────────────────
    # Row uses ``opened_at`` (open row) or ``closed_at`` (close row); we
    # sort by ``opened_at`` since that's on every fill. ``kind`` derives
    # from ``intent_kind``/``side``.
    last_fill: dict[str, Any] = {}
    try:
        row = await db.equity_live_trades.find_one(sort=[("opened_at", -1)])
        if row:
            ts = row.get("opened_at") or row.get("closed_at")
            last_fill = {
                "symbol": row.get("symbol"),
                "kind": row.get("intent_kind") or row.get("side"),
                "notional": row.get("notional") or row.get("live_notional_usd"),
                "ts": _iso(ts),
                "minutes_ago": _minutes_since(ts),
            }
    except Exception as exc:  # noqa: BLE001
        last_fill = {"error": str(exc)}

    # ── Atlas health ────────────────────────────────────────────────
    atlas: dict[str, Any] = {"enabled": False, "inflight": 0}
    try:
        from services import atlas_bridge

        atlas = {
            "enabled": atlas_bridge.atlas_enabled() and atlas_bridge.get_ledger() is not None,
            "inflight": atlas_bridge.inflight_count(),
        }
    except Exception as exc:  # noqa: BLE001
        atlas = {"error": str(exc)}

    # ── Verdict ─────────────────────────────────────────────────────
    verdict = _build_verdict(
        slow=slow_regime, fast=fast_regime, universe=universe,
        last_fill=last_fill, skip_reasons=skip_reasons,
    )

    return {
        "computed_at": _iso(now),
        "verdict": verdict,
        "regime": {"slow": slow_regime, "fast": fast_regime},
        "universe": universe,
        "recent_scans": recent_scans,
        "skip_reasons_24h": skip_reasons,
        "last_fill": last_fill,
        "atlas": atlas,
    }


def _build_verdict(
    *,
    slow: dict[str, Any],
    fast: dict[str, Any],
    universe: dict[str, Any],
    last_fill: dict[str, Any],
    skip_reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn the raw signals into one plain-English line + severity.

    Severity levels:
      * ``ok``      — bot is trading normally
      * ``holding`` — deliberate stand-down (regime chop)
      * ``warn``    — degraded but not broken (stale universe, gate loop)
      * ``broken``  — no trades AND no obvious deliberate reason
    """
    reason_lines: list[str] = []
    severity = "ok"

    # Universe empty → structural block.
    if isinstance(universe, dict) and universe.get("count") == 0:
        return {
            "severity": "broken",
            "headline": "Universe is empty — Alpha has nothing to scan",
            "details": ["Run POST /api/admin/top-universe/rebuild to seed it"],
        }

    # Regime chop → deliberate stand-down.
    slow_label = (slow or {}).get("label", "") or ""
    fast_label = (fast or {}).get("label", "") or ""
    if "chop" in slow_label.lower() or "chop" in fast_label.lower() or "meanrevert" in slow_label.lower():
        severity = "holding"
        reason_lines.append(
            f"Regime = {slow_label or 'unknown'} (slow) / {fast_label or 'unknown'} (fast)"
        )

    # Last fill recency.
    minutes_ago = last_fill.get("minutes_ago") if isinstance(last_fill, dict) else None
    if minutes_ago is None:
        reason_lines.append("No live fill on record yet")
        if severity == "ok":
            severity = "warn"
    elif minutes_ago > 60 * 24:
        reason_lines.append(f"Last live fill was {minutes_ago // 60}h ago")
    else:
        reason_lines.append(f"Last live fill {minutes_ago}m ago")

    # Top skip reason bucket.
    top_skip = None
    for sr in skip_reasons or []:
        if isinstance(sr, dict) and sr.get("reason"):
            top_skip = sr
            break
    if top_skip:
        reason_lines.append(
            f"Top 24h skip reason: {top_skip['reason']} × {top_skip['count']}"
        )

    if severity == "holding":
        headline = "Standing down by design — market regime is chop"
    elif severity == "warn":
        headline = "Bot healthy but idle — no recent activity to report on"
    elif severity == "broken":
        headline = "Bot appears stuck — see details"
    else:
        headline = "Bot trading normally"

    return {"severity": severity, "headline": headline, "details": reason_lines}
