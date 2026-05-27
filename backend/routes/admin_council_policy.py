"""Council Policy — admin-controlled confidence floor (2026-02-23, P1).

Lets the operator raise the brain's confidence floor at runtime
WITHOUT a redeploy. Useful when the council's calibration is
drifting and we want a tighter conviction bar fast, or when MC
flags rising drawdown across multiple brains and the operator
wants symmetric tightening.

Endpoints
---------
* ``GET /api/admin/council-policy`` — read current policy snapshot.
* ``POST /api/admin/council-policy`` — set / clear an override.

Doctrine — safety floor
-----------------------
Operators can RAISE the bar but NEVER drop it below ``HARD_FLOOR``
(default ``0.55``, the same value the legacy static
``_MIN_PAPER_CONFIDENCE`` enforces). This means an admin mistake
can only make Alpha MORE conservative — never less. Mirrors the
"admin can only raise the safety bar" doctrine the operator
explicitly mandated when scoping this work (2026-02-23).

Persistence
-----------
Writes to the existing ``confidence_gate_overrides`` collection
that the dynamic threshold engine already reads at runtime — no
new schema, no new pipe to wire. The override row uses
``_id="current"`` so it's a singleton; expiry is enforced
server-side via the ``expires_at`` field already in the schema.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin-council-policy"])

db: Any = None  # set by route_registry.wire_db


def set_db(database) -> None:  # noqa: D401
    """Inject the Mongo handle. Called once at startup by route_registry."""
    global db
    db = database


# Hard safety floor — operator can never set the base below this.
# Same value as the legacy static ``_MIN_PAPER_CONFIDENCE`` in
# ml_paper_trader so this is a no-op compared to today's behaviour
# when no override is set, but it CANNOT be bypassed via this admin
# surface. Env-tunable for testing; production should leave it at
# the doctrine default.
def _hard_floor() -> float:
    try:
        return float(os.environ.get("COUNCIL_POLICY_HARD_FLOOR", "0.55"))
    except (TypeError, ValueError):
        return 0.55


def _hard_cap() -> float:
    """Upper bound — same as the dynamic gate's MAX_THRESHOLD."""
    try:
        return float(os.environ.get("COUNCIL_POLICY_HARD_CAP", "0.90"))
    except (TypeError, ValueError):
        return 0.90


async def _require_owner(request: Request):
    """Owner-only — mirrors the pattern used in admin_conviction.py."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── GET /api/admin/council-policy ────────────────────────────────


@router.get("/council-policy")
async def get_council_policy(request: Request) -> dict[str, Any]:
    """Snapshot of the current confidence-gate policy.

    Surfaces:
      * Hard floor / cap (immutable doctrine bounds — informational).
      * Static base & cap from env (what the dynamic threshold uses
        when no override is active).
      * Current override row (if any).
      * Dynamic threshold computation for equity + crypto cores
        (so the operator sees the live effect of the policy,
        including drawdown / loss-streak / calibration adjustments).
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from services.confidence_gate import (
        _current_base_min_confidence, _current_max_threshold,
        get_dynamic_confidence_threshold,
    )

    snapshot: dict[str, Any] = {
        "hard_floor": _hard_floor(),
        "hard_cap": _hard_cap(),
        "static_base": _current_base_min_confidence(),
        "static_cap": _current_max_threshold(),
        "override": None,
        "live": {},
    }

    try:
        ov = await db["confidence_gate_overrides"].find_one(
            {"_id": "current"},
            {"_id": 0, "min_rr": 1, "min_confidence": 1,
             "set_by": 1, "set_at": 1, "expires_at": 1, "note": 1},
        )
        if ov:
            snapshot["override"] = ov
    except Exception as exc:  # noqa: BLE001
        logger.debug("[council_policy] override read failed: %s", exc)

    # Live dynamic threshold — gives operator the actual bar each
    # core is enforcing right now.
    for asset in ("equity", "crypto"):
        try:
            t = await get_dynamic_confidence_threshold(db, asset_type=asset)
            snapshot["live"][asset] = {
                "base": t.base,
                "delta": t.delta,
                "threshold": t.threshold,
                "drawdown": t.drawdown,
                "loss_streak": t.loss_streak,
                "calibration_gap": t.calibration_gap,
                "reasons": t.reasons,
            }
        except Exception as exc:  # noqa: BLE001
            snapshot["live"][asset] = {"error": str(exc)}

    return snapshot


# ── POST /api/admin/council-policy ───────────────────────────────


@router.post("/council-policy")
async def set_council_policy(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Set or clear a council confidence-floor override.

    Body schema::

        {
          "min_confidence": 0.65,    # optional — new floor in [hard_floor, hard_cap]
          "min_rr":         2.0,     # optional — passed through to existing RR gate
          "ttl_hours":      24,      # default 24h; max 168 (7 days)
          "note":           "...",   # free-form audit note
          "clear":          false    # set true to delete the override
        }

    Returns the resulting policy snapshot. Doctrine: the new
    ``min_confidence`` MUST sit inside ``[hard_floor, hard_cap]``.
    If a value outside is submitted, the response is 422 with a
    structured error naming the field — same shape MC uses for
    its 422-on-empty contributions so operator tooling parses it
    uniformly.
    """
    user = await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Clear path — short-circuit before validation.
    if bool(body.get("clear")):
        await db["confidence_gate_overrides"].delete_one({"_id": "current"})
        return await get_council_policy(request)

    floor = _hard_floor()
    cap = _hard_cap()
    min_conf = body.get("min_confidence")
    min_rr = body.get("min_rr")

    if min_conf is None and min_rr is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_policy_update",
                "fields": ["min_confidence", "min_rr"],
                "hint": "Provide at least one of min_confidence or min_rr, "
                        "or set clear=true to remove the active override.",
            },
        )

    if min_conf is not None:
        try:
            min_conf = float(min_conf)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_min_confidence",
                        "received": body.get("min_confidence")},
            )
        if min_conf < floor:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "below_hard_floor",
                    "hard_floor": floor,
                    "received": min_conf,
                    "doctrine": (
                        "Operator may RAISE the confidence bar but never "
                        "drop it below the hard safety floor."
                    ),
                },
            )
        if min_conf > cap:
            raise HTTPException(
                status_code=422,
                detail={"error": "above_hard_cap",
                        "hard_cap": cap, "received": min_conf},
            )

    if min_rr is not None:
        try:
            min_rr = float(min_rr)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_min_rr", "received": body.get("min_rr")},
            )
        if min_rr < 0.0:
            raise HTTPException(
                status_code=422,
                detail={"error": "negative_min_rr", "received": min_rr},
            )

    # TTL: default 24h; clamp to [1, 168] hours.
    try:
        ttl_hours = int(body.get("ttl_hours") or 24)
    except (TypeError, ValueError):
        ttl_hours = 24
    ttl_hours = max(1, min(168, ttl_hours))
    now = datetime.now(timezone.utc)

    doc: dict[str, Any] = {
        "_id": "current",
        "set_by": user.get("email") or user.get("id") or "owner",
        "set_at": now,
        "expires_at": now + timedelta(hours=ttl_hours),
        "note": (str(body.get("note") or "")).strip()[:500] or None,
    }
    if min_conf is not None:
        doc["min_confidence"] = min_conf
    if min_rr is not None:
        doc["min_rr"] = min_rr

    await db["confidence_gate_overrides"].update_one(
        {"_id": "current"}, {"$set": doc}, upsert=True,
    )
    logger.info(
        "[council_policy] override set by=%s min_conf=%s min_rr=%s ttl=%dh",
        doc["set_by"], min_conf, min_rr, ttl_hours,
    )
    return await get_council_policy(request)


__all__ = ["router", "set_db"]
