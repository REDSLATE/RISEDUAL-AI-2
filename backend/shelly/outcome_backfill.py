"""Phase 4 — Shelly outcome backfill.

When a paper trade closes, this module stamps the realised
``outcome.pnl_pct`` / ``outcome.outcome_label`` onto every Shelly
memory that contributed to that decision cluster — across all 5
LocalShellys (Alpha / Camaro / Chevelle / RedEye / MC) AND the MC
shared-memory collection.

Without this loop, both ``LocalShelly.reason()`` and
``MCShelly.reason_across_shellys()`` permanently report
"Not enough shared Shelly memory yet" — they only count memories
with ``outcome.pnl_pct`` present, and nothing else writes that
field.

Match strategy
--------------
* **symbol** — canonical-uppercased.
* **direction** — canonical LONG / SHORT / HOLD via
  ``services.prediction_tracker.canonical_ai_dir``.
* **time window** — memories with ``created_at`` within
  ``OUTCOME_BACKFILL_WINDOW_SECONDS`` (default 600s) of the
  trade's ``opened_at``. The window binds outcomes to a single
  decision cluster (hypothesis fan-out + paper_trade emission
  all land within a few seconds of each other).
* **only-unresolved** — memories already carrying
  ``outcome.pnl_pct`` are left alone. The backfill is strictly
  idempotent.

Authority discipline
--------------------
Every write touches only the ``outcome`` sub-document. The
top-level ``authority: memory_reasoning_only`` stamp is never
modified. The reasoning receipts (separate collection) are
untouched — only the source memories. The next reasoning call
will pick up the new outcomes naturally.

Fail-soft
---------
Per-node try/except. A failure on one collection NEVER blocks
the calling close path; the function is called inside the
paper_trade_closer success branch.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from shelly.config import NODE_NAMES

logger = logging.getLogger(__name__)


OUTCOME_BACKFILL_WINDOW_SECONDS = int(
    os.environ.get("SHELLY_OUTCOME_BACKFILL_WINDOW_SECONDS", "600") or 600,
)


def _canon_direction(raw: Any) -> str:
    """Boundary-normalise to LONG / SHORT / HOLD."""
    try:
        from services.prediction_tracker import canonical_ai_dir
        canon = canonical_ai_dir(str(raw or ""))
    except Exception:  # noqa: BLE001
        canon = ""
    # canonical_ai_dir returns LONG / SHORT / UNKNOWN. We treat
    # UNKNOWN (and any unexpected value) as HOLD so the match
    # still works for observation closes — the brain decided not
    # to trade but Shelly still remembered the decision.
    if canon and canon != "UNKNOWN":
        return canon
    return "HOLD"


def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort ISO/datetime → tz-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


async def backfill_outcome_for_trade(
    db: Any,
    *,
    ticker: str,
    direction: str,
    trade_id: Optional[str] = None,
    opened_at: Any = None,
    closed_at: Any = None,
    pnl_pct: float,
    outcome_label: str,
    window_seconds: Optional[int] = None,
) -> dict[str, Any]:
    """Stamp outcome onto matching unresolved Shelly memories.

    Returns a per-node summary ``{node: count_updated, ...,
    "mc_shared": int, "total": int}``. Fail-soft per node — a
    Mongo error on one collection logs DEBUG and contributes 0
    to that node's count.
    """
    symbol = (ticker or "").strip().upper()
    canon_dir = _canon_direction(direction)
    window = int(window_seconds or OUTCOME_BACKFILL_WINDOW_SECONDS)

    # Anchor — prefer opened_at (when we know the decision time);
    # fall back to closed_at minus a generous slack for legacy rows
    # missing opened_at. If neither parses, skip the time filter
    # entirely so the most-recent unresolved memory still gets
    # stamped (better to over-match than to never resolve).
    anchor = _parse_dt(opened_at) or _parse_dt(closed_at)
    time_filter: dict[str, Any] = {}
    if anchor is not None:
        lo = (anchor - timedelta(seconds=window)).isoformat()
        hi = (anchor + timedelta(seconds=window)).isoformat()
        time_filter = {"created_at": {"$gte": lo, "$lte": hi}}

    outcome_set = {
        "outcome.pnl_pct": float(pnl_pct),
        "outcome.outcome_label": str(outcome_label or "unknown"),
        "outcome.backfilled_at": datetime.now(timezone.utc).isoformat(),
    }
    if trade_id:
        outcome_set["outcome.trade_id"] = str(trade_id)
    if closed_at is not None:
        outcome_set["outcome.closed_at"] = (
            closed_at.isoformat() if isinstance(closed_at, datetime)
            else str(closed_at)
        )

    base_filter: dict[str, Any] = {
        "symbol": symbol,
        "direction": canon_dir,
        # Idempotency — only stamp memories whose outcome.pnl_pct
        # has not been written yet.
        "$or": [
            {"outcome": None},
            {"outcome.pnl_pct": {"$exists": False}},
        ],
        **time_filter,
    }

    summary: dict[str, Any] = {"total": 0}

    # Per-node LocalShelly collections.
    for node in NODE_NAMES:
        coll_name = f"shelly_{node.lower()}_memories"
        try:
            res = await db[coll_name].update_many(
                base_filter, {"$set": outcome_set},
            )
            count = int(getattr(res, "modified_count", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[shelly_backfill] node=%s update failed: %s", node, exc,
            )
            count = 0
        summary[node] = count
        summary["total"] += count

    # MC shared aggregator — also backfill so the cross-brain
    # reasoning receipts pick up the resolved outcome immediately.
    try:
        res = await db["shelly_mc_shared_memory"].update_many(
            base_filter, {"$set": outcome_set},
        )
        mc_count = int(getattr(res, "modified_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shelly_backfill] mc_shared update failed: %s", exc)
        mc_count = 0
    summary["mc_shared"] = mc_count
    summary["total"] += mc_count

    if summary["total"]:
        logger.info(
            "[shelly_backfill] %s %s pnl_pct=%.4f outcome=%s touched=%d",
            symbol, canon_dir, float(pnl_pct), outcome_label, summary["total"],
        )
    return summary


__all__ = [
    "backfill_outcome_for_trade",
    "OUTCOME_BACKFILL_WINDOW_SECONDS",
]
