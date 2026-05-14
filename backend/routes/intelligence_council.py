"""Council snapshot endpoint — the inbound side of the sidecar, exposed
as a single JSON read for the War Room UI.

Replaces the legacy Strategist/Auditor pair with the live 4-brain
council (alpha/camaro/chevelle/redeye) as reported by Mission Control.

GET /api/intelligence/council
    Returns the most recent roles-manifest snapshot + a per-peer slice
    of the most recent opinions, balanced so no single chatty peer
    (looking at you, Chevelle) crowds out the rest.

GET /api/intelligence/council/{symbol}
    Same as above but filtered to opinions whose ``topic`` matches the
    symbol (``symbol:NVDA``).

This route is intentionally cheap — it reads MC via the existing
``risedual_monorepo_client`` reader helpers and applies the
read-side balancer from ``mc_inbox_poller``. No DB writes happen
here; Shelly's perception of the same opinions runs in the inbox
poller's background task on its own cadence.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services import risedual_monorepo_client as mono
from services.mc_inbox_poller import balanced_recent_opinions, load_state

logger = logging.getLogger("intelligence_council")
router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


def _format_opinion(op: dict[str, Any]) -> dict[str, Any]:
    """Trim an opinion payload to a UI-friendly shape."""
    evidence = op.get("evidence") if isinstance(op.get("evidence"), dict) else {}
    return {
        "id": op.get("id") or op.get("opinion_id"),
        "runtime": op.get("runtime"),
        "topic": op.get("topic"),
        "stance": op.get("stance"),
        "confidence": op.get("confidence"),
        "body": op.get("body"),
        "created_at": op.get("created_at"),
        "evidence_keys": list(evidence.keys()) if evidence else [],
    }


def _format_role(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime": item.get("runtime"),
        "role": item.get("role"),
        "title": item.get("title"),
        "tagline": item.get("tagline"),
        "description": item.get("description"),
        "authority_state": item.get("authority_state"),
        "allowed_actions": item.get("allowed_actions") or [],
        "last_seen": item.get("last_seen"),
        "may_execute": bool(item.get("may_execute", False)),
    }


@router.get("/council")
async def get_council(
    limit: int = Query(20, ge=1, le=100),
    max_per_peer: int = Query(5, ge=1, le=20),
):
    """Return the live 4-brain council snapshot.

    Combines:
      - roles manifest (who's connected + their seat)
      - balanced recent opinions (up to max_per_peer per brain)
      - this brain's scorecard (from inbox poller's cached snapshot)
    """
    try:
        roles = await mono.read_roles_manifest()
    except Exception as e:  # noqa: BLE001
        logger.warning("[council] roles read failed: %s", e)
        roles = {"items": [], "count": 0, "error": str(e)}

    try:
        opins = await mono.read_opinions(limit=max(limit * 4, 50))
    except Exception as e:  # noqa: BLE001
        logger.warning("[council] opinions read failed: %s", e)
        opins = {"items": [], "count": 0, "error": str(e)}

    raw_items = list(opins.get("items") or [])
    balanced = balanced_recent_opinions(
        raw_items, max_per_peer=max_per_peer, total_cap=limit,
    )

    # Cached scorecard from the inbox poller's state file (best-effort).
    scorecard = None
    try:
        state = load_state()
        scorecard = state.get("scorecard_snapshot") or None
    except Exception:  # noqa: BLE001
        scorecard = None

    return {
        "doctrine": roles.get("doctrine"),
        "roles": [_format_role(r) for r in (roles.get("items") or [])],
        "opinions": [_format_opinion(o) for o in balanced],
        "opinion_count_total": opins.get("count", len(raw_items)),
        "opinion_count_returned": len(balanced),
        "scorecard": scorecard,
        "errors": {
            "roles": roles.get("error"),
            "opinions": opins.get("error"),
        },
    }


@router.get("/council/{symbol}")
async def get_council_for_symbol(
    symbol: str,
    limit: int = Query(20, ge=1, le=100),
    max_per_peer: int = Query(5, ge=1, le=20),
):
    """Per-symbol slice of the council snapshot. Filters opinions by topic."""
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(400, detail="symbol required")

    try:
        roles = await mono.read_roles_manifest()
    except Exception as e:  # noqa: BLE001
        roles = {"items": [], "count": 0, "error": str(e)}

    # MC's ``symbol`` filter on /opinions takes the canonical short
    # symbol — pass it through; on failure, fall back to client-side
    # topic-prefix filtering.
    try:
        opins = await mono.read_opinions(symbol=sym, limit=max(limit * 4, 50))
    except Exception as e:  # noqa: BLE001
        opins = {"items": [], "count": 0, "error": str(e)}

    raw_items = list(opins.get("items") or [])
    # Defensive client-side topic filter in case MC ignored the param.
    raw_items = [
        o for o in raw_items
        if (o.get("topic") or "").upper().endswith(f":{sym}")
        or sym in (o.get("topic") or "").upper()
        or sym == (o.get("symbol") or "").upper()
    ] or raw_items

    balanced = balanced_recent_opinions(
        raw_items, max_per_peer=max_per_peer, total_cap=limit,
    )

    return {
        "symbol": sym,
        "doctrine": roles.get("doctrine"),
        "roles": [_format_role(r) for r in (roles.get("items") or [])],
        "opinions": [_format_opinion(o) for o in balanced],
        "opinion_count_returned": len(balanced),
        "errors": {
            "roles": roles.get("error"),
            "opinions": opins.get("error"),
        },
    }


def set_db(_db) -> None:  # for symmetry with other routes
    """No-op; this route reads from MC, not Mongo."""
    return
