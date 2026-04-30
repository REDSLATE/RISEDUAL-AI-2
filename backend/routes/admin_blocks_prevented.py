"""
Blocks Prevented — investor-facing summary of the IP contract's
defensive value.

Aggregates the proof chain's ``EXECUTION_REJECTED`` blocks over a
configurable window (default: this quarter) into one summary doc:

  * Total trades the IP refused
  * Estimated dollar exposure prevented
  * Breakdown by reason (adversarial / auditor / authority / failure
    / risk)
  * Breakdown by asset class
  * Top symbols where the guard intervened
  * Sparkline of blocks per week

Reads the proof chain only — single source of truth, hash-verifiable
counts. Doesn't touch the shadow log (that's would-block; this is
actually-blocked).

Owner-only. Designed for screenshot/PNG-export to investor decks.
"""
from __future__ import annotations

__domain__ = "PRD"  # Post-Resolution Domain — historic analysis

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/blocks-prevented", tags=["admin", "ip"])

PROOF_COLLECTION = "decision_proof_chain"

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


# Map the rejection reason to the responsible patent — operators
# see the patent label in the breakdown rather than the technical
# string from the proof block.
REASON_LABELS = {
    "adversarial_rejection": "Patent K — Adversarial",
    "auditor_veto": "Auditor — Calibration Veto",
    "invalid_authority": "Authority — Expiry/Countersign",
    "failure_mode_block": "Patent M — Failure Mode",
    "risk_budget_rejection": "Patent I — Risk Budget",
    "execution_failed": "Execution — Broker Failure",
    "invalid_signal_action": "Contract — Invalid Action",
}


def _quarter_start(now: datetime) -> datetime:
    """Return the UTC start of the current calendar quarter."""
    q_month = ((now.month - 1) // 3) * 3 + 1
    return datetime(now.year, q_month, 1, tzinfo=timezone.utc)


@router.get("/summary")
async def blocks_prevented_summary(
    request: Request,
    days: Optional[int] = Query(
        None,
        description="Lookback window in days. If omitted, uses the current calendar quarter.",
    ),
):
    """Investor-facing summary. Single endpoint backs the admin card +
    its PNG export."""
    await _require_owner(request)
    if _db is None:
        return _empty_response("db_unavailable")

    now = datetime.now(timezone.utc)
    if days is None:
        since = _quarter_start(now)
        window_label = (
            f"Q{((now.month - 1) // 3) + 1} {now.year}"
        )
        window_days = (now - since).days
    else:
        days = max(1, min(int(days), 365))
        since = now - timedelta(days=days)
        window_label = f"Last {days} day{'s' if days != 1 else ''}"
        window_days = days

    # Pull every EXECUTION_REJECTED block in window. Cap at 10k for
    # the worst-case bot-loop-with-bug scenario; legitimate
    # quarter-long windows fit well under this in practice.
    rejection_cursor = _db[PROOF_COLLECTION].find(
        {
            "event_type": "EXECUTION_REJECTED",
            "created_at": {"$gte": since},
        },
        {"_id": 0, "entity_id": 1, "created_at": 1, "payload": 1},
    ).limit(10000)

    rows: list[dict] = []
    async for r in rejection_cursor:
        rows.append(r)

    total_blocked = len(rows)
    by_reason: dict[str, int] = {}
    by_asset: dict[str, int] = {}
    by_symbol: dict[str, int] = {}
    weekly: dict[str, int] = {}
    estimated_exposure_prevented = 0.0

    # For exposure estimation, we walk back from the rejected block
    # to pick up the entry-side notional. The same entity_id has a
    # SIGNAL_CREATED or RISK_BUDGET_APPLIED block earlier in the
    # chain whose payload carries the proposed notional — that's the
    # dollar-amount the guard saved the user.
    entity_ids = [r.get("entity_id") for r in rows if r.get("entity_id")]
    notional_lookup: dict[str, float] = {}
    if entity_ids:
        # One bulk read; group by entity_id.
        notional_cursor = _db[PROOF_COLLECTION].find(
            {
                "entity_id": {"$in": list(set(entity_ids))},
                "event_type": {"$in": ["SIGNAL_CREATED", "RISK_BUDGET_APPLIED"]},
            },
            {"_id": 0, "entity_id": 1, "event_type": 1, "payload": 1},
        ).limit(20000)
        async for nr in notional_cursor:
            eid = nr.get("entity_id")
            if not eid:
                continue
            payload = nr.get("payload") or {}
            # Prefer RISK_BUDGET_APPLIED's final_notional (post-cap),
            # fall back to SIGNAL_CREATED's notional, then 0.
            v = (
                payload.get("final_notional")
                or payload.get("notional")
                or payload.get("base_notional")
                or 0.0
            )
            try:
                v = float(v)
            except (ValueError, TypeError):
                v = 0.0
            # Keep the largest seen per entity (RISK row supersedes
            # SIGNAL but we don't rely on insert order).
            notional_lookup[eid] = max(notional_lookup.get(eid, 0.0), v)

    for r in rows:
        payload = r.get("payload") or {}
        reason = payload.get("reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1

        # Asset class lives in detail.adversarial / failure / risk
        # depending on which gate fired. Try a few paths.
        detail = payload.get("detail") or {}
        asset = (
            detail.get("asset_class")
            or detail.get("asset_type")
            or _guess_asset_from_entity(r.get("entity_id"))
        )
        by_asset[asset] = by_asset.get(asset, 0) + 1

        # Symbol — same: detail-dependent. Fall back to entity_id parse.
        symbol = (
            detail.get("symbol")
            or _parse_symbol_from_entity(r.get("entity_id"))
        )
        if symbol:
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1

        # Weekly bucket
        ca = r.get("created_at")
        if isinstance(ca, datetime):
            wk = (ca - timedelta(days=ca.weekday())).date().isoformat()
            weekly[wk] = weekly.get(wk, 0) + 1

        # Exposure
        eid = r.get("entity_id")
        if eid and eid in notional_lookup:
            estimated_exposure_prevented += notional_lookup[eid]

    by_reason_list = [
        {
            "reason": k,
            "label": REASON_LABELS.get(k, k.replace("_", " ").title()),
            "count": v,
        }
        for k, v in sorted(by_reason.items(), key=lambda x: -x[1])
    ]
    by_asset_list = [
        {"asset_class": k, "count": v}
        for k, v in sorted(by_asset.items(), key=lambda x: -x[1])
    ]
    top_symbols = sorted(
        ({"symbol": k, "count": v} for k, v in by_symbol.items()),
        key=lambda x: -x["count"],
    )[:10]
    weekly_series = sorted(
        ({"week_start": k, "count": v} for k, v in weekly.items()),
        key=lambda x: x["week_start"],
    )

    return {
        "window": {
            "label": window_label,
            "days": window_days,
            "since": since.isoformat(),
            "until": now.isoformat(),
        },
        "total_blocked": total_blocked,
        "estimated_exposure_prevented_usd": round(
            estimated_exposure_prevented, 2,
        ),
        "by_reason": by_reason_list,
        "by_asset_class": by_asset_list,
        "top_symbols": top_symbols,
        "weekly_series": weekly_series,
        "generated_at": now.isoformat(),
    }


def _guess_asset_from_entity(entity_id: Optional[str]) -> str:
    if not entity_id:
        return "unknown"
    head = entity_id.split(":", 1)[0].lower()
    if head in ("crypto", "equity", "options"):
        return head
    return "unknown"


def _parse_symbol_from_entity(entity_id: Optional[str]) -> Optional[str]:
    """Entity ids are conventionally ``<asset>:<...>:<SYMBOL>:<ts>`` for
    manual orders or ``crypto:<SYMBOL>:<ts>`` for the bot. Walk the
    parts and find the first uppercase token of length 1-6."""
    if not entity_id:
        return None
    for part in entity_id.split(":"):
        if part.isupper() and 1 <= len(part) <= 6:
            return part
    return None


def _empty_response(reason: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "window": {
            "label": "—",
            "days": 0,
            "since": now.isoformat(),
            "until": now.isoformat(),
        },
        "total_blocked": 0,
        "estimated_exposure_prevented_usd": 0.0,
        "by_reason": [],
        "by_asset_class": [],
        "top_symbols": [],
        "weekly_series": [],
        "generated_at": now.isoformat(),
        "note": reason,
    }
