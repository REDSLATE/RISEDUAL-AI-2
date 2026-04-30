"""Admin route for the Decision Pipeline Guard shadow-mode log.

Surfaces:

  * GET /api/admin/guard-shadow/summary   — counts + would-block rate
  * GET /api/admin/guard-shadow/decisions — paginated raw rows

Both owner-only. Used during the guard rollout (Step 5 of the
implementation plan): operators compare "would have blocked" against
"actually executed" before flipping ``GUARD_SHADOW_MODE`` off.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from routes.auth import get_current_user
from services.guard_policy_store import (
    VALID_FLAGS,
    clear_override,
    get_state,
    set_override,
)
from services.guard_shadow_log import COLLECTION, is_shadow_mode_enabled

router = APIRouter(prefix="/api/admin/guard-shadow", tags=["admin", "guard-shadow"])

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


@router.get("/summary")
async def shadow_summary(request: Request, hours: int = Query(168, ge=1, le=720)):
    """Aggregate over the last ``hours``: total / would-allow / would-block,
    plus per-source breakdown and the top blocking reasons."""
    await _require_owner(request)
    if _db is None:
        return {
            "shadow_mode_enabled": is_shadow_mode_enabled(),
            "window_hours": hours,
            "total": 0,
            "would_allow": 0,
            "would_block": 0,
            "by_source": {},
            "top_block_reasons": [],
        }

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    coll = _db[COLLECTION]
    match = {"created_at": {"$gte": since}}

    total = await coll.count_documents(match)
    allow = await coll.count_documents({**match, "would_allow": True})
    block = await coll.count_documents({**match, "would_allow": False})

    by_source: dict[str, dict[str, int]] = {}
    pipeline_src = [
        {"$match": match},
        {"$group": {
            "_id": {"source": "$source", "would_allow": "$would_allow"},
            "n": {"$sum": 1},
        }},
    ]
    async for row in coll.aggregate(pipeline_src):
        src = row["_id"]["source"] or "unknown"
        bucket = by_source.setdefault(src, {"would_allow": 0, "would_block": 0})
        if row["_id"]["would_allow"]:
            bucket["would_allow"] = row["n"]
        else:
            bucket["would_block"] = row["n"]

    # Top reasons across blocked rows only (those drive enforcement
    # decisions; allow-rows mostly say "ok").
    reason_counts: dict[str, int] = {}
    async for row in coll.find(
        {**match, "would_allow": False}, {"_id": 0, "reasons": 1},
    ).limit(2000):
        for r in (row.get("reasons") or [])[:3]:
            if not r:
                continue
            reason_counts[r] = reason_counts.get(r, 0) + 1
    top_reasons = sorted(
        ({"reason": k, "count": v} for k, v in reason_counts.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "shadow_mode_enabled": is_shadow_mode_enabled(),
        "window_hours": hours,
        "total": total,
        "would_allow": allow,
        "would_block": block,
        "would_block_rate": round(block / total, 4) if total else 0.0,
        "by_source": by_source,
        "top_block_reasons": top_reasons,
    }


@router.get("/decisions")
async def list_decisions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    source: Optional[str] = None,
    only_blocked: bool = False,
    entity_substr: Optional[str] = None,
):
    """Paginated raw shadow-log feed, newest-first. Filters AND-combine."""
    await _require_owner(request)
    if _db is None:
        return {"decisions": [], "total": 0}

    match: dict = {}
    if source:
        match["source"] = source
    if only_blocked:
        match["would_allow"] = False
    if entity_substr:
        match["entity_id"] = {"$regex": entity_substr, "$options": "i"}

    rows: list[dict] = []
    cursor = (
        _db[COLLECTION]
        .find(match, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    async for r in cursor:
        ca = r.get("created_at")
        if isinstance(ca, datetime):
            r["created_at"] = ca.isoformat()
        rows.append(r)

    return {
        "decisions": rows,
        "limit": limit,
        "filters": {
            "source": source,
            "only_blocked": only_blocked,
            "entity_substr": entity_substr,
        },
    }



# ── Per-patent enforcement policy (Promote/Demote/Clear) ──────────────


@router.get("/policy")
async def get_policy(request: Request):
    """Current effective policy + history + auto-promotion suggestions.

    Returns ``effective`` (env-default merged with Mongo overrides as
    the IP contract sees it), ``overrides`` (just the Mongo deltas),
    ``env_defaults`` (read-only baseline so the UI can show what
    reverting to env would do), a recent ``history`` array, and
    ``suggestions`` — automatically computed promote candidates per
    flag (``would_block_rate < 5%`` over a 7-day window).
    """
    await _require_owner(request)
    state = await get_state(_db)

    # Recompute effective from process — same logic as
    # ``EnforcementPolicy.from_env`` but explicit about what's an
    # env default vs an override.
    import os as _os

    def _flag(name: str) -> bool:
        raw = _os.environ.get(name, "")
        if raw == "":
            return True
        return raw.lower() not in ("0", "false", "no", "off")

    env_defaults = {
        "enforce_adversarial": _flag("PATENT_K_ENFORCE"),
        "enforce_auditor": _flag("AUDITOR_ENFORCE"),
        "enforce_authority": _flag("AUTHORITY_ENFORCE"),
        "enforce_failure_mode": _flag("PATENT_M_ENFORCE"),
        "enforce_risk_budget": _flag("PATENT_I_ENFORCE"),
    }
    overrides = state.get("overrides") or {}
    effective = {k: overrides.get(k, env_defaults[k]) for k in env_defaults}

    # ── Auto-promotion suggestions ────────────────────────────────
    # Scan the shadow log for the last 7d. For each flag that's
    # currently in shadow mode (effective=False), compute the
    # would-block rate attributable to that gate. If <5% and we have
    # at least 50 evaluations of it, suggest promotion.
    suggestions = await _compute_promotion_suggestions(
        effective=effective,
    )

    return {
        "effective": effective,
        "overrides": overrides,
        "env_defaults": env_defaults,
        "history": state.get("history") or [],
        "updated_at": state.get("updated_at"),
        "shadow_mode_enabled": is_shadow_mode_enabled(),
        "valid_flags": sorted(VALID_FLAGS),
        "suggestions": suggestions,
    }


# Map each flag name to the rejection reason its enforcement would
# cause. The shadow log records the `reasons` list per decision —
# matching against this map tells us which flag-block-rate to count.
_FLAG_TO_REASON_PREFIX = {
    "enforce_adversarial": "adversarial_rejection",
    "enforce_auditor": "auditor_veto",
    "enforce_authority": "invalid_authority",
    "enforce_failure_mode": "failure_mode_block",
    "enforce_risk_budget": "risk_budget_rejection",
}

PROMOTION_QUIET_THRESHOLD = 0.05   # <5% would-block rate
PROMOTION_MIN_EVALUATIONS = 50     # need decent sample size
PROMOTION_WINDOW_DAYS = 7


async def _compute_promotion_suggestions(
    *, effective: dict[str, bool],
) -> list[dict]:
    """Scan the last 7d of shadow_log; suggest promotion for any
    currently-shadow flag with <5% would-block rate.

    Returns one entry per quiet flag:
        {"flag": "enforce_auditor",
         "would_block_rate": 0.012,
         "n_evaluations": 142,
         "n_would_block": 2,
         "reason_prefix": "auditor_veto",
         "days_quiet": 9,         # estimate: window covers >= this much
         "rationale": "auditor has been quiet for 9 days, ready to enforce?"}

    Empty list when nothing qualifies. Never raises — a missing
    collection just means "no data yet".
    """
    if _db is None:
        return []
    suggestions: list[dict] = []
    since = datetime.now(timezone.utc) - timedelta(days=PROMOTION_WINDOW_DAYS)
    coll = _db[COLLECTION]

    for flag, currently_enforcing in effective.items():
        if currently_enforcing:
            # Already enforcing — no promote needed.
            continue
        reason_prefix = _FLAG_TO_REASON_PREFIX.get(flag)
        if not reason_prefix:
            continue
        try:
            # Total evaluations in the window.
            n_total = await coll.count_documents({
                "created_at": {"$gte": since},
            })
            if n_total < PROMOTION_MIN_EVALUATIONS:
                continue
            # Rows where THIS gate's reason appears in the reasons list.
            # Stored as `reasons: ["auditor_veto", "..."]` so substring
            # match via $regex on the array element.
            n_block = await coll.count_documents({
                "created_at": {"$gte": since},
                "reasons": {"$regex": reason_prefix, "$options": "i"},
            })
            block_rate = n_block / n_total if n_total else 0.0
            if block_rate >= PROMOTION_QUIET_THRESHOLD:
                continue
            # Days quiet — distance from oldest log row in the
            # window. If we have data spanning the full window, that's
            # 7 days. Otherwise it's how much we've actually seen.
            oldest = await coll.find_one(
                {"created_at": {"$gte": since}},
                {"_id": 0, "created_at": 1},
                sort=[("created_at", 1)],
            )
            days_quiet = PROMOTION_WINDOW_DAYS
            if oldest and isinstance(oldest.get("created_at"), datetime):
                # Mongo strips tzinfo on round-trip — re-attach UTC so
                # the subtraction below doesn't raise (caught earlier
                # by the broad `except` and silently dropped the
                # entire suggestion, which is why the auto-promotion
                # logic appeared to never fire).
                oldest_dt = oldest["created_at"]
                if oldest_dt.tzinfo is None:
                    oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - oldest_dt).total_seconds()
                days_quiet = max(1, int(age / 86400))

            human_label = {
                "enforce_adversarial": "adversarial",
                "enforce_auditor": "auditor",
                "enforce_authority": "authority",
                "enforce_failure_mode": "failure-mode",
                "enforce_risk_budget": "risk-budget",
            }.get(flag, flag)
            suggestions.append({
                "flag": flag,
                "would_block_rate": round(block_rate, 4),
                "n_evaluations": n_total,
                "n_would_block": n_block,
                "reason_prefix": reason_prefix,
                "days_quiet": days_quiet,
                "rationale": (
                    f"{human_label} has been quiet for {days_quiet} day"
                    + ("s" if days_quiet != 1 else "")
                    + f" ({n_block}/{n_total} would-blocks · "
                    + f"{round(block_rate * 100, 1)}%) — ready to enforce?"
                ),
            })
        except Exception:  # noqa: BLE001
            # Per-flag failure must not block the others.
            continue

    return suggestions


@router.post("/policy/promote")
async def promote_flag(request: Request):
    """Set a single flag's override.

    Body: {"flag": "enforce_adversarial", "value": true, "note": "..."}

    Effect: writes the override to Mongo. The next IP-contract
    evaluation in any worker (<=30s lag from the per-process cache
    TTL) starts honouring the new value. No restart needed.
    """
    user = await _require_owner(request)
    body = await request.json()
    flag = (body.get("flag") or "").strip()
    value = bool(body.get("value"))
    note = (body.get("note") or "").strip()
    if flag not in VALID_FLAGS:
        raise HTTPException(
            status_code=400,
            detail=f"flag must be one of {sorted(VALID_FLAGS)}",
        )
    entry = await set_override(
        _db, flag=flag, value=value,
        actor=user.get("email") or user.get("id") or "unknown",
        note=note,
    )
    # Also force a cache refresh so the UI sees the change immediately
    # rather than waiting up to 30s for the next decision-time refresh.
    try:
        from services.guard_policy_store import load_overrides_into_cache
        await load_overrides_into_cache()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "applied": entry}


@router.post("/policy/clear")
async def clear_flag(request: Request):
    """Remove a flag's override so the env-default takes over again."""
    user = await _require_owner(request)
    body = await request.json()
    flag = (body.get("flag") or "").strip()
    if flag not in VALID_FLAGS:
        raise HTTPException(
            status_code=400,
            detail=f"flag must be one of {sorted(VALID_FLAGS)}",
        )
    await clear_override(
        _db, flag=flag,
        actor=user.get("email") or user.get("id") or "unknown",
    )
    try:
        from services.guard_policy_store import load_overrides_into_cache
        await load_overrides_into_cache()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "cleared": flag}
